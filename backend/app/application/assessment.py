"""How far a plan can be relied on, and what the planner does about what it cannot.

``assess_plan`` reads only the plan's own checks (data gaps, the refine loop's leftover issues,
constraint checks, opening hours, the stay's rate and the stay research verdict) and turns them into
a confidence, a list of what no source confirmed, a list of what only the traveller can say, and one
next action. It makes no model call and no judgement of its own: every signal is a fact already on
the plan, with a fixed weight.

``assess_and_improve`` is the feedback loop: while the assessment says more research could settle
something, it runs that research, applies what it found to the plan (a better-fitting stay replaces
the chosen one, unless the traveller locked or confirmed it), and assesses again. What is still open
after the last round becomes a question for the traveller.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from app.application.profile_rules import CHILDREN_LONG_TRANSFER_MINUTES
from app.application.research import FAVOURABLE, research_unclear_stays, stay_wishes
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AssessmentSignal,
    ConstraintStatus,
    HotelStay,
    PlanAssessment,
    PriceKind,
    StayMatch,
    TripContext,
    TripPlan,
)

#: Opens the plan note for a change the feedback loop made (the refine loop's own prefix).
CHANGE_PREFIX = "Plan change: "

HIGH, MEDIUM = 0.75, 0.5
_MAX_LISTED = 6
_VERDICT_RANK = {"meets": 0, "partly": 1, "unclear": 2, "fails": 3}
#: What an unsettled stay wish costs, by the chosen stay's verdict (None: never researched).
_STAY_WEIGHT = {
    None: 0.12,
    "unclear": 0.12,
    "fails": 0.20,
    "partly": 0.05,
    "meets": 0.0,
}


def _level(confidence: float) -> str:
    return "high" if confidence >= HIGH else "medium" if confidence >= MEDIUM else "low"


def _capped(count: int, each: float, cap: float) -> float:
    return round(min(count * each, cap), 2)


#: At most this many step-free or long-transfer lines are listed, so the plan's other open points still show.
_PROFILE_LINES_EACH = 3


def _profile_lines(plan: TripPlan, context: TripContext | None) -> list[str]:
    """What the traveller's profile makes worth saying: places step-free access is not confirmed for, and a single long transfer
    on a trip with children. Read from fields the sources gave; nothing is judged here."""
    profile = getattr(context, "profile", None)
    if profile is None:
        return []
    lines: list[str] = []
    if profile.step_free:
        step = []
        for day in plan.days:
            for item in day.items:
                if item.type.value not in {"attraction", "meal"} or item.step_free is True:
                    continue
                if item.step_free is False:
                    step.append(f"no step-free entrance at {item.name} (the place source says so)")
                else:
                    step.append(f"step-free access not confirmed: {item.name}")
        lines.extend(list(dict.fromkeys(step))[:_PROFILE_LINES_EACH])
    if profile.with_children:
        long = []
        for number, day in enumerate(plan.days, start=1):
            for item in day.items:
                if item.transfer is not None and item.transfer.minutes > CHILDREN_LONG_TRANSFER_MINUTES:
                    long.append(
                        f"{item.transfer.minutes} minutes to get to {item.name} (day {number}) is long with children."
                    )
        lines.extend(long[:_PROFILE_LINES_EACH])
    return lines


def assess_plan(
    plan: TripPlan,
    context: TripContext | None,
    issues: Iterable[Any] = (),
    *,
    research_rounds: int = 0,
    can_research: bool = False,
    max_research_rounds: int = 1,
    improvements: Iterable[str] = (),
) -> PlanAssessment:
    """The plan's assessment. Pure: the plan is not changed."""
    signals: list[AssessmentSignal] = []
    unverified: list[str] = _profile_lines(plan, context)
    missing: list[str] = []

    gaps = [gap for gap in plan.data_quality.gaps if gap.section != "budget"]
    sections = list(dict.fromkeys(gap.section for gap in gaps))
    if sections:
        signals.append(
            AssessmentSignal(
                code="data_gap",
                weight=_capped(len(sections), 0.08, 0.24),
                message=f"Data was missing for: {', '.join(sections)}.",
            )
        )
        unverified.extend(f"{gap.section}: {gap.message}" for gap in gaps)

    issues = list(issues)
    errors = [issue for issue in issues if getattr(issue, "severity", "") == "error"]
    warns = [issue for issue in issues if getattr(issue, "severity", "") != "error"]
    if issues:
        weight = round(min(len(errors) * 0.08 + len(warns) * 0.03, 0.25), 2)
        signals.append(
            AssessmentSignal(
                code="plan_check",
                weight=weight,
                message=f"The plan check left {len(errors)} problem(s) and {len(warns)} warning(s) it could not fix.",
            )
        )
        unverified.extend(str(issue.message) for issue in [*errors, *warns][:3])

    failed = [
        check for check in plan.constraints if check.status == ConstraintStatus.FAIL
    ]
    unknown = [
        check for check in plan.constraints if check.status == ConstraintStatus.UNKNOWN
    ]
    if failed:
        signals.append(
            AssessmentSignal(
                code="constraint_failed",
                weight=_capped(len(failed), 0.10, 0.20),
                message=f"{len(failed)} of your requirements could not be met.",
            )
        )
        unverified.extend(f"Not met: {check.text}" for check in failed)
    if unknown:
        signals.append(
            AssessmentSignal(
                code="constraint_unchecked",
                weight=_capped(len(unknown), 0.04, 0.12),
                message=f"{len(unknown)} of your requirements could not be checked against the data.",
            )
        )
        unverified.extend(f"Not checked: {check.text}" for check in unknown)

    stops = [
        item for day in plan.days for item in day.items if item.type.value != "meal"
    ]
    unstated = [item for item in stops if item.hours_status != "stated"]
    if stops and len(unstated) * 2 >= len(stops):
        signals.append(
            AssessmentSignal(
                code="hours_unknown",
                weight=0.06,
                message=f"Opening hours are not stated for {len(unstated)} of {len(stops)} stops.",
            )
        )
        unverified.append(f"Opening hours for {len(unstated)} of {len(stops)} stops.")

    stay = plan.first_stay
    overnight = plan.dates.night_count > 0
    if overnight and stay is None and "hotels" not in sections and len(plan.segments) <= 1:
        signals.append(
            AssessmentSignal(
                code="no_stay", weight=0.10, message="No stay was found for the trip."
            )
        )
        unverified.append("Where to stay.")
    if stay is not None and (
        stay.price_per_night is None or stay.price_kind == PriceKind.NONE
    ):
        signals.append(
            AssessmentSignal(
                code="stay_unpriced",
                weight=0.04,
                message=f"{stay.name} has no rate for these dates.",
            )
        )
        unverified.append(f"The nightly rate at {stay.name}.")
    if len(plan.segments) > 1:
        _assess_route(plan, sections, signals, missing, unverified)
    if plan.cost.status == "over":
        signals.append(
            AssessmentSignal(
                code="over_budget",
                weight=0.08,
                message="The verified cost is over the budget.",
            )
        )

    wishes = stay_wishes(context)
    stay_open = False
    if wishes and stay is not None:
        match = stay.match
        verdict = match.verdict if match is not None else None
        weight = _STAY_WEIGHT[verdict]
        wish_text = "; ".join(wishes[:2])
        if verdict in (None, "unclear"):
            stay_open = True
            signals.append(
                AssessmentSignal(
                    code="stay_wish_unclear",
                    weight=weight,
                    message=f"No source says whether {stay.name} offers: {wish_text}.",
                )
            )
            unverified.append(f"Whether {stay.name} offers: {wish_text}.")
        elif verdict == "fails":
            stay_open = True
            signals.append(
                AssessmentSignal(
                    code="stay_wish_failed",
                    weight=weight,
                    message=f"{stay.name} does not appear to offer: {wish_text}.",
                )
            )
            unverified.append(f"Not met: {wish_text} (at {stay.name}).")
        elif verdict == "partly":
            signals.append(
                AssessmentSignal(
                    code="stay_wish_partly",
                    weight=weight,
                    message=f"{stay.name} only partly fits: {wish_text}.",
                )
            )

    confidence = round(max(0.05, 1.0 - sum(signal.weight for signal in signals)), 2)
    next_action, question = "none", ""
    if (
        stay_open
        and can_research
        and research_rounds < max_research_rounds
        and not (stay.locked or stay.confirmed)
    ):
        next_action = "research"
    elif stay_open:
        next_action = "ask"
        missing.append(
            f"Whether another room type, price range or area would do for: {wishes[0]}."
        )
        question = (
            f"I could not confirm “{wishes[0]}” at {stay.name} or at the other stays I found. "
            "Would a different room type do, or should I look at a higher nightly rate or another area?"
        )
    elif failed:
        next_action = "ask"
        missing.append(f"Whether to drop or relax: {failed[0].text}.")
        question = (
            f"I could not meet “{failed[0].text}” with the data I found. "
            "Should I relax it, or change the plan around it?"
        )
    return PlanAssessment(
        confidence=confidence,
        level=_level(confidence),  # type: ignore[arg-type]
        signals=signals,
        missing=missing[:_MAX_LISTED],
        unverified=list(dict.fromkeys(unverified))[:_MAX_LISTED],
        next_action=next_action,  # type: ignore[arg-type]
        question=question,
        research_rounds=research_rounds,
        improvements=list(improvements)[:_MAX_LISTED],
    )


