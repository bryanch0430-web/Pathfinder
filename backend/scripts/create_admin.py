"""Create or update a local administrator account.

Run from the backend folder:

    .venv\\Scripts\\python.exe scripts\\create_admin.py --email you@example.com --name "You" --password "..."

With no --password a strong one is generated and printed once.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import close_db, init_db  # noqa: E402
from app.services import local_auth  # noqa: E402

ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a Pathfinder administrator.")
    parser.add_argument("--email", required=True, help="account email (sign-in name)")
    parser.add_argument("--password", help="password; generated when omitted")
    parser.add_argument("--name", default=None, help="display name")
    args = parser.parse_args()

    password = args.password
    generated = password is None
    if generated:
        password = generate_password()

    await init_db()
    existing = await local_auth.get_user_by_email(args.email)
    if existing is None:
        user = await local_auth.create_user(args.email, password, args.name, is_admin=True)
        action = "created"
    else:
        user = await local_auth.update_account(
            existing.id,
            password=password if not generated else None,
            full_name=args.name if args.name is not None else existing.full_name,
            is_admin=True,
        )
        action = "updated"
    await close_db()

    print(f"{action} administrator: {user.email}  id={user.id}")
    if generated:
        print(f"password: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
