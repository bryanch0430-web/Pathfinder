# Full-Plan Chat Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the traveller has no stop selected and names no day, the edit and answer prompts carry the whole plan in detail; otherwise they carry the outline plus that day in detail and name the selected stop.

**Architecture:** A text renderer `plan_detail` next to the existing `plan_outline`, a small `plan_focus` module that works out which day (if any) the message is about, and a `plan_context` function that picks between them. `ModifyPath._extract` and `ask_prompt` call `plan_context` in place of `plan_outline`. Backend only; the frontend payload does not change.

**Tech Stack:** Python, pydantic models in `backend/app/domain/models.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-multi-city-trips-design.md`, section "Full-plan context (goal 7)". This is step 1 of that spec's "Order of work"; steps 2-7 get their own plans.

## Global Constraints

- No URL and no image reference in any prompt text produced here.
- Budget for the detail text: 24 000 characters. Over it, drop in order: unchosen alternatives, meal detail, then per-item price/rating. Never drop a day or a stay.
- The map's active day is not a selection. Only `scope_item_id` or a day/date named in the message narrows the context.
- Every name or note copied into a prompt goes through `strip_fence_tags` (as `plan_outline` does) and is flattened to one line.
- A value the plan does not hold is left out; the text never contains `None`.
- **Deviation from the spec:** the router keeps `plan_outline`. It only classifies the turn and runs on every message; the detail would add cost there for no decision it makes. Modify and ask get the detail.
- All commands run from `backend/`: tests `PYTHONPATH=. python -m pytest -q`, lint `ruff check`.
- Commit messages are one plain sentence describing the behaviour (see `git log`), with no co-author or tool attribution lines.

## Review Focus

1. A plan with no stay, no tickets and an empty cost: the detail renders the days and simply has no Stay/Transport/Cost lines. (Task 1)
2. `scope_item_id` that is no longer in the plan: treated as no selection, so the full detail is sent. (Task 2)
3. A message naming a day the trip does not have ("day 9" of a 3-day trip): no focus, full detail, not an empty block. (Task 2)
4. A 30-day plan with alternatives on every meal: output is at most 24 000 characters and still has a line for every day and every stay. (Task 1)
5. A place name holding a fence tag or a line break: the tag is gone and the item stays on one line. (Task 1)

## File Structure

- Modify `backend/app/application/prompt_context.py`: add `PLAN_DETAIL_CHARS`, `plan_detail`, `plan_context`.
- Create `backend/app/application/plan_focus.py`: `day_number_named`, `focus_day`.
- Modify `backend/app/application/modification.py`: `_extract` takes the selection and uses `plan_context`; `_rule_day` becomes an alias of `plan_focus.day_number_named`.
- Modify `backend/app/application/asking.py`: `ask_prompt` takes the selection and uses `plan_context`; `_named_date` moves to `plan_focus`.
- Modify `backend/app/prompts/modify.md` and the ask prompt file (the one `gateway` loads for the purpose used at `asking.py:312`): one paragraph each on the new block.
- Create tests `backend/tests/test_plan_detail.py`, `test_plan_focus.py`, `test_modify_plan_context.py`, `test_ask_plan_context.py`.

---

### Task 0: Baseline

- [ ] **Step 1:** Run `PYTHONPATH=. python -m pytest -q` and `ruff check`. Record the pass/fail counts and the names of any failing tests in the task report. Later tasks must not add failures to this list.

### Task 1: `plan_detail`

**Files:**
- Modify: `backend/app/application/prompt_context.py`
- Test: `backend/tests/test_plan_detail.py`

**Interfaces:**
- Produces:
  - `PLAN_DETAIL_CHARS: int = 24_000`
  - `plan_detail(plan: TripPlan, *, days: Sequence[int] | None = None, ids: bool = True, cap: int = PLAN_DETAIL_CHARS) -> str`. `days` are 1-based day numbers; `None` means every day. Numbers outside the plan are ignored.

**Output format** (one logical record per line; a part is omitted when the plan has no value for it):

