"""The app's long-lived parts, made and connected in one place.

`build` makes them for one data folder. The web interface (web/app.py) and the tray
(cli.py) use what is here; nothing here knows about them.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from adawork import paths
from adawork.config import Settings
from adawork.db.session import db_file, make_engine, make_sessionmaker, migrate
from adawork.scraper.runner import ScrapeRunner


@dataclass
class Services:
    settings: Settings
    sessions: sessionmaker[Session]
    cache_dir: Path
    runner: ScrapeRunner

    def stop(self) -> None:
        self.runner.stop()


def build(
    settings: Settings | None = None,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
    runner_factory=None,
) -> Services:
    settings = settings or Settings.load()
    engine = make_engine(db_path or db_file())
    migrate(engine)
    sessions = make_sessionmaker(engine)
    cache_dir = cache_dir or paths.data_dir() / "cache"
    runner = (runner_factory or ScrapeRunner)(sessions, cache_dir, profile=lambda: settings.profile)
    return Services(settings=settings, sessions=sessions, cache_dir=cache_dir, runner=runner)
