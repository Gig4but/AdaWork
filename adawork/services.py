"""The app's long-lived parts, made and connected in one place.

`build` makes them for one data folder. The web interface (web/app.py) and the tray
(cli.py) use what is here; nothing here knows about them.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from adawork import paths
from adawork.ai.services import AiServices, make_ai_services
from adawork.config import Settings
from adawork.db.session import db_file, make_engine, make_sessionmaker, migrate
from adawork.geo.places import SharedPlaces
from adawork.scraper.runner import RunHooks, ScrapeRunner
from adawork.scraper.sources import MANUAL
from adawork.social.logins import SocialLogins

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    sessions: sessionmaker[Session]
    data_dir: Path
    cache_dir: Path
    places: SharedPlaces
    ai: AiServices
    runner: ScrapeRunner
    logins: SocialLogins

    def start(self) -> None:
        if self.settings.wizard_completed:
            try:  # resume an interrupted download, follow hardware changes (Auto)
                self.ai.manager.start_download()
                if self.ai.manager.ready:
                    self.ai.enricher.start()  # finish what the last session left
            except Exception:
                log.exception("AI start-up failed")

    def stop(self) -> None:
        self.runner.stop()
        self.ai.shutdown()


def build(
    settings: Settings | None = None,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    runner_factory=None,
    ai_factory=None,
) -> Services:
    settings = settings or Settings.load()
    engine = make_engine(db_path or db_file())
    migrate(engine)
    sessions = make_sessionmaker(engine)
    data_dir = data_dir or paths.data_dir()
    cache_dir = cache_dir or data_dir / "cache"
    ai: AiServices = (ai_factory or make_ai_services)(settings, sessions, cache_dir)
    runner = (runner_factory or ScrapeRunner)(
        sessions,
        cache_dir,
        profile=lambda: settings.profile,
        source_choices=lambda: settings.sources,
        hooks=RunHooks(before_clear=ai.enricher.stop, after_run=ai.enricher.start),
        social_settings=lambda: settings.social,
        resources=ai.manager.resources,
        kept_sources=(MANUAL,),
    )
    return Services(
        settings=settings,
        sessions=sessions,
        data_dir=data_dir,
        cache_dir=cache_dir,
        places=SharedPlaces(sessions),
        ai=ai,
        runner=runner,
        logins=SocialLogins(sessions, data_dir),
    )