```
<destination>, <start>..<end>, party <n>, budget <amount> <CUR>, v<version>
Stay: <id> <name>, <check_in>..<check_out>, <amount> <CUR>/night (<price_kind>), rating <r> (<reviews>), <km> km to centre [locked]
Transport: <id> <mode> <origin>-<destination> <operator> <depart date HH:MM>-<arrive HH:MM>, return <date HH:MM>-<HH:MM>, <amount> <CUR> (<price_kind>) [selected]
Other transport: <id> <mode> <operator> <depart HH:MM>, <amount> <CUR>; ...
Day <N> <date> "<title>":
  <id> <start>-<end> <name> (<type>[ <meal_slot>]) <amount> <CUR> | <price_level>, <cuisine>, rating <r> (<reviews>) [locked] [confirmed]
    other choices: <name> (rating <r>, <price_level>, <distance_m> m); ...
Cost: total <total> <CUR> of <budget> (<status>); <label> <amount>; ...
```

- Header fields and item ids follow the same rules as `plan_outline` (`ids=False` drops the ids).
- One `Stay:` line per entry of `plan.hotels`. `Transport:` is `selected_ticket(plan.tickets)`; the rest go on one `Other transport:` line.
- `(attraction)` is not written, as in `plan_outline`; other types are.
- With `days` given, only those days' blocks are written; the header, Stay, Transport and Cost lines are still written.
- **Fitting to `cap`:** build at full detail; if longer than `cap`, rebuild without the `other choices` lines; if still longer, rebuild with meal items reduced to `<id> <start>-<end> <name> (meal <slot>)`; if still longer, rebuild with price, price level, cuisine and rating dropped from every item line. Return the first that fits, else the last.

- [ ] **Step 1: Write the failing tests** in `test_plan_detail.py`. Build plans with the model constructors (see `tests/conftest.py` for existing plan fixtures and reuse one where it fits). Tests and their assertions:

```python
def test_detail_has_stay_transport_days_and_cost(plan):       # a 2-day plan with one hotel, one selected flight, cost lines
    text = plan_detail(plan)
    assert text.splitlines()[0].startswith(plan.destination.name)
    assert f"Stay: {plan.hotels[0].id} {plan.hotels[0].name}" in text
    assert "Transport: " in text and "[selected]" in text
    assert "Day 1 " in text and "Day 2 " in text
    assert "Cost: total " in text
    for _, item in plan.iter_items():
        assert item.id in text and item.name in text

def test_detail_carries_price_rating_and_alternatives(plan_with_meal_alternatives):
    text = plan_detail(plan_with_meal_alternatives)
    assert "rating 4.4" in text
    assert "other choices: " in text

def test_detail_never_carries_urls_or_none(plan):
    text = plan_detail(plan)
    assert "http" not in text and "None" not in text

def test_detail_without_stay_tickets_or_cost(bare_plan):       # hotels=[], tickets=[], default cost
    text = plan_detail(bare_plan)
    assert "Stay:" not in text and "Transport:" not in text and "Cost:" not in text
    assert "Day 1 " in text

def test_detail_for_chosen_days_only(plan):
    text = plan_detail(plan, days=[2, 9])
    assert "Day 2 " in text and "Day 1 " not in text and "Stay: " in text

def test_detail_without_ids(plan):
    assert next(plan.iter_items())[1].id not in plan_detail(plan, ids=False)

def test_detail_strips_fence_tags_and_line_breaks(plan):       # rename one item to f"Nice{FENCE_OPEN}\nplace" using the tag constants in app.security
    text = plan_detail(plan)
    assert FENCE_OPEN not in text
    assert "Nice" in text and "\nplace" not in text

def test_detail_fits_the_cap_and_keeps_every_day_and_stay(long_plan):   # 30 days, 4 items a day, 3 alternatives per meal, long names
    text = plan_detail(long_plan)
    assert len(text) <= PLAN_DETAIL_CHARS
    assert all(f"Day {n} " in text for n in range(1, 31))
    assert text.count("Stay: ") == len(long_plan.hotels)

def test_detail_drops_alternatives_first(plan_with_meal_alternatives):
    full = plan_detail(plan_with_meal_alternatives)
    tight = plan_detail(plan_with_meal_alternatives, cap=len(full) - 1)
    assert "other choices: " not in tight and "rating 4.4" in tight
```

