from datetime import date, timedelta

from app.application.memory import MemoryService, embed
from app.config import Settings
from app.domain.models import SavedTrip, TripContext


def test_memory_recall_applies_the_destination_and_similarity(settings: Settings, container) -> None:
    memory = MemoryService(container.store, settings)
    trip = SavedTrip(
        trip_id="t1",
        destination="Kyoto",
        rating=1.0,
        stars=5,
        summary="Three days of temples and food for two travellers.",
        embedding=embed("Kyoto temples food three days two travellers slow pace"),
    )
    container.store.save_saved_trip(trip)
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=date.today() + timedelta(days=30),
        end_date=date.today() + timedelta(days=32),
        party_size=2,
        preferences=["slow pace", "food"],
    )
    matches = memory.recall(context)
    assert matches
    assert matches[0][0] >= settings.memory_similarity_cutoff

    context.destination = "Bangkok"
    assert memory.recall(context) == []


def test_memory_cutoff_can_exclude_everything(settings: Settings, container) -> None:
    strict = settings.model_copy(update={"memory_similarity_cutoff": 1.01})
    memory = MemoryService(container.store, strict)
    container.store.save_saved_trip(
        SavedTrip(trip_id="t2", destination="Kyoto", rating=1.0, summary="Anything", embedding=embed("unrelated words"))
    )
    context = TripContext(session_id="s", destination="Kyoto", preferences=["temples"])
    assert memory.recall(context) == []


def test_embedding_is_stable_and_normalised() -> None:
    first = embed("Kyoto temples")
    second = embed("Kyoto temples")
    assert first == second
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6
