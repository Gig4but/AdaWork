"""The app's long-lived parts, made and connected in one place (ARCHITECTURE.md).

`build` makes them for one data folder; `start` and `stop` run their background work. The web
interface (web/app.py) and the tray (cli.py) use what is here; nothing here knows about them.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from adawork import paths
from adawork.ai.services import AiServices, make_ai_services
from adawork.config import Settings
from adawork.db.session import db_file, make_engine, make_sessionmaker, migrate
from adawork.desktop import autostart
from adawork.employers import downloads
from adawork.geo import ruian
from adawork.geo.home import Locator
from adawork.geo.places import SharedPlaces
from adawork.net.http import Refresh, make_client
from adawork.routing.commute import CommuteService
from adawork.routing.data import HOME_KM, Area, RoutingData
from adawork.routing.motis import MotisServer
from adawork.scheduler import Scheduler
from adawork.scraper.runner import RunHooks, ScrapeRunner
from adawork.scraper.sources import MANUAL
from adawork.search.travel import TravelFilter, kept_starts
from adawork.secrets import Secrets, default_secrets
from adawork.social.logins import SocialLogins
from adawork.system.resources import Priority

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    sessions: sessionmaker[Session]
    secrets: Secrets
    data_dir: Path
    cache_dir: Path
    places: SharedPlaces
    ai: AiServices
    runner: ScrapeRunner
    scheduler: Scheduler
    motis: MotisServer
    routing: RoutingData
    commute: CommuteService
    travel: TravelFilter
    logins: SocialLogins
    autostart_sync: Callable[[bool], object]

    def set_automation(self, on: bool | None = None) -> None:
        """Automatic checks changed (or `on`: switch them): rescheduled at once."""
        if on is None:
            self.scheduler.wake()
        else:
            self.scheduler.set_enabled(on)
        auto = self.settings.automation
        self.autostart_sync(auto.enabled and auto.start_with_windows)

    def start(self) -> None:
        if self.settings.wizard_completed:
            try:  # resume an interrupted download, follow hardware changes (Auto)
                self.ai.manager.start_download()
                if self.ai.manager.ready:
                    self.ai.enricher.start()  # finish what the last session left
            except Exception:
                log.exception("AI start-up failed")
            self.set_automation()  # the Run value follows the app if it moved
        self.scheduler.start()
        self.routing.start()

    def stop(self) -> None:
        self.scheduler.stop()  # first: it could start a new search meanwhile
        self.runner.stop()
        self.routing.stop()
        self.motis.close()
        self.ai.shutdown()


def build(
    settings: Settings | None = None,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    secrets: Secrets | None = None,
    runner_factory=None,
    ai_factory=None,
    autostart_sync: Callable[[bool], object] | None = None,  # the Run value added or removed
    refreshes: tuple[Refresh, ...] | None = None,
    routing_updates: bool = True,  # False in tests: no map data downloads
) -> Services:
    settings = settings or Settings.load()
    engine = make_engine(db_path or db_file())
    migrate(engine)
    sessions = make_sessionmaker(engine)
    data_dir = data_dir or paths.data_dir()
    cache_dir = cache_dir or data_dir / "cache"
    keys = secrets or default_secrets()
    ai: AiServices = (ai_factory or make_ai_services)(settings, sessions, cache_dir)
    resources = ai.manager.resources
    places = SharedPlaces(sessions)
    locator = Locator(places, cache_dir / "ruian" / ruian.FILE_NAME)

    # --- Commute (PLAN §4.6) ---
    vendor = paths.vendor_dir()
    motis = MotisServer(vendor, paths.log_dir() / "motis.log")

    def wanted_area() -> Area | None:
        choice = settings.routing_area
        if choice == "auto":
            choice = "whole" if resources.limits.routing_radius_km is None else "home"
        if choice == "whole":
            return Area()
        home = locator.locate(settings.profile.home_address)
        return Area(HOME_KM, home.lat, home.lon) if home else None

    def free_ram_for_import() -> None:
        if resources.limits.parallel_heavy == 1:  # 8 GB: the model and the import never overlap
            ai.manager.server.stop()

    routing = RoutingData(
        data_dir / "routing",
        vendor,
        motis,
        wanted_area,
        make_client,
        allowed=lambda: routing_updates and settings.routing_download,
        heavy=lambda: resources.heavy(Priority.IMPORT),
        busy=resources.busy,
        before_import=free_ram_for_import,
    )
    if routing.version and not settings.routing_download:  # imported before it was a choice
        settings.routing_download = True
        settings.save()

    # `travel` is made below: `keep` is first called by a later computation.
    commute = CommuteService(
        sessions, routing, motis, keep=lambda s: kept_starts(s, travel.home_start())
    )
    travel = TravelFilter(lambda: settings.profile, locator, routing, commute)
    routing.on_ready = travel.routing_ready

    # --- Searching ---
    hooks = RunHooks(before_clear=ai.enricher.stop, after_run=ai.enricher.start)
    runner = (runner_factory or ScrapeRunner)(
        sessions,
        cache_dir,
        profile=lambda: settings.profile,
        source_choices=lambda: settings.sources,
        hooks=hooks,
        social_settings=lambda: settings.social,
        resources=resources,
        kept_sources=(MANUAL,),
        api_key=keys.get,
    )
    if refreshes is None:
        refreshes = downloads.refreshes(cache_dir / "employers")
    scheduler = Scheduler(settings, runner, sessions, refreshes, prepare=travel.prepare)

    return Services(
        settings=settings,
        sessions=sessions,
        secrets=keys,
        data_dir=data_dir,
        cache_dir=cache_dir,
        places=places,
        ai=ai,
        runner=runner,
        scheduler=scheduler,
        motis=motis,
        routing=routing,
        commute=commute,
        travel=travel,
        logins=SocialLogins(sessions, data_dir),
        autostart_sync=autostart_sync or autostart.sync,
    )
