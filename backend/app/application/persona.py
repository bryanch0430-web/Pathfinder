"""Identity and small-talk answers.

Some questions are not travel questions and are not worth a model call: "who
are you?", "are you a real person?". They are static facts about the assistant,
so they are answered here, deterministically and instantly. A model paraphrase
adds latency and risk (it could claim to be human, or invent a maker), and a
web search would be absurd.
"""

from __future__ import annotations

import re

PERSONA = "Pathfinder"
KIND = "an AI trip planner"

#: Only complete, unambiguous meta-questions match. A travel request that
#: happens to contain "who" or "real" must not be swallowed by this path.
IDENTITY = re.compile(
    r"(\bwho (are|r) (you|u)\b"
    r"|\bwhat are you\b"
    r"|\bwhat('s| is) your name\b"
    r"|\bare you (a |an )?(real|human|person|bot|ai|robot|machine)\b"
    r"|\bis (this|it) (a |an )?(real|human) (person|being)\b"
    r"|\bwho am i (talking|speaking) (to|with)\b"
    r"|\bare you (chatgpt|gpt|claude|gemini|grok|deepseek)\b"
    r"|\bwhat (can|do) you do\b"
    r"|\bhow do you work\b"
    r"|\b(what|which) model\b)",
    re.IGNORECASE,
)

CAPABILITIES = (
    "I plan trips from live sources. Tell me a destination, your dates, how many "
    "travellers and a budget, and I gather attractions, hotels, weather and tickets "
    "in parallel, then build a day-by-day itinerary. You can ask me to change one "
    "thing without rebuilding the rest, or ask a question about the plan. Every stop "
    "shows its source, and when a source returns nothing I say so instead of guessing."
)

LIMITS = (
    "I cannot book, take payment, or confirm availability, and I will not invent a "
    "price. I keep a trip only if you save it."
)


def identity_reply(message: str) -> str | None:
    """A short, honest answer for a meta-question, or None if it is not one."""
    if not message or not IDENTITY.search(message):
        return None
    lower = message.lower()

    if re.search(r"\b(who are you|who r u|what are you|your name|who am i (talking|speaking))", lower):
        return (
            f"I'm {PERSONA}, {KIND} — software, not a person. {CAPABILITIES}"
        )
    if re.search(r"\b(real|human|person|bot|robot|machine)\b", lower):
        return (
            f"No — I'm {KIND}, not a real person. I'm software built for this course "
            f"project. {CAPABILITIES}"
        )
    if re.search(r"\b(chatgpt|gpt|claude|gemini|grok|deepseek|model)\b", lower):
        return (
            f"I'm {PERSONA}, {KIND}. I run on a language model chosen by whoever operates "
            f"this deployment, and I only use it to phrase answers from the travel data "
            f"I fetch — the routing, validation and source checks are ordinary code."
        )
    if re.search(r"\b(what (can|do) you do|how do you work)\b", lower):
        return f"{CAPABILITIES} {LIMITS}"
    return f"I'm {PERSONA}, {KIND}. {CAPABILITIES}"
