"""SQLite engine, sessions and automatic migrations."""

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from adawork import paths

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def db_file() -> Path:
    return paths.data_dir() / "adawork.db"


def make_engine(path: Path | None = None) -> Engine:
    engine = create_engine(f"sqlite:///{path or db_file()}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def migrate(engine: Engine) -> None:
    """Bring the schema to the latest version. Runs on every app start; a database that
    needs changes is first copied to `<file>.bak` (one copy, replaced by the next upgrade)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current is not None and current != head:
        backup(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def backup(engine: Engine) -> Path | None:
    """A consistent copy of the database next to its file (SQLite's online backup)."""
    name = engine.url.database
    if not name or name == ":memory:":
        return None
    target = Path(name).with_name(Path(name).name + ".bak")
    source = engine.raw_connection()
    try:
        driver = source.driver_connection
        assert isinstance(driver, sqlite3.Connection)
        with closing(sqlite3.connect(target)) as copy:
            driver.backup(copy)
    finally:
        source.close()
    return target


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