def reassess(
    plan: TripPlan, context: TripContext | None, settings: Any | None = None
) -> PlanAssessment:
    """The assessment of a plan as it stands (after a choice or an edit), with no further research.

    The refine loop's checks are run again to count what is still wrong; the rounds of research and
    the improvements already on the plan's assessment are kept.
    """
    from app.application.refine import RefineLimits, evaluate_plan

    try:
        issues = evaluate_plan(
            plan, context, limits=RefineLimits.from_settings(settings)
        )
    except Exception:
        issues = []
    before = plan.assessment
    return assess_plan(
        plan,
        context,
        issues,
        research_rounds=before.research_rounds if before else 0,
        improvements=before.improvements if before else (),
    )


def _rank(match: StayMatch | None) -> tuple[int, int]:
    return (
        (_VERDICT_RANK[match.verdict], -match.score)
        if match is not None
        else (_VERDICT_RANK["unclear"], 0)
    )


def apply_stay_matches(
    plan: TripPlan,
    matches: dict[str, StayMatch],
    *,
    planner: Any,
    context: TripContext | None,
) -> list[str]:
    """Write new judgements on the plan's stays and move to a better-fitting one. Returns what changed."""
    from app.application.choices import _choose_hotel, _recost, stay_dates_match
    from app.application.planner import annotate_stays

    if not matches:
        return []
    told: list[str] = []
    for stay in [*plan.hotels, *plan.hotel_options]:
        fresh = matches.get(stay.id)
        if fresh is not None:
            stay.match = fresh.model_copy(
                update={"rooms_checked": stay.rooms_checked_for is not None}
            )
    current: HotelStay | None = plan.first_stay
    if current is None:
        return told
    wish = (
        current.match.wishes[0]
        if current.match and current.match.wishes
        else "your stay wish"
    )
    if (
        current.id in matches
        and current.match is not None
        and current.match.verdict in FAVOURABLE
    ):
        told.append(f"A reference page confirms {current.name} fits: {wish}.")
    candidates = [
        stay
        for stay in plan.hotel_options
        if stay.id != current.id
        and stay.match is not None
        and stay.match.verdict in FAVOURABLE
        and stay_dates_match(stay, current)
    ]
    candidates.sort(key=lambda stay: _rank(stay.match))
    better = candidates[0] if candidates else None
    if (
        better is not None
        and not (current.locked or current.confirmed)
        and _VERDICT_RANK[better.match.verdict] < _rank(current.match)[0]  # type: ignore[union-attr]
    ):
        _choose_hotel(plan, better.id)
        _recost(plan, planner, context)
        annotate_stays(plan)
        change = f"The stay is now {better.name} instead of {current.name}: further research found it fits {wish}."
        told.append(change)
        plan.data_quality.notes.append(CHANGE_PREFIX + change)
    return told


