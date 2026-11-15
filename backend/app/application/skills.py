"""One skill per writer job. The gateway still asks for a prompt name.

``modify`` maps to ``edit-itinerary``, and the same for every other prompt name.
The skill body is the instruction. Notes on how the Python agents search are not
skills; they are in docs/search-skill-notes. The folder is read once per process.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


@cache
def _load() -> tuple[Skill, ...]:
    """Every skill file, read once per process."""
    found: list[Skill] = []
    if not _ROOT.is_dir():
        return ()
    for path in sorted(_ROOT.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _frontmatter(text)
        name = meta.get("name", "")
        if name:
            found.append(Skill(name, meta.get("description", ""), body))
    return tuple(found)


def load_skills() -> list[Skill]:
    return list(_load())


#: Prompt name the gateway loads, and the one skill whose body is that prompt.
_PROMPT_SKILLS = {
    "modify": "edit-itinerary",
    "clarify": "clarify-trip",
    "planner": "compose-itinerary",
    "ask": "answer-question",
    "router": "route-message",
    "understand": "read-trip-request",
    "intake": "read-shared-material",
    "research": "judge-stay",
    "audit": "audit-candidates",
    "repair": "repair-plan",
    "translate": "translate-plan",
}


def skill_for(prompt: str) -> Skill | None:
    """The skill for a prompt name."""
    name = _PROMPT_SKILLS.get(prompt)
    if not name:
        return None
    for skill in load_skills():
        if skill.name == name:
            return skill
    return None


def skill_for_modify() -> Skill | None:
    """The edit skill. The router has already called this turn a change."""
    return skill_for("modify")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip()
