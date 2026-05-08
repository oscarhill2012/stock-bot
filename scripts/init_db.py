"""Initialise the StockBot DB schema. Idempotent.

Usage:
    PYTHONPATH=src python -m scripts.init_db
    PYTHONPATH=src python -m scripts.init_db --db-url sqlite:///path/to.db
"""
from __future__ import annotations

import argparse
import os

from orchestrator.persistence import create_all, make_engine


def init_db(db_url: str) -> None:
    """Create all StockBot tables on the given DB URL. Idempotent."""
    engine = make_engine(db_url)
    create_all(engine)


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None, help="SQLAlchemy URL")
    args = parser.parse_args()
    db_url = args.db_url or _resolve_default_db_url()
    init_db(db_url)
    print(f"Created all tables on {db_url}")


if __name__ == "__main__":
    main()
