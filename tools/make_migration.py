"""Dev helper: autogenerate a migration from models.py.

    uv run python tools/make_migration.py "add employers"

Applies existing migrations to a temporary database, then diffs it against the models.
"""

import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

from adawork.db.session import MIGRATIONS_DIR, make_engine, migrate


def main() -> None:
    message = sys.argv[1] if len(sys.argv) > 1 else "update"
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(Path(tmp) / "dev.db")
        if any((MIGRATIONS_DIR / "versions").glob("*.py")):
            migrate(engine)
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.revision(cfg, message=message, autogenerate=True)
        engine.dispose()


if __name__ == "__main__":
    main()
