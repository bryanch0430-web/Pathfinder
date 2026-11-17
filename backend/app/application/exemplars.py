"""Teach the router with examples, instead of another regex.

Case-based reasoning keeps a past situation and what should happen next to it
(Aamodt & Plaza, 1994). Few-shot prompting does the same inside a model prompt
(Brown et al., 2020). Retell calls the pair a finetune example: a short
transcript, and the transition you wanted.

Here an example is one traveller line and the path it should take
(plan, modify, ask or unclear). A close example is decided before Jev and
before the writer, so a correction does not have to outrun a new pattern.
A looser example is only shown to the writer as a hint. Display-language and
refresh rules still win: those are product rules, not taste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import Intent, RouteDecision

_TOKEN = re.compile(r"[a-z0-9']+")
_STOP = frozenset({"a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "my", "me", "please"})
_INTENTS = {item.value for item in Intent if item.value in {"plan", "modify", "ask", "unclear"}}


@dataclass(frozen=True)
class ExemplarHit:
    utterance: str
    intent: str
    score: float


def tokens(text: str) -> set[str]:
    """The words that carry a line, without the little ones every sentence shares."""
    return {word for word in _TOKEN.findall((text or "").lower()) if word not in _STOP and len(word) > 1}


def similarity(left: str, right: str) -> float:
    """How much of the wording two lines share, from 0 to 1."""
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def best_exemplar(message: str, examples: list[tuple[str, str]]) -> ExemplarHit | None:
    """The closest enabled example, when it is close enough to decide the path."""
    best: ExemplarHit | None = None
    for utterance, intent in examples:
        if intent not in _INTENTS:
            continue
        score = similarity(message, utterance)
        if best is None or score > best.score:
            best = ExemplarHit(utterance, intent, score)
    if best is None or best.score < 0.5:
        return None
    # One shared word is not a lesson. "you" must not route every sentence.
    if len(tokens(message) & tokens(best.utterance)) < 2 and best.score < 0.99:
        return None
    return best


def exemplar_decision(message: str, examples: list[tuple[str, str]]) -> RouteDecision | None:
    """The path a close example names, or None when nothing taught applies."""
    hit = best_exemplar(message, examples)
    if hit is None:
        return None
    intent = Intent(hit.intent)
    return RouteDecision(
        intent=intent,
        confidence=0.95,
        need_clarification=intent == Intent.UNCLEAR,
        reason_code="exemplar",
        rationale=f"training example {hit.score:.2f}: {hit.utterance}",
        related_to_plan=intent != Intent.ASK,
    )


def exemplar_block(examples: list[tuple[str, str]], limit: int = 8) -> str:
    """The hint the writer sees when no example was close enough to decide alone."""
    rows = [(utterance.strip(), intent) for utterance, intent in examples if utterance.strip() and intent in _INTENTS]
    if not rows:
        return ""
    lines = [f"- {utterance!r} -> {intent}" for utterance, intent in rows[:limit]]
    return "Training examples (when the message is the same kind of thing, follow these):\n" + "\n".join(lines) + "\n\n"
