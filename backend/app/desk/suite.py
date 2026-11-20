"""The same checks the tests run, so the admin page can show them."""

from app.desk.policy import asks_for_a_person, note_unchanged_edit
from app.planning.upgrades import (
    SerpApiRing,
    flight_window,
    mask_for_model,
    move_far_stops,
    place_decision,
    render_plan,
    restore_locks,
    review_days,
    stitch_cities,
    worker_block,
)
from app.security.pii import mask_pii


def offline_cases() -> list[dict]:
    cases = []

    def add(name: str, passed: bool, detail: str) -> None:
        cases.append({"name": name, "passed": bool(passed), "detail": detail})

    add("asks for a person", asks_for_a_person("I want to talk to a live agent"), "A request queues the chat.")
    add("a refusal does not", not asks_for_a_person("Please don't talk to a live agent"), "A negated request stays with the planner.")
    stuck = {"status": "auto", "reason": "", "stuck": 0, "last_words": []}
    for _ in range(3):
        stuck = note_unchanged_edit(stuck, "move the museum again", unchanged=True)
    add("three unchanged edits wait", stuck["status"] == "queued", "The third shared failure queues the chat.")
    add("email is masked", mask_pii("write me at a@b.co") == "write me at [EMAIL]", "The model copy drops the address.")
    add("known city is kept", place_decision("Kyoto", None, "Kyoto, Japan")[1] is None, "Kyoto does not wait for confirmation.")
    add("unknown hit waits", place_decision("Heaven", "Kyoto", "Heaven, Texas")[0] == "Kyoto", "A different map hit is not stored yet.")
    window = flight_window("22:45", "16:00")
    add("late landing opens later", window["day1_open"] == "00:45", "Day 1 opens two hours after landing.")
    days = [
        {"day": 1, "items": [{"name": "Far", "lat": 0.0, "lon": 0.0}, {"name": "Near", "lat": 0.2, "lon": 0.0}]},
        {"day": 2, "items": [{"name": "There", "lat": 0.01, "lon": 0.0}]},
    ]
    notes = move_far_stops(days, km=8)
    add("a far stop moves", any(note.startswith("Moved Far") for note in notes), "A sight far from its day moves to a closer day.")
    stitched = stitch_cities([{"city": "Kyoto", "days": [{"day": 1}]}, {"city": "Osaka", "days": [{"day": 1}]}])
    add("cities stay in order", [day["city"] for day in stitched] == ["Kyoto", "Osaka"] and stitched[1]["day"] == 2, "Each city is planned, then the days are numbered once.")
    block = worker_block(tickets=None)
    add("a missing fare stays missing", "Do not invent a price" in block, "Tickets without a source are a gap.")
    restored = restore_locks(
        [{"day": 1, "items": [{"name": "Locked Inn"}]}],
        [{"day": 1, "items": [{"name": "Other Inn"}]}],
        [{"kind": "stay", "name": "Locked Inn", "day": 1}],
    )
    add("a lock comes back", any(item["name"] == "Locked Inn" for item in restored[0]["items"]), "A revision cannot drop a locked stay.")
    rendered = render_plan("Walk to Fushimi", "zh-Hant", {})
    add("transport words translate", "步行" in rendered and "Fushimi" in rendered, "A place name stays unless a source supplied one.")
    ring = SerpApiRing(["one", "two"])
    add("a spent key steps aside", ring.retire_if_quota("run out of searches") == "two", "The next ready key is used.")
    add("selection is named", "Nezu" in mask_for_model("what is this?", "Nezu"), "This means the selected stop.")
    findings = review_days(
        [{"day": 1, "items": [{"name": "Park", "category": "sight", "minutes": 30, "price": 10}]}],
        known={"Park"}, city="Kyoto",
    )
    add("a short park is noted", any("minutes" in note for note in findings), "A park is not given a short stop.")
    return cases