- [ ] **Step 2:** Run `PYTHONPATH=. python -m pytest tests/test_plan_detail.py -q`. Expected: FAIL, `plan_detail` cannot be imported.
- [ ] **Step 3:** Implement `PLAN_DETAIL_CHARS` and `plan_detail` in `prompt_context.py` to the format and fitting rule above. Read the plan object directly (not `summary_for_prompt`). Update the module docstring: the detail is the one block that is not compact, and when it is sent.
- [ ] **Step 4:** Run the same command. Expected: PASS. Run `ruff check app/application/prompt_context.py tests/test_plan_detail.py`. Expected: clean.
- [ ] **Step 5:** Commit: `The whole plan can be written out for a prompt with its stay, transport, prices, ratings and other choices, cut to fit without losing a day`.

### Task 2: `focus_day` and `plan_context`

**Files:**
- Create: `backend/app/application/plan_focus.py`
- Modify: `backend/app/application/prompt_context.py`, `backend/app/application/modification.py` (`_rule_day`, line ~1058), `backend/app/application/asking.py` (`_named_date`, line ~591)
- Test: `backend/tests/test_plan_focus.py`

**Interfaces:**
- Consumes: `plan_detail`, `plan_outline` from Task 1 / existing.
- Produces:
  - `plan_focus.day_number_named(message: str) -> int | None`: the body of today's `modification._rule_day`, moved. `modification._rule_day = day_number_named` stays as an alias so its callers and tests are untouched.
  - `plan_focus.date_named(question: str, plan: TripPlan | None) -> date | None`: today's `asking._named_date`, moved with any helper only it uses; `asking._named_date` stays as an alias.
  - `plan_focus.focus_day(plan: TripPlan, message: str, scope_item_id: str | None) -> int | None`: 1-based day number, in this order: the day of the selected stop when `plan.find_item(scope_item_id)` finds it; else `day_number_named(message)` when `1 <= n <= len(plan.days)`; else the index of `date_named(message, plan)` when that date is one of the plan's days; else `None`. A selected stay (`plan.find_hotel`) gives `None`.
  - `prompt_context.plan_context(plan: TripPlan, *, focus: int | None, selected_id: str | None = None, ids: bool = True) -> str`:
    - `focus is None`: `plan_detail(plan, ids=ids)`, followed by the selected line below when `selected_id` names a stay.
    - otherwise: `plan_outline(plan.summary_for_prompt(), ids=ids)` + `"\n\nDay {focus} in detail:\n"` + `plan_detail(plan, days=[focus], ids=ids)`, followed by the selected line when `selected_id` is in the plan.
    - Selected line, exact text: `Selected by the traveller: {id} {name}` plus ` (Day {n})` for a stop. Nothing is written for an id the plan does not hold.

`plan_focus` must not import `modification` or `asking` (they import it).

- [ ] **Step 1: Write the failing tests** in `test_plan_focus.py`:

```python
def test_no_selection_and_no_day_is_no_focus(plan):
    assert focus_day(plan, "make the trip more relaxed", None) is None

def test_selected_stop_gives_its_day(plan):          # pick an item from day 2
    assert focus_day(plan, "swap this", day2_item.id) == 2

def test_selected_stop_wins_over_a_named_day(plan):
    assert focus_day(plan, "move it to day 1", day2_item.id) == 2

def test_named_day_number(plan):
    assert focus_day(plan, "what is on day 2?", None) == 2
    assert focus_day(plan, "the second day is too full", None) == 2

def test_named_date_in_the_trip(plan):                # message carries plan.days[1].date written the way asking's tests write dates
    assert focus_day(plan, message_with_day2_date, None) == 2

def test_day_outside_the_trip_is_no_focus(plan):
    assert focus_day(plan, "what about day 9?", None) is None

def test_selection_gone_from_the_plan_is_no_focus(plan):
    assert focus_day(plan, "change this", "item_gone") is None

def test_selected_stay_is_no_focus(plan):
    assert focus_day(plan, "is this near the station?", plan.hotels[0].id) is None

def test_context_without_focus_is_the_full_detail(plan):
    assert plan_context(plan, focus=None) == plan_detail(plan)

def test_context_with_focus_is_outline_plus_that_day(plan):
    text = plan_context(plan, focus=2, selected_id=day2_item.id)
    assert text.startswith(plan_outline(plan.summary_for_prompt(), ids=True))
    assert "Day 2 in detail:" in text
    assert text.endswith(f"Selected by the traveller: {day2_item.id} {day2_item.name} (Day 2)")
    assert text.count("Stay: ") == 1

def test_context_names_a_selected_stay(plan):
    text = plan_context(plan, focus=None, selected_id=plan.hotels[0].id)
    assert text.endswith(f"Selected by the traveller: {plan.hotels[0].id} {plan.hotels[0].name}")

def test_context_ignores_a_selection_not_in_the_plan(plan):
    assert "Selected by the traveller" not in plan_context(plan, focus=None, selected_id="item_gone")
```

