"""One skill per writer job, plus warn-edit, which the add graph reads by name.

``modify`` maps to ``edit-itinerary``, and the same for every other prompt name.
The skill body is the instruction. Notes on how the Python agents search are not
skills; they are in docs/search-skill-notes. The folder is read once per process.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parent.parent / "skills"
_FRONTMATTER = re.compile(r"\A\ufeff?---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


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
    "rewrite": "rewrite-day",
    "ask": "answer-question",
    "router": "route-message",
    "understand": "read-trip-request",
    "intake": "read-shared-material",
    "research": "judge-stay",
    "audit": "audit-candidates",
    "repair": "repair-plan",
    "review": "review-failures",
    "translate": "translate-plan",
    "reply": "reply-after-edit",
    "turn-review": "turn-review",
}


def prompt_file(prompt: str) -> str:
    """The built-in skill file a prompt name stands for, or "" when the text is not a file."""
    folder = _PROMPT_SKILLS.get(prompt)
    if not folder:
        return ""
    return f"backend/app/skills/{folder}/SKILL.md"


def prompt_entries() -> list[tuple[str, str, str]]:
    """Each writer prompt: the name the gateway uses, a one-line description, the file body."""
    rows: list[tuple[str, str, str]] = []
    for prompt in _PROMPT_SKILLS:
        skill = skill_for(prompt)
        rows.append((prompt, skill.description if skill else "", skill.body if skill else ""))
    return rows


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


def skill_by_name(name: str) -> Skill | None:
    """A skill the add graph reads by the name in its file, not a writer prompt."""
    for skill in load_skills():
        if skill.name == name:
            return skill
    return None


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, match.group(2).strip()
