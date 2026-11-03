"""`.env.example` documents every setting and agrees with the application's defaults.

`scripts/make_env_example.py` keeps it that way: run it after adding or changing a setting.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"


def _generator():
    spec = importlib.util.spec_from_file_location("make_env_example", ROOT / "scripts" / "make_env_example.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_setting_is_in_the_example_and_nothing_else_is() -> None:
    skipped = _generator().SKIP
    settings = {name.upper() for name in Settings.model_fields} - skipped
    # A setting may be a live line or a commented-out default.
    mentioned = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", EXAMPLE.read_text(encoding="utf-8"), flags=re.M))
    assert not settings - mentioned, f"missing from .env.example: {sorted(settings - mentioned)}"
    assert not mentioned - settings, f"in .env.example but not a setting: {sorted(mentioned - settings)}"


def test_example_values_are_the_defaults_except_the_documented_ones(tmp_path: Path) -> None:
    generator = _generator()
    # Uncomment every commented-out default, then load the whole file as configuration.
    text = re.sub(r"^#\s*([A-Z][A-Z0-9_]*=)", r"\1", EXAMPLE.read_text(encoding="utf-8"), flags=re.M)
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    loaded, default = Settings(_env_file=env), Settings(_env_file=None)

    documented = generator.KEEP | set(generator.FIXED) | {"ADMIN_TOKEN", "AUTH_SECRET"}
    differing = {
        name.upper()
        for name in Settings.model_fields
        if getattr(loaded, name) != getattr(default, name)
    }
    assert differing <= documented, f"values in .env.example that are not the default: {sorted(differing - documented)}"
