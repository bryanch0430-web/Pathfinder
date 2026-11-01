"""Generate a placeholder-only .env.example from .env / .env.example.

Never prints secret values. Keeps comments, key order and every non-secret
setting so the repo stays runnable after `cp .env.example .env`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

SECRET_NAME = re.compile(r"(API_KEY|SECRET|PASSWORD|_TOKEN$|^ADMIN_TOKEN$|^KEY$)", re.I)
#: non-secret settings whose names merely look secret-like (numbers / test strings)
NOT_SECRET = re.compile(r"(MAX_TOKENS|CANARY)", re.I)
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,})")
KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

PLACEHOLDER = "REPLACE_ME"


def parse(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def is_secret(name: str, value: str) -> bool:
    if NOT_SECRET.search(name):
        return False
    return bool(SECRET_NAME.search(name)) or bool(SECRET_VALUE.search(value))


def key_of(line: str) -> str | None:
    m = KV.match(line.strip())
    return m.group(1) if m else None


def main() -> None:
    base = parse(EXAMPLE) or parse(ENV)
    seen: set[str] = set()
    out: list[str] = []
    masked: list[str] = []
    kept_secret_named = 0

    for line in base:
        stripped = line.strip()
        m = KV.match(stripped)
        if not m:
            out.append(line)
            continue
        name, value = m.group(1), m.group(2).strip()
        seen.add(name)
        secret = is_secret(name, value)
        if secret and value:
            if not value.upper().startswith(("REPLACE", "YOUR_", "<")):
                masked.append(name)
            value = PLACEHOLDER
            kept_secret_named += 1
        out.append(f"{name}={value}")

    # append any key that exists in .env but not in the template, so nothing is missing
    added: list[str] = []
    for line in parse(ENV):
        name = key_of(line)
        if not name or name in seen:
            continue
        seen.add(name)
        m = KV.match(line.strip())
        value = (m.group(2).strip() if m else "")
        if is_secret(name, value):
            value = PLACEHOLDER
            masked.append(name)
        out.append(f"{name}={value}")
        added.append(name)

    EXAMPLE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    print(f"wrote {EXAMPLE.name}: {len(seen)} keys")
    print(f"  placeholders  : {kept_secret_named + len(added)}")
    print(f"  masked keys   : {', '.join(sorted(set(masked))) or '(none)'}")
    print(f"  appended from .env: {', '.join(added) or '(none)'}")


if __name__ == "__main__":
    main()
