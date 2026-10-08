"""Memory scenarios 19-20, end to end (unit tests live in backend/tests/memory)."""

from __future__ import annotations

from backend.container import Container
from backend.memory.retrieval import plan_summary_text
from backend.schemas.common import Route
from backend.tests.integration.conftest import kyoto_context


async def test_s19_useful_plan_is_saved_embedded_and_written_in_background(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context(hotel_style="business"))
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None

    queued = await container.orchestrator.mark_useful(state.session_id, useful=True, rating=4)
    assert queued is True
    await container.writer.drain()  # the write happened off the request path

    vector = (await container.embedder.embed([plan_summary_text(result.plan)]))[0]
    hits = await container.repos.embeddings.search(
        vector, model=container.embedder.model, top_k=5, min_similarity=0.99
    )
    assert len(hits) == 1
    record = await container.repos.trips.get(hits[0].trip_id)
    assert record is not None
    assert record.destination == "Kyoto" and record.rating == 4
    assert record.plan == result.plan
    profile = await container.repos.preferences.get(state.session_id)
    assert profile is not None and profile.hotel_style == "business"
    assert profile.liked_categories  # categories of the liked plan
    assert container.writer.completed == 1 and container.writer.failed == 0


async def test_s19_plan_not_marked_useful_is_not_saved(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context())
    await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert await container.orchestrator.mark_useful(state.session_id, useful=False, rating=2) is False
    await container.writer.drain()
    assert container.writer.completed == 0
    assert await container.repos.preferences.get(state.session_id) is None


async def test_s20_session_memory_holds_plan_history_and_preferences(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context(hotel_style="ryokan"))
    sid = state.session_id
    first = await container.orchestrator.handle_turn(sid, "Plan my trip to Kyoto please")
    second = await container.orchestrator.handle_turn(sid, "What is the weather in Kyoto on 11 April?")
    third = await container.orchestrator.handle_turn(sid, "Can you swap the hotel for another one?")
    assert (first.route, second.route, third.route) == (Route.PLAN, Route.ASK, Route.MODIFY)

    stored = await container.orchestrator.get_session(sid)
    assert stored.plan is not None and third.plan is not None
    assert stored.plan.version == third.plan.version == 2
    assert [t.role for t in stored.history] == ["user", "assistant"] * 3
    assert [t.route for t in stored.history[::2]] == [Route.PLAN, Route.ASK, Route.MODIFY]
    assert stored.preferences.hotel_style == "ryokan"
    assert stored.preferences.budget_hint is not None and stored.preferences.party_size_hint == 2
    assert stored.turn_count == 3

    # The router of the third turn saw the plan, the recent history and the preference profile.
    router_call = next(r for r in container.observability.llm_calls(third.trace_id) if r.purpose == "router")
    prompt = router_call.request.messages[1].content
    assert first.plan is not None and first.plan.plan_id in prompt
    assert "What is the weather in Kyoto on 11 April?" in prompt
    assert '"hotel_style":"ryokan"' in prompt