def _assess_route(
    plan: TripPlan,
    sections: Iterable[str],
    signals: list[AssessmentSignal],
    missing: list[str],
    unverified: list[str],
) -> None:
    """What a plan of several cities could not settle: a city with no stay, a leg with no option, a leg that is only a
    rough estimate. Each finding names its city."""
    for segment in plan.segments:
        nights = segment.dates.night_count
        if nights > 0 and not plan.stays_in(segment.id) and "hotels" not in sections:
            signals.append(
                AssessmentSignal(
                    code="no_stay", weight=0.05, message=f"No stay was found in {segment.destination.name}."
                )
            )
            unverified.append(f"Where to stay in {segment.destination.name}.")
    for leg in plan.legs:
        options = plan.options_for(leg.id)
        where = f"{leg.from_place} to {leg.to_place}"
        if not options:
            signals.append(
                AssessmentSignal(code="no_transport", weight=0.05, message=f"No transport was found from {where}.")
            )
            missing.append(f"transport from {leg.from_place} to {leg.to_place}")
            continue
        chosen = next((option for option in options if option.selected), options[0])
        if chosen.mode == "ground":
            unverified.append(f"Transport from {where} is a rough estimate from the distance, not a timetable.")


async def assess_and_improve(
    plan: TripPlan,
    *,
    context: TripContext,
    issues: Iterable[Any],
    registry: Any,
    toolbox: Any,
    planner: Any,
    settings: Any,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> TripPlan:
    """Assess the plan; research what is unclear, apply it and assess again; leave the result on the plan."""
    issues = list(issues)
    gateway = getattr(planner, "gateway", None)
    can_research = bool(
        getattr(settings, "enable_stay_research", True)
        and getattr(settings, "web_search_enabled", False)
        and gateway is not None
        and gateway.available
    )
    rounds_cap = int(getattr(settings, "assessment_max_research_rounds", 1))
    rounds = 0
    improvements: list[str] = []

    def assess() -> PlanAssessment:
        return assess_plan(
            plan,
            context,
            issues,
            research_rounds=rounds,
            can_research=can_research,
            max_research_rounds=rounds_cap,
            improvements=improvements,
        )

    assessment = assess()
    while assessment.next_action == "research" and rounds < rounds_cap:
        rounds += 1
        if emit is not None:
            emit(
                "progress",
                {
                    "phase": "assess",
                    "message": "Checking what the plan could not confirm.",
                    "progress": 92,
                },
            )
        try:
            matches = await research_unclear_stays(
                plan,
                context=context,
                registry=registry,
                toolbox=toolbox,
                gateway=gateway,
                settings=settings,
            )
            improvements.extend(
                apply_stay_matches(plan, matches, planner=planner, context=context)
            )
        except CanaryLeak:
            raise
        except Exception:
            # Further research fails open: the plan stands as it was assessed.
            pass
        assessment = assess()
    plan.assessment = assessment
    return plan


__all__ = ["apply_stay_matches", "assess_and_improve", "assess_plan", "reassess"]