- [ ] **Step 2:** Run `PYTHONPATH=. python -m pytest tests/test_plan_focus.py -q`. Expected: FAIL on import.
- [ ] **Step 3:** Create `plan_focus.py`, move the two functions, leave the aliases, add `plan_context`.
- [ ] **Step 4:** Run `PYTHONPATH=. python -m pytest tests/test_plan_focus.py tests/test_asking.py tests/test_modify_targets.py -q`. Expected: PASS (the moved functions behave as before). `ruff check` clean.
- [ ] **Step 5:** Commit: `A message's day is worked out in one place, from the selected stop, a day number or a date, and the plan block for a prompt follows it`.

### Task 3: The edit prompt

**Files:**
- Modify: `backend/app/application/modification.py` (`_extract` at ~1000 and its call at ~236), `backend/app/prompts/modify.md`
- Test: `backend/tests/test_modify_plan_context.py`

**Interfaces:**
- Consumes: `focus_day`, `plan_context`.
- Produces: `_extract(self, message, plan, context, route_parts, *, history=(), scope_item_id: str | None = None)`. The call site passes the `scope_item_id` the turn arrived with (before the reassignment near line 302).

The user text becomes `f"Current plan:\n{plan_context(plan, focus=focus_day(plan, message, scope_item_id), selected_id=scope_item_id)}\n"` followed by the unchanged `Trip:` line, conversation block and fenced message.

Add to `modify.md`, after its description of the plan input: "The current plan is given in full (stay, transport, each day's stops with prices and ratings, and cost) when the traveller selected nothing and named no day: weigh the whole trip. When one day is given in detail under an outline of the rest, the request is about that day. A line starting `Selected by the traveller:` names the stop or stay they picked before typing; "this", "it" and "here" mean that one."

- [ ] **Step 1: Write the failing tests.** Capture the prompt the same way `tests/test_conversation_context.py` captures the modify prompt (a gateway double recording `user`):

```python
async def test_edit_with_nothing_selected_sends_the_full_plan(...):
    await modify("make the whole trip cheaper", scope_item_id=None)
    assert "Stay: " in captured.user and "Cost: total " in captured.user
    assert all(f"Day {n} " in captured.user for n in range(1, len(plan.days) + 1))
    assert "in detail:" not in captured.user

async def test_edit_naming_a_day_sends_outline_plus_that_day(...):
    await modify("day 2 is too busy", scope_item_id=None)
    assert "Day 2 in detail:" in captured.user

async def test_edit_with_a_selected_stop_names_it(...):
    await modify("swap this for something indoors", scope_item_id=day1_item.id)
    assert "Day 1 in detail:" in captured.user
    assert f"Selected by the traveller: {day1_item.id} {day1_item.name} (Day 1)" in captured.user

async def test_edit_prompt_has_no_urls(...):
    assert "http" not in captured.user
```

