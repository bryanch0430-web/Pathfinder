"""Skill folders. A path skill is loaded after the router has already chosen that path.

The description says when the skill applies. The body is the procedure the writer
reads, on top of the prompt in ``app/prompts``. Routing stays where it is.
Agent skills in this folder record how a search works. They are not a fifth path.
"""

from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def load_skills() -> list[Skill]:
    found: list[Skill] = []
    if not _ROOT.is_dir():
        return found
    for path in sorted(_ROOT.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _frontmatter(text)
        name = meta.get("name", "")
        if name:
            found.append(Skill(name, meta.get("description", ""), body))
    return found


#: The prompt name the gateway loads, and the one skill appended to it.
_PATH_SKILLS = {
    "modify": "edit-itinerary",
    "clarify": "clarify-trip",
    "planner": "compose-itinerary",
    "ask": "answer-question",
}


def skill_for(prompt: str) -> Skill | None:
    """The skill for a path the router already chose. Other skills stay on disk."""
    name = _PATH_SKILLS.get(prompt)
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
