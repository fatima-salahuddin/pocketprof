"""Schema migrations (Alembic). The API applies pending migrations on start-up; in Compose the same
command runs once before the API starts:  python -m pocketprof.db.migrate"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ..config import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"


def config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_url.replace("%", "%%"))
    return cfg


def upgrade(revision: str = "head") -> None:
    command.upgrade(config(), revision)


def revision(message: str) -> None:
    """Developer helper: autogenerate a migration from the models."""
    command.revision(config(), message=message, autogenerate=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2 and sys.argv[1] == "revision":
        revision(" ".join(sys.argv[2:]))
    else:
        upgrade()
