"""Generate a placeholder-only .env.example from .env / .env.example.

Never prints secret values. Keeps comments, key order and every non-secret
setting so the repo stays runnable after `cp .env.example .env`.

It also reads `Settings` (backend/app/config.py), so the template cannot drift from the
application: a setting the template does not mention is appended as a commented-out line
with its default, and an active value that differs from the default is set back to it
(except the few values listed in `KEEP` and `FIXED`). Run it again after adding a setting.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

SECRET_NAME = re.compile(r"(API_KEY|SECRET|PASSWORD|_TOKEN$|^ADMIN_TOKEN$|^KEY$|^AMAP_KEY$)", re.I)
#: non-secret settings whose names merely look secret-like (numbers / test strings)
NOT_SECRET = re.compile(r"(MAX_TOKENS|CANARY)", re.I)
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,})")
KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

PLACEHOLDER = "REPLACE_ME"

#: Settings the template does not set; they are derived, not configured.
SKIP = {"BASE_DIR"}
#: Active values that are documentation, not a copy of the default (the default is empty or local).
KEEP = {"ENVIRONMENT", "XAI_BASE_URL", "XAI_MODEL", "DECISION_API_URL"}
#: A value the template sets because the default is empty: the endpoint of the default provider.
FIXED = {"LLM_BASE_URL": "https://api.deepseek.com"}
#: One-line explanations for settings the hand-written part of the template does not cover.
NOTES = {
    "APP_NAME": "Name the API reports at /api/runtime.",
    "DEBUG": "Not read by the application yet.",
    "DATA_DIR": "Where the database, traces and evaluation output live. Relative paths resolve against the repository root.",
    "TRACE_DIR": "One JSON trace per request, read through /api/admin/spans/{request_id}.",
    "EVAL_DIR": "Where evaluation runs write their output.",
    "FRONTEND_DIST": "The built frontend (npm run build) that the backend serves at /.",
    "CORS_ORIGINS": "Browser origins allowed to call the API, comma separated.",
    "AUTH_TOKEN_HOURS": "How long a sign-in lasts.",
    "USER_AGENT": "Sent to data sources that accept a generic client string.",
    "DEFAULT_LOCALE": "Language of the interface and of new plans.",
    "SUPPORTED_LOCALES": "Languages the API accepts, comma separated.",
    "LLM_JSON_MODE": "Ask the writer for JSON output; turn off for an endpoint that rejects response_format.",
    "DECISION_PROVIDER": "Not read by the application yet.",
    "DECISION_PATH": "Path added to DECISION_API_URL unless the URL already ends with it.",
    "GOOGLE_PLACES_PROXY": "Base URL of a Places API proxy. Empty means places.googleapis.com.",
    "ENABLE_HK_DISRUPTIONS": "Hong Kong trips check Observatory warnings and MTR line status.",
    "ROUTER_RULES_FIRST": "Let the rules decide an unambiguous message without a router or writer call.",
    "HTTP_TIMEOUT_SECONDS": "Timeout of one request to a data source.",
    "HTTP_RETRIES": "Retries of a request to the keyed data sources: flights, hotels, routes, search and Google geocoding.",
    "MAX_TOOL_CALLS_PER_TURN": "Lookups one turn path may make before the rest are refused with a data gap. 0 turns the cap off.",
    "MAX_DAYS": "The longest trip the planner accepts.",
    "MAX_ATTRACTIONS_PER_DAY": "The most attractions placed on one day.",
    "WEATHER_FRESH_SECONDS": "How long a forecast counts as current before it is fetched again (6 hours).",
    "TICKET_FRESH_SECONDS": "How long a fare counts as current (24 hours).",
    "HOTEL_FRESH_SECONDS": "How long a hotel price counts as current (7 days).",
    "ATTRACTION_FRESH_SECONDS": "How long attraction data counts as current (30 days).",
    "BLOCKLIST_ENABLED": "Screen traveller messages for prompt injection before any model sees them.",
    "FENCE_ENABLED": "Not read by the application yet.",
}

#: Keys the template must always carry: (name, value, anchor key, comment lines).
#: A missing key is inserted after its anchor (or at the end) with its comment.
#: Outside ENVIRONMENT=development/test the backend refuses to start with the
#: default AUTH_SECRET / ADMIN_TOKEN, so both must be documented here.
REQUIRED: list[tuple[str, str, str, list[str]]] = [
    ("ENVIRONMENT", "development", "", []),
    (
        "AUTH_SECRET",
        PLACEHOLDER,
        "ADMIN_TOKEN",
        [
            "# Signs the HS256 bearer tokens. Outside ENVIRONMENT=development/test the app",
            "# refuses to start unless AUTH_SECRET is a random value of at least 32 characters",
            "# and ADMIN_TOKEN is a real secret (not local-admin / REPLACE_ME).",
            '# Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"',
        ],
    ),
]


def load_settings_defaults() -> dict[str, str]:
    """Every setting as ENV_NAME -> its default, written the way a .env file would."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import Settings  # noqa: PLC0415 - needs the path set above

    def text(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, Path):
            try:
                return value.relative_to(ROOT).as_posix()
            except ValueError:
                return value.as_posix()
        return "" if value is None else str(value)

    return {name.upper(): text(field.default) for name, field in Settings.model_fields.items()}


def same_value(a: str, b: str) -> bool:
    if a.strip().lower() == b.strip().lower():
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


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
    defaults = load_settings_defaults()
    realigned: list[str] = []
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
        elif not secret and name in defaults and name not in KEEP:
            wanted = FIXED.get(name, defaults[name])
            if not same_value(value, wanted):
                realigned.append(name)
                value = wanted
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

    # insert any required key that neither file has, next to its anchor
    required: list[str] = []
    for name, value, anchor, comment in REQUIRED:
        if name in seen:
            continue
        seen.add(name)
        at = next((i + 1 for i, line in enumerate(out) if anchor and key_of(line) == anchor), len(out))
        out[at:at] = [*comment, f"{name}={value}"]
        required.append(name)

    # list every setting the template does not mention, commented out with its default
    mentioned = set(seen) | {m.group(1) for line in out if (m := re.match(r"^#\s*([A-Z][A-Z0-9_]*)=", line))}
    undocumented = [name for name in defaults if name not in mentioned and name not in SKIP]
    if undocumented:
        out += ["", "# --- other settings: the defaults, commented out ------------------------------", "# Uncomment a line to change that setting."]
        for name in undocumented:
            if name in NOTES:
                out.append(f"# {NOTES[name]}")
            out.append(f"# {name}={defaults[name]}")

    EXAMPLE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    print(f"wrote {EXAMPLE.name}: {len(seen)} keys")
    print(f"  placeholders  : {kept_secret_named + len(added)}")
    print(f"  masked keys   : {', '.join(sorted(set(masked))) or '(none)'}")
    print(f"  appended from .env: {', '.join(added) or '(none)'}")
    print(f"  required keys added: {', '.join(required) or '(none)'}")
    print(f"  set back to the default: {', '.join(realigned) or '(none)'}")
    print(f"  settings added (commented out): {', '.join(undocumented) or '(none)'}")


if __name__ == "__main__":
    main()
