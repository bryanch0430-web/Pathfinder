import json
import logging
import re
from typing import Any

from app.agents.trip_planner.guides import guide_leaning
from app.agents.trip_planner.planning.graph import planning_graph
from app.agents.trip_planner.planning.state import create_initial_planning_state
from app.agents.trip_planner.state import TripPlanningState
from app.planning.upgrades import evidence_from_result, lock_lines
from app.routing.places import named_place_instruction
from app.services.llm.service import llm_service

logger = logging.getLogger(__name__)

_OFF_EARTH = re.compile(
    r"\b(?:mercury|venus|mars|jupiter|saturn|uranus|neptune|pluto|the moon|luna|"
    r"europa|titan|ganymede|callisto|enceladus|triton|ceres|the sun|outer space|deep space)\b",
    re.I,
)


def off_earth_places(*names: str | None) -> list[str]:
    """Place names in this brief that are not on Earth. A town that shares a planet's name matches too."""
    found: list[str] = []
    for name in names:
        text = (name or "").strip()
        if text and _OFF_EARTH.search(text) and text not in found:
            found.append(text)
    return found


def off_earth_note(*names: str | None) -> str:
    places = off_earth_places(*names)
    if not places:
        return ""
    shown = ", ".join(places)
    return (
        f"These places are not on Earth: {shown}.\n"
        "Say that in the opening, in plain words. Then still write the itinerary they asked for, as a proof of concept.\n"
        "Do not refuse. Do not invent live hotels, flights, or a real forecast for them. Say those details are imagined.\n"
    )


PLAN_GENERATION_SYSTEM_INSTRUCTION = """
You are Pathfinder, a knowledgeable and witty conversational AI travel companion.

The traveler has provided enough information to begin planning, and the planning
research phase has now completed.

Your job is to turn the structured planning research into a useful FIRST-DRAFT
trip plan for the traveler.

You are NOT doing new research. Use the provided planning data and research
results as your source material.

Create a day-by-day itinerary for the requested duration: one dated day, in visit order, the way a scheduled trip reads.

Guidelines:
- Use the destination, duration, origin, research findings, and candidates.
- Include the traveler's requested places when feasible; flag any that do not fit the stay.
- Follow the traveler's stated pace, interests, and things to avoid when selecting and sequencing activities.
- When GUIDE LEANING is present, choose and order stops to match it. The traveler's own brief still wins if they conflict.
- Use the rest of the brief in planning_preferences when present: travel dates (season, weekdays; never invent
  event schedules), adults/children (keep it family-friendly with children), comfort level (budget, mid_range,
  comfortable) for stays and dining, and travel_mode for how they get around.
- If a preference conflicts with a requested place or duration, explain the tradeoff rather than ignoring it.
- Prefer realistic sequencing and avoid unnecessarily complicated travel.
- Cover every requested day exactly once. Heading: "Day N — <overnight city>". Never a day range or a phase that merges days.
- Under each day, list that day only, in the order the traveler would go. Each stop is one bullet:
  `09:00–11:00 Name — why it is here`. These clock times are planning slots, not reservations.
- Name the overnight stay on that day. Include breakfast, lunch and dinner as their own stops when the day has room.
- Every stop must be a real venue a map can pin: a named temple, restaurant, hotel, station, or museum. Never a theme such as "Temples & traditional experiences" or "modern art afternoon".
- When the day travels to another city, name the train or flight and the hotel you stay at there. Do not write only "Return to Tokyo".
- Keep the day in one area. Put an intercity move on the travel day as its own stop, with how long it takes, instead of hiding it.
- Sequence each stop near the one before it, and leave time to arrive, leave, and get between them.
- If the research is thin, say what needs verification rather than filling gaps with confident specifics.
- Include a short "What to decide next" section with two concrete choices the traveler can change.
- Explain briefly why each major stop or area is included.
- Do not invent unsupported facts.
- Do not claim exact prices, availability, ticket times, or bookings. A clock time on a day is a planning slot, not a reservation.
- Do not claim a specific train time, fare, pass price, or pass coverage unless the supplied research supports it. Mark unverified transport details as things to check.
- Do not pretend anything has been booked.
- Treat the plan as a first draft that the traveler can refine.
- If the origin or destination is not on Earth, say that in the opening, then still write the itinerary as a proof of concept. Do not refuse. Do not present imagined hotels, flights, or weather as live data.
- Keep it useful and reasonably concise.
- Do not mention LangGraph, nodes, state, tools, subgraphs, APIs, orchestration,
  or internal implementation details.
- Do not repeat the entire research data back to the traveler.

Formatting requirements:
- Return valid Markdown only.
- Use Markdown headings for sections.
- Use Markdown bullet or numbered lists where useful.
- Use GitHub-Flavored Markdown tables when a table improves clarity.
- Do not use raw HTML tags.
- Do not use <br>, <div>, <table>, <p>, or similar HTML tags.
- Do not wrap the entire response in a code block.

Personality:
- clear and direct
- warm and genuinely helpful
- conversational rather than robotic
- do not force catchphrases or overdo the personality

End with a natural invitation to refine the plan.
"""


