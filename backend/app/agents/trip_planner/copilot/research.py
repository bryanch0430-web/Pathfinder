"""Community-first research: traveler posts (Reddit, Quora, TripAdvisor forums) become ranked candidates."""

import asyncio
import json
import logging
import re
from typing import Any

from app.agents.trip_planner.copilot import engine
from app.agents.trip_planner.guides import guide_leaning
from app.agents.trip_planner.planning.tools.web_search import search_web
from app.services.llm.service import llm_service, model_for

logger = logging.getLogger(__name__)

COMMUNITY_DOMAINS = ["reddit.com", "quora.com", "tripadvisor.com"]
TAGS = ("temple", "shrine", "museum", "art", "history", "nature", "hiking", "beach", "park", "viewpoint",
        "street food", "fine dining", "cafe", "nightlife", "bar", "shopping", "market", "crowded", "quiet",
        "hidden gem", "family", "romantic", "adventure", "free", "rainy day", "local", "touristy", "seafood",
        "vegetarian", "photography", "day trip", "onsen", "wellness")

EXTRACT_INSTRUCTION = f"""You turn traveler forum posts into trip candidates for Pathfinder.
Return JSON only:
{{"bases":[{{"name":"Tokyo","days":3}}],
 "rates":{{"transit":0,"taxi":0,"drive":0,"stay_per_night":0,"food_per_day":0}},
 "candidates":[{{"name":"...","base":"city it belongs to","area":"neighborhood","category":"attraction|food|experience|nightlife|shopping|nature",
   "tags":["..."],"est_cost":0,"duration_hours":2,"why":"what travelers actually say about it","source":1}}]}}
Rules:
- Prefer places real travelers recommend or warn about in EVIDENCE; "source" is the evidence number, or null if from general knowledge.
- "why" paraphrases the traveler insight (timing, crowd tips, what is overrated). Never invent quotes.
- est_cost: typical per-person spend in CURRENCY (0 if free). rates: typical per-person cost of one cross-town hop per mode, one hotel room night for two at the traveler's COMFORT level, one day of food per person, all in CURRENCY. Use null when unsure.
- tags: choose from {", ".join(TAGS)}.
- Skip anything matching the traveler's AVOID list. Up to 12 candidates, spread across bases. Keep "why" to one sentence.
- "bases" only when asked: overnight cities in travel order whose days sum to the trip length; include MUST places' cities.
"""


def _json(text: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", text or "")
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def community_search(query: str) -> list[dict[str, Any]]:
    results = await search_web(query, max_results=5, include_domains=COMMUNITY_DOMAINS)
    # search_web may pad with a generic summary; keep only real traveler posts.
    return [r for r in results if any(domain in (r.get("url") or "") for domain in COMMUNITY_DOMAINS)]


def initial_queries(copilot: dict) -> list[str]:
    from app.agents.trip_planner.guides import search_hint

    profile, destination = copilot["profile"], copilot["destination"]
    likes = " ".join((profile["soft"]["likes"] + profile["vibe"])[:3])
    leaning = search_hint(profile.get("guide"))
    return [f"{destination} {len(copilot['days'])} day itinerary advice",
            f"{destination} {leaning or 'hidden gems'} {likes}".strip(),
            f"{destination} budget tips transport food cost"]


async def research(copilot: dict, queries: list[str], *, plan_bases: bool = False) -> int:
    """Search traveler communities, extract candidates, merge into the pool. Returns items added."""
    queries = [q for q in dict.fromkeys(queries) if q not in copilot["researched"]][:3]
    if not queries and not plan_bases:
        return 0
    copilot["researched"] += queries
    batches = await asyncio.gather(*(community_search(q) for q in queries), return_exceptions=True)
    evidence = [r for batch in batches if isinstance(batch, list) for r in batch][:12]
    profile = copilot["profile"]
    prompt = "\n".join([
        f"DESTINATION: {copilot['destination']} ({len(copilot['days'])} days)",
        f"CURRENCY: {copilot['currency']}",
        f"COMFORT: {profile.get('comfort', 'mid_range')} (budget = hostels, street food; comfortable = nicer hotels, sit-down meals)",
        f"CURRENT BASES: {[day['base'] for day in copilot['days']]}",
        f"PLAN BASES: {'yes' if plan_bases else 'no'}",
        f"MUST: {profile['hard']['must']}  AVOID: {profile['hard']['avoid']}",
        f"LIKES: {profile['soft']['likes']}  DISLIKES: {profile['soft']['dislikes']}  VIBE: {profile['vibe']}",
        guide_leaning({"guide": profile.get("guide")}).strip(),
        f"FOCUS: {queries}",
        "EVIDENCE:",
        *(f"[{i}] {r.get('title', '')} ({r.get('url', '')}): {str(r.get('content', ''))[:600]}"
          for i, r in enumerate(evidence, 1)),
    ])
    parsed: dict = {}
    # The fast model occasionally returns truncated JSON, sometimes twice running, which left the first
    # build-with-agent turn with nothing to suggest. So the one retry goes to the main model.
    for attempt in range(2):
        try:
            raw = await llm_service.generate(prompt=prompt, system_instruction=EXTRACT_INSTRUCTION,
                                             temperature=0.2, **(model_for("fast") if attempt == 0 else {}))
        except Exception as exc:
            logger.warning("Copilot research extraction failed: %s", exc)
            return 0
        parsed = _json(raw)
        if parsed.get("candidates"):
            break
        logger.warning("Copilot research extraction returned no candidates (attempt %d, %d chars)",
                       attempt + 1, len(raw or ""))

    items = []
    for raw in parsed.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        source = raw.get("source")
        hit = evidence[source - 1] if type(source) is int and 1 <= source <= len(evidence) else None
        item = engine.make_item({**raw, "source": hit.get("url") if hit else None},
                                basis="community" if hit else "model_estimate")
        if item and not engine.hard_conflicts(profile, item):
            items.append(item)
    added = engine.merge_pool(copilot, items)

    rates = parsed.get("rates") if isinstance(parsed.get("rates"), dict) else {}
    for mode in ("transit", "taxi", "drive"):
        if copilot["rates"]["transfer"][mode] is None:
            copilot["rates"]["transfer"][mode] = engine.number(rates.get(mode))
    for name in ("stay_per_night", "food_per_day"):
        if copilot["rates"][name] is None:
            copilot["rates"][name] = engine.number(rates.get(name))

    if plan_bases:
        _assign_bases(copilot, parsed.get("bases"))
    return added


def _assign_bases(copilot: dict, bases: Any) -> None:
    day = 0
    for base in bases if isinstance(bases, list) else []:
        name = engine.clean(base.get("name")) if isinstance(base, dict) else ""
        count = base.get("days") if isinstance(base, dict) else None
        if not name or type(count) is not int or count < 1:
            continue
        for _ in range(count):
            if day < len(copilot["days"]):
                copilot["days"][day]["base"] = name
                day += 1
    # Leftover days stay with the last base rather than falling back to the country name.
    for rest in copilot["days"][day:] if day else []:
        rest["base"] = copilot["days"][day - 1]["base"]
