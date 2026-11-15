"""The skill files are read once per process, and only the files that steer a writer are in the skills folder."""

from pathlib import Path

from app.application import skills
from app.application.skills import _PROMPT_SKILLS, load_skills, skill_for

NOT_A_PATH = {
    "attraction-search",
    "hotel-search",
    "flight-search",
    "weather-forecast",
    "web-search",
}


def test_every_skill_in_the_folder_is_a_prompt_skill() -> None:
    names = {skill.name for skill in load_skills()}

    assert names == set(_PROMPT_SKILLS.values())
    assert not names & NOT_A_PATH


def test_the_files_are_read_once_per_process(monkeypatch) -> None:
    skills._load.cache_clear()
    reads: list[Path] = []
    real = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    try:
        first = skill_for("modify")
        after_first = len(reads)
        for prompt in _PROMPT_SKILLS:
            assert skill_for(prompt) is not None
        load_skills()
        assert after_first == len(_PROMPT_SKILLS.values()), reads
        assert len(reads) == after_first, "a later call read the folder again"
        assert first is not None and first.name == "edit-itinerary"
    finally:
        skills._load.cache_clear()