REVISION_SYSTEM_INSTRUCTION = """
You are Pathfinder, revising a traveler's itinerary while chatting with them.

- The chat shows only your opening. Start with two to four sentences that name exactly what changed and what stayed put, like a friend planning alongside them ("Done: Fushimi Inari moves to the morning of day 2, and Okochi-Sanso takes its afternoon slot. The other days are unchanged.").
- After that opening, give the complete updated itinerary in the same structure and formatting as the current one. The studio saves this list; do not skip a day or a stop that stayed.
- Keep every day as its own timed list (Day N, clock times, overnight stay). Do not collapse days into a range.
- Apply the request faithfully. Keep every other day, base and activity as it was unless the change needs it.
- When GUIDE LEANING is present, a new or moved stop should fit that leaning unless this message asks for something else.
- A new stop must be a real venue a map can pin. Never add a theme such as "Temples & traditional experiences".
- If they agree to something you proposed in YOUR LAST MESSAGE ("yes", "do it", "go ahead", "finalize it"),
  apply exactly that proposal.
- If the request doesn't work (not enough time, too far, it clashes with their preferences), say so plainly
  and make the closest sensible change instead.
- Every day stays in the same day-by-day format with its day number, so the change shows up in their Trip Studio.
- The update is saved to their Trip Studio as soon as you finish: don't ask them to confirm or finalize it.
- Never invent prices, schedules, availability or bookings; flag details to verify.
- If the origin or destination is not on Earth, say that before the revision, then still apply the request as a proof of concept. Do not refuse, and do not present imagined hotels, flights, or weather as live data.
- If BUDGET LEDGER is supplied, its projected total and target are authoritative. Do not add up prices
  from the prose or call entered planning costs "spent" money.
- Markdown only: no raw HTML, no code fences. Never mention internal systems.
"""

ANSWER_SYSTEM_INSTRUCTION = """
You are Pathfinder, chatting with a traveler about the itinerary you planned together.
Answer their message directly and naturally in 1-5 sentences, grounded in their itinerary where it helps.
Don't rewrite or repeat the itinerary. If they seem to want a change, offer one specific change as a yes/no
question ("Want me to move Okochi-Sanso to day 1?"): a "yes" applies it.
If JUST SAVED lists something, confirm it in a few words first.
If they say the plan is final, or ask to save it or update the studio: every change is saved to their Trip Studio
the moment it's made, so tell them it's already saved and they can open the studio to see it.
Never invent prices, schedules or bookings. If they ask about flights or tickets, point them at the Book flight links on the first and last day and say you do not book it for them. Markdown only, no headings. Never mention internal systems.
If STUDIO SCHEDULE is present, that list is the trip. A place on it is already included. Do not say it is missing, and do not offer to add it again. When a stop has a source or listed hours, use those.
If the traveler pinned a stop, answer about that stop.
If BUDGET LEDGER is supplied, use only its total and target when discussing the overall budget.
"""


def answer_outline(plan: str) -> str:
    """Stop names for a question. The full draft stays out of the prompt so it is not copied back."""
    lines = []
    for line in (plan or "").splitlines():
        stripped = line.strip().lstrip("-* ").strip()
        if not stripped:
            continue
        if re.match(r"Day \d+\b", stripped) or re.match(r"\d{1,2}:\d{2}", stripped):
            lines.append(stripped[:110])
        if len(lines) >= 36:
            break
    return "\n".join(lines) or (plan or "")[:700]


_DAY_HEADING = re.compile(r"^(#{1,3}\s*)?(\*\*)?Day \d+\b", re.I)


def revision_visible(text: str) -> tuple[str, bool]:
    """The opening the traveler should read, and whether the day-by-day list has started.

    The day list is still stored for the studio. It is not repeated in the chat.
    """
    kept: list[str] = []
    started = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _DAY_HEADING.match(stripped) or stripped.startswith("**Route:") or "First Draft" in stripped:
            started = True
            break
        kept.append(line)
    return "\n".join(kept).strip(), started


def clip_to_answer(text: str) -> str:
    """Drop a reprinted draft from a question reply. The first answer paragraph stays."""
    kept = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if re.match(r"(#{1,3}\s|\*\*Day \d+|Day \d+\b)", stripped):
            break
        if "First Draft" in stripped or stripped.startswith("**Route:"):
            break
        kept.append(line)
    result = "\n".join(kept).strip()
    return result or (text or "").strip()


