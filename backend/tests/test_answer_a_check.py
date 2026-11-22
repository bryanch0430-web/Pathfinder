"""'ok fix' answers the failed-check question instead of leaving the plan alone."""

from app.application.check_fix import fix_failed_check
from app.domain.models import ConstraintCheck, ConstraintStatus, PlanAssessment
from tests.plan_samples import make_plan


def _ask(reply: str) -> str:
    return (
        "About this question on the plan: I could not meet “flight arrival” with the data I found. "
        "Should I relax it, or change the plan around it?\n\n" + reply
    )


def test_ok_fix_moves_stops_that_start_before_the_flight() -> None:
    plan = make_plan(days=2, with_tickets=True)
    plan.constraints = [
        ConstraintCheck(text="flight arrival", status=ConstraintStatus.FAIL, evidence="stale"),
    ]
    plan.assessment = PlanAssessment(
        question="I could not meet “flight arrival” with the data I found. Should I relax it, or change the plan around it?",
        next_action="ask",
        missing=["Whether to drop or relax: flight arrival."],
    )
    updated = fix_failed_check(plan, _ask("ok fix"))
    assert updated is not None
    assert updated.version == plan.version + 1
    assert updated.assessment is not None and updated.assessment.question == ""
    arrival = next(check for check in updated.constraints if check.text == "flight arrival")
    assert arrival.status == ConstraintStatus.PASS
    assert any(item.start >= "15:55" for item in updated.days[0].items)


def test_a_new_request_is_not_an_answer_to_the_check() -> None:
    plan = make_plan(days=2)
    assert fix_failed_check(plan, "ok sorry, can you add a stop at day 1 where i can see sakura flower") is None