- [ ] **Step 2:** Run `PYTHONPATH=. python -m pytest tests/test_modify_plan_context.py -q`. Expected: FAIL (no `Stay:` in the prompt).
- [ ] **Step 3:** Change `_extract`, its call, and `modify.md`.
- [ ] **Step 4:** Run `PYTHONPATH=. python -m pytest tests -q -k "modify or edit or conversation_context or prompt"`. Expected: PASS. Where an existing test asserts the old outline-only edit prompt, change its expectation to the new block and say which tests in the report; do not weaken an assertion about fencing, canaries or conversation limits.
- [ ] **Step 5:** Commit: `An edit with nothing selected and no day named is read against the whole plan, and a selected stop is named to the model`.

### Task 4: The answer prompt

**Files:**
- Modify: `backend/app/application/asking.py` (`ask_prompt` at ~325 and its call at ~312), the ask prompt file under `backend/app/prompts/`
- Test: `backend/tests/test_ask_plan_context.py`

**Interfaces:**
- Consumes: `focus_day`, `plan_context`.
- Produces: `ask_prompt(question, plan, facts, source, conversation="", *, scope_item_id: str | None = None) -> str`.

Behaviour: with a plan, `Plan:` is followed by `plan_context(plan, focus=focus_day(plan, question, scope_item_id), selected_id=scope_item_id, ids=False)`. The rule that cut the outline to its header when `"itinerary" in facts` goes; in its place, the copy of `facts` written into the prompt leaves out the `itinerary` key whenever a plan is present (the plan block now holds those rows). The `facts` dict itself is not changed, so `_fallback_answer` is unaffected. The `[:12000]` cut stays. With no plan, `Plan: none` as today.

Add to the ask prompt file the same paragraph as Task 3, worded for answering ("answer from the whole trip" / "the question is about that day").

- [ ] **Step 1: Write the failing tests:**

```python
def test_question_with_nothing_selected_gets_the_full_plan(plan):
    text = ask_prompt("is this trip too rushed?", plan, {}, "plan")
    assert "Stay: " in text and all(f"Day {n} " in text for n in range(1, len(plan.days) + 1))

def test_question_about_a_day_gets_that_day_in_detail(plan):
    assert "Day 2 in detail:" in ask_prompt("what do we eat on day 2?", plan, {}, "plan")

def test_question_with_a_selected_stop_names_it(plan):
    text = ask_prompt("is this open on Mondays?", plan, {}, "plan", scope_item_id=day1_item.id)
    assert f"Selected by the traveller: {day1_item.name} (Day 1)" in text   # ids=False: no id in the line

def test_itinerary_rows_are_not_sent_twice(plan):
    facts = {"itinerary": [{"x": "ROW_MARK"}], "cost": {"total": 1}}
    text = ask_prompt("what is the total?", plan, facts, "plan")
    assert "ROW_MARK" not in text and '"cost"' in text
    assert "itinerary" in facts

def test_no_plan_is_unchanged():
    assert ask_prompt("where should I go?", None, {}, "none").startswith("Plan: none\n")
```

  For `ids=False` the selected line is `Selected by the traveller: {name}` (plus the day); add `test_context_selected_line_without_ids` to `test_plan_focus.py` asserting exactly that, and make `plan_context` follow it.

- [ ] **Step 2:** Run `PYTHONPATH=. python -m pytest tests/test_ask_plan_context.py -q`. Expected: FAIL.
- [ ] **Step 3:** Change `ask_prompt`, pass `scope_item_id` at its call site, edit the prompt file.
- [ ] **Step 4:** Run `PYTHONPATH=. python -m pytest tests -q -k "ask or asking or conversation_context"`. Expected: PASS, with old outline expectations updated as in Task 3.
- [ ] **Step 5:** Commit: `A question with nothing selected and no day named is answered from the whole plan`.

### Task 5: Whole-suite check

- [ ] **Step 1:** Run `PYTHONPATH=. python -m pytest -q`. Expected: no failing test that was not in the Task 0 baseline. Any test that measures prompt size for the router must be unchanged; one that measures the edit or answer prompt may need its limit raised to cover the 24 000-character block, and the report says which.
- [ ] **Step 2:** Run `ruff check`. Expected: clean (or no new findings against the baseline).
- [ ] **Step 3:** Report the two commands and their final output lines.
