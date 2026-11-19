"""Failed cases come from evaluation and from live or finished chats. A suggestion stays a draft."""

from __future__ import annotations

from app.application.case_review import failed_cases, suggest_skill_changes
from app.domain.models import EvalCaseResult, EvaluationRun, Feedback, Intent, Message
from app.infrastructure.store import SQLiteStore
from tests.test_scoped_edits import _plan


class _Writer:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def json(self, *_args, **_kwargs):
        self.calls += 1
        return {
            "suggestions": [
                {
                    "skill": "modify",
                    "summary": "A day can lose every attraction.",
                    "rationale": "Chats that asked to clear a day left the plan unchanged.",
                    "body": "When the traveller names a day and asks to remove its attractions, remove the unlocked attractions on that day.",
                }
            ]
        }


def test_failed_cases_include_evaluation_and_chats(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "cases.db")
    store.save_eval_run(
        EvaluationRun(
            experiment_tag="check",
            dataset_id="routing",
            cases=[
                EvalCaseResult(
                    case_id="miss",
                    dataset="routing",
                    split="dev",
                    passed=False,
                    failure_class="wrong_intent",
                    input="only change day 2",
                    output_summary="every day changed",
                ),
                EvalCaseResult(case_id="ok", dataset="routing", split="dev", passed=True, input="two stops a day"),
            ],
        )
    )
    session = store.create_session("guest")
    store.add_message(Message(session_id=session.id, role="user", content="I want to see the Gundam statue"))
    store.add_message(Message(session_id=session.id, role="assistant", content="Which day should it go on?"))
    store.add_message(Message(session_id=session.id, role="user", content="remove all attractions on day 2"))
    store.add_message(
        Message(
            session_id=session.id,
            role="assistant",
            content="No unlocked stop matched that removal, so the itinerary stayed as it was.",
            intent=Intent.MODIFY,
            meta={"diff": []},
        )
    )
    kept = store.create_session("guest-2")
    store.add_message(
        Message(
            session_id=kept.id,
            role="assistant",
            content="Updated to version 2.",
            intent=Intent.MODIFY,
            meta={"diff": [{"kind": "item"}]},
        )
    )
    rated = store.create_session("guest-3")
    plan = _plan()
    plan.session_id = rated.id
    store.save_plan(plan, "built")
    store.add_message(Message(session_id=rated.id, role="user", content="this plan walks too far"))
    store.add_feedback(Feedback(trip_id=plan.trip_id, useful=False, stars=2, reason_code="too much walking"))

    cases = failed_cases(store)
    kinds = {(row["source"], row["kind"]) for row in cases}

    assert ("evaluation", "misclassified") in kinds
    assert ("session", "unchanged") in kinds
    unchanged = next(row for row in cases if row["kind"] == "unchanged")
    assert "I want to see the Gundam statue" in unchanged["earlier"]
    assert "remove all attractions on day 2" not in unchanged["earlier"]
    assert ("session", "not_useful") in kinds
    assert all(row["case_id"] != "ok" for row in cases)
    assert any(row["session_state"] == "live" for row in cases if row["source"] == "session")
    assert not any("Updated to version 2" in row["output_summary"] for row in cases)

    store.add_operator_case(
        session_id=session.id,
        session_state="live",
        message="remove all attractions on day 2",
        expected="Day 2 has no attractions. The other days stay.",
        note="The removal said no stop matched.",
    )
    added = failed_cases(store)
    assert any(row["kind"] == "added" and row["source"] == "operator" for row in added)


def test_a_check_marks_an_exam_case_that_now_passes(tmp_path) -> None:
    import asyncio

    from app.application.case_review import check_listed
    from app.domain.models import EvalCaseResult

    store = SQLiteStore(tmp_path / "check.db")
    store.save_eval_run(
        EvaluationRun(
            experiment_tag="before",
            dataset_id="routing",
            cases=[
                EvalCaseResult(
                    case_id="r03",
                    dataset="routing",
                    split="dev",
                    passed=False,
                    failure_class="wrong_intent",
                    input="can you build an itinerary",
                )
            ],
        )
    )

    class _Scorer:
        async def rescore(self, dataset: str, ids: set[str]):
            assert dataset == "routing" and "r03" in ids
            return [
                EvalCaseResult(
                    case_id="r03",
                    dataset="routing",
                    split="dev",
                    passed=True,
                    output_summary="rules router → plan (0.84)",
                    judge_reason="Expected plan.",
                )
            ]

    result = asyncio.run(check_listed(_Scorer(), store))

    assert result["passed"] == 1 and result["exam"] == 1
    assert result["cases"][0]["check_status"] == "pass"


async def _suggest(store: SQLiteStore):
    writer = _Writer()
    saved = await suggest_skill_changes(writer, store)
    return writer, saved


def test_a_suggestion_is_not_used_until_it_is_accepted(tmp_path) -> None:
    import asyncio

    store = SQLiteStore(tmp_path / "suggest.db")
    session = store.create_session("guest")
    store.add_message(Message(session_id=session.id, role="user", content="remove all attractions on day 2"))
    store.add_message(
        Message(
            session_id=session.id,
            role="assistant",
            content="The plan stayed as it was.",
            intent=Intent.MODIFY,
            meta={"diff": []},
        )
    )

    writer, saved = asyncio.run(_suggest(store))

    assert writer.calls == 1 and saved and saved[0]["status"] == "draft"
    assert store.prompt_override("modify") is None
    accepted = store.decide_prompt_suggestion(saved[0]["id"], "accepted", saved[0]["body"])
    store.set_prompt_override(accepted["skill"], accepted["body"])
    assert "names a day" in (store.prompt_override("modify") or "")


def test_a_restored_suggestion_can_be_dismissed(settings) -> None:
    from fastapi.testclient import TestClient

    from app.interface.api import create_app
    from app.infrastructure.store import SQLiteStore

    store = SQLiteStore(settings.database_path)
    leftover = store.add_prompt_suggestion(
        skill="router",
        summary="A leftover replacement",
        rationale="The built-in skill is already back.",
        body="replacement",
        case_ids=[],
    )
    store.decide_prompt_suggestion(leftover["id"], "restored")
    live = store.add_prompt_suggestion(
        skill="modify",
        summary="Still in use",
        rationale="This replacement is live.",
        body="replacement",
        case_ids=[],
    )
    store.decide_prompt_suggestion(live["id"], "accepted")
    headers = {"X-Admin-Token": settings.admin_token}
    with TestClient(create_app(settings)) as client:
        gone = client.post(f"/api/admin/cases/suggestions/{leftover['id']}/dismiss", headers=headers)
        kept = client.post(f"/api/admin/cases/suggestions/{live['id']}/dismiss", headers=headers)
    assert gone.status_code == 200
    assert store.get_prompt_suggestion(leftover["id"])["status"] == "dismissed"
    assert leftover["id"] not in {row["id"] for row in store.list_prompt_suggestions()}
    assert kept.status_code == 404
    assert store.get_prompt_suggestion(live["id"])["status"] == "accepted"