def build_followup_prompt(
    previous_plan: str,
    message: str,
    destination: str | None,
    duration_days: int | None,
    origin: str | None,
    planning_preferences: dict[str, Any] | None,
    last_reply: str | None = None,
    saved: list[str] | None = None,
    budget_context: dict[str, Any] | None = None,
    scope: str | None = None,
    tool_notes: str | None = None,
    answering: bool = False,
) -> str:
    """Prompt for revising or discussing an existing itinerary without re-researching the trip.

    last_reply: your previous message when it wasn't the itinerary itself (an answer or an offer),
    so "yes" and "do that" have something to point at. saved: what this turn already stored.
    """
    return (
        f"TRIP: {duration_days} days in {destination}, travelling from {origin or 'not specified'}\n"
        f"PREFERENCES: {json.dumps(planning_preferences or {}, ensure_ascii=False)}\n"
        f"{guide_leaning(planning_preferences)}\n"
        f"CURRENT ITINERARY:\n{previous_plan[:16000]}\n\n"
        + (f"YOUR LAST MESSAGE:\n{last_reply[:3000]}\n\n" if last_reply else "")
        + (f"JUST SAVED: {'; '.join(saved)}\n\n" if saved else "")
        + (f"BUDGET LEDGER (authoritative trip total): {json.dumps(budget_context)}\n\n" if budget_context else "")
        + (f"{scope}" if scope else "")
        + (f"{tool_notes}\n\n" if tool_notes else "")
        + lock_lines((planning_preferences or {}).get("locks"))
        + off_earth_note(origin, destination)
        + f"TRAVELER'S MESSAGE:\n{message}"
    )


SEASON_TASK = """
Fit the plan to the season: the context's "season" block gives typical weather for the travel
month(s). Follow its how_to_use guidance when choosing and ordering places, and add a short
"Season notes" line with the packing tip. Say "typical for <month>", never "forecast".
"""

CONDITIONS_TASK = """
Plan around the context's "conditions" block (real data for the trip's dates):
- forecast: on a rainy or stormy day, put the indoor highlights there and move outdoor sights to a
  drier day; on a hot day, do outdoor sights early or late. Call it the forecast for that day.
- public_holidays: expect closures and crowds. Put popular sights early, suggest booking ahead, and
  mention the holiday by name on its day.
Only mention the days listed; don't guess at the weather for other days.
"""


