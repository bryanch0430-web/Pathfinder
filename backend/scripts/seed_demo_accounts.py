"""Create the three local demo accounts in the active SQLite database.

Run from Pathfinder_v1/backend with:
    PYTHONPATH=. python scripts/seed_demo_accounts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.auth import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.domain.models import User  # noqa: E402
from app.infrastructure.store import SQLiteStore  # noqa: E402


def main() -> None:
    settings = get_settings()
    settings.ensure_directories()
    store = SQLiteStore(settings.database_path, share_ttl_days=settings.share_ttl_days)
    for index in (1, 2, 3):
        email = f"testaccount{index}@pathfinder.test"
        if store.get_user_by_email(email):
            print(f"exists  {email}")
            continue
        store.create_user(
            User(email=email, display_name=f"Test Account {index}"),
            hash_password("Pathfinder123!"),
        )
        print(f"created {email}")


if __name__ == "__main__":
    main()