def build_plan_generation_prompt(
    planning_result: dict[str, Any],
    destination: str,
    duration_days: int,
    origin: str | None = None,
    user_name: str | None = None,
    places_to_visit: list[str] | None = None,
    planning_preferences: dict[str, Any] | None = None,
) -> str:
    """
    Build the canonical prompt for turning destination research into an initial trip plan.
    Shared across non-streaming (planning_trip) and streaming (process_message_stream) paths.
    """
    planning_context = {
        "traveler_name": user_name,
        "destination": destination,
        "duration_days": duration_days,
        "origin": origin,
        "places_to_visit": places_to_visit or [],
        "planning_preferences": planning_preferences or {},
        # Typical weather for the travel months (seasonality.trip_season); kept ahead of the research
        # so truncation below never drops it. Absent when there's no start date or the lookup failed.
        **({"season": planning_result["season"]} if planning_result.get("season") else {}),
        # Forecast days and public holidays in the trip's dates (enrichment.draft_conditions), same rules.
        **({"conditions": planning_result["conditions"]} if planning_result.get("conditions") else {}),
        **({"hotel_references": planning_result["lookups"]} if planning_result.get("lookups") else {}),
        "trip_type": planning_result.get("trip_type"),
        "planning_notes": planning_result.get("planning_notes"),
        "research_queries": planning_result.get("research_queries", []),
        "research_results": planning_result.get("research_results", []),
        "candidates": planning_result.get("candidates", []),
    }

    planning_context_json = json.dumps(
        planning_context,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    # Protect prompt from exceeding context bounds if search pages are large
    max_context_chars = 30000
    if len(planning_context_json) > max_context_chars:
        logger.info(
            "Planning context is large (%d chars); truncating to %d chars.",
            len(planning_context_json),
            max_context_chars,
        )
        planning_context_json = (
            planning_context_json[:max_context_chars]
            + "\n...[planning context truncated for LLM input]..."
        )

    return f"""TRIP INFORMATION AND RESEARCH:

{planning_context_json}

TASK:

Create the first draft of the travel plan for this trip.

The traveler has not asked for bookings. Give them one section per requested day,
in visit order, with planning-slot clock times they can move around.

Use the researched candidates and findings instead of generic filler.
{SEASON_TASK if planning_result.get('season') else ''}{CONDITIONS_TASK if planning_result.get('conditions') else ''}{off_earth_note(origin, destination)}{named_place_instruction(planning_result.get('named_sights'), planning_result.get('named_restaurants'))}{evidence_from_result(planning_result) + chr(10) if planning_result.get('workers') or planning_result.get('hotel_options') or planning_result.get('meals') or planning_result.get('tickets') else ''}{'Use hotel_references only as names to consider. Do not invent a rate that is not written there.\n' if planning_result.get('lookups') else ''}
Formatting:
- Return valid Markdown only.
- Do not use raw HTML tags such as <br>, <div>, <table>, <p>, or <span>.
- Use GitHub-Flavored Markdown tables when presenting structured day-by-day highlights.
- Do not wrap the entire response in a code block.

Traveler name: {user_name or "not provided"}
Destination: {destination}
Duration: {duration_days} days
Origin: {origin or "not specified"}
Requested places: {", ".join(places_to_visit or []) or "open to suggestions"}
Planning preferences: {json.dumps(planning_preferences or {}, ensure_ascii=False)}
{guide_leaning(planning_preferences)}"""


async def execute_planning_subgraph(
    destination: str,
    duration_days: int,
    origin: str | None = None,
    places_to_visit: list[str] | None = None,
    planning_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Canonical helper to execute the destination research and candidate planning subgraph.
    Shared between main graph adapter (planning_trip) and streaming conversation handler.
    """
    planning_input = create_initial_planning_state(
        destination=destination,
        duration_days=duration_days,
        origin=origin,
        places_to_visit=places_to_visit,
        planning_preferences=planning_preferences,
    )

    logger.info("🔥 INVOKING planning_graph")
    try:
        planning_result = await planning_graph.ainvoke(planning_input)
    except Exception:
        logger.exception("🔥 PLANNING GRAPH FAILED")
        raise

    logger.info("🔥 planning_graph completed")
    logger.info(
        "Planning subgraph summary: queries=%d results=%d candidates=%d",
        len(planning_result.get("research_queries", [])),
        len(planning_result.get("research_results", [])),
        len(planning_result.get("candidates", [])),
    )
    logger.info("Planning candidates: %s", planning_result.get("candidates", []))

    return planning_result


async def planning_trip(state: TripPlanningState) -> dict[str, Any]:
    """
    Main-graph adapter/orchestrator for the planning phase (non-streaming).

    Flow:
        TripPlanningState
            ↓
        execute_planning_subgraph (PlanningState → research + candidates)
            ↓
        build_plan_generation_prompt
            ↓
        LLM generates first-draft plan
            ↓
        return plan + planning data to parent state
    """
    logger.info("🔥 ENTERED planning_trip")

    destination = state["destination"]
    duration_days = state["duration_days"]
    origin = state.get("origin")
    user_name = state.get("user_name")
    places_to_visit = state.get("places_to_visit") or []
    planning_preferences = state.get("planning_preferences") or {}

    if not destination or duration_days is None:
        raise ValueError(
            "Cannot execute planning_trip without required destination and duration_days."
        )

    logger.info(
        "Planning input: destination=%s duration=%s origin=%s",
        destination,
        duration_days,
        origin,
    )

    # 1. Execute shared planning subgraph
    planning_result = await execute_planning_subgraph(
        destination=destination,
        duration_days=duration_days,
        origin=origin,
        places_to_visit=places_to_visit,
        planning_preferences=planning_preferences,
    )

    # 2. Build canonical plan prompt
    plan_generation_prompt = build_plan_generation_prompt(
        planning_result=planning_result,
        destination=destination,
        duration_days=duration_days,
        origin=origin,
        user_name=user_name,
        places_to_visit=places_to_visit,
        planning_preferences=planning_preferences,
    )

    # 3. Generate initial plan
    from app.services.admin_settings import active_instruction
    logger.info("🔥 GENERATING INITIAL PLAN FROM RESEARCH")
    try:
        assistant_response = await llm_service.generate(
            prompt=plan_generation_prompt,
            system_instruction=active_instruction("plan", PLAN_GENERATION_SYSTEM_INSTRUCTION),
            temperature=0.5,
        )
    except Exception:
        logger.exception("🔥 INITIAL PLAN GENERATION FAILED")
        raise

    if not assistant_response or not str(assistant_response).strip():
        raise RuntimeError("Initial plan generation returned an empty response.")

    assistant_response = str(assistant_response).strip()
    logger.info("🔥 INITIAL PLAN GENERATED")

    # 4. Return plan + planning data to parent state
    return {
        "assistant_response": assistant_response,
        "planning_notes": planning_result.get("planning_notes"),
        "research_queries": planning_result.get("research_queries", []),
        "research_results": planning_result.get("research_results", []),
        "candidates": planning_result.get("candidates", []),
    }
