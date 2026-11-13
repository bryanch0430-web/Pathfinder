import assert from "node:assert/strict";
import test from "node:test";

import {
  chosenFor,
  daysBySegment,
  legHome,
  legInto,
  legsOf,
  nightsBetween,
  optionsFor,
  routeLabel,
  segmentOfDay,
  segmentsOf,
  stayOn,
  staysIn,
} from "./segments.ts";

const place = (name) => ({ name, country_code: "JP", geo: null, timezone: "" });
const stay = (id, check_in, check_out, segment_id) => ({ id, name: id, check_in, check_out, segment_id });
const ticket = (id, leg_id, selected = false) => ({ id, name: id, leg_id, selected });
const day = (date, segment_id) => ({ date, title: "", items: [], segment_id });

/** Kyoto 3-5 December, Osaka 5-7 December: five days, a stay and a train in between. */
function twoCities() {
  return {
    destination: place("Kyoto"),
    origin: "Hong Kong",
    dates: { start: "2026-12-03", end: "2026-12-07" },
    segments: [
      { id: "seg_a", destination: place("Kyoto"), dates: { start: "2026-12-03", end: "2026-12-05" } },
      { id: "seg_b", destination: place("Osaka"), dates: { start: "2026-12-05", end: "2026-12-07" } },
    ],
    days: [
      day("2026-12-03", "seg_a"),
      day("2026-12-04", "seg_a"),
      day("2026-12-05", "seg_b"),
      day("2026-12-06", "seg_b"),
      day("2026-12-07", "seg_b"),
    ],
    hotels: [stay("hotel_a", "2026-12-03", "2026-12-05", "seg_a"), stay("hotel_b", "2026-12-05", "2026-12-07", "seg_b")],
    legs: [
      { id: "leg_out", from_place: "Hong Kong", to_place: "Kyoto", depart_date: "2026-12-03", kind: "outbound" },
      { id: "leg_ab", from_place: "Kyoto", to_place: "Osaka", depart_date: "2026-12-05", kind: "between" },
      { id: "leg_home", from_place: "Osaka", to_place: "Hong Kong", depart_date: "2026-12-07", kind: "return" },
    ],
    tickets: [
      ticket("t_out_1", "leg_out", true),
      ticket("t_out_2", "leg_out"),
      ticket("t_ab_train", "leg_ab", true),
      ticket("t_ab_bus", "leg_ab"),
      ticket("t_home_1", "leg_home"),
    ],
  };
}

/** A plan stored before cities and legs existed. */
function oldPlan() {
  return {
    destination: place("Kyoto"),
    origin: "Hong Kong",
    dates: { start: "2026-12-03", end: "2026-12-04" },
    days: [day("2026-12-03"), day("2026-12-04")],
    hotels: [stay("hotel_1", "2026-11-01", "2026-11-02")],
    tickets: [ticket("t1", undefined, false), ticket("t2", undefined, true)],
  };
}

test("a plan with no cities is one city over the trip's dates", () => {
  const plan = oldPlan();
  assert.deepEqual(
    segmentsOf(plan).map((s) => [s.id, s.destination.name, s.dates.start, s.dates.end]),
    [["seg_1", "Kyoto", "2026-12-03", "2026-12-04"]],
  );
  assert.equal(routeLabel(plan), "Kyoto");
  assert.equal(segmentOfDay(plan, "2031-01-01").id, "seg_1");
});

test("the day two cities share belongs to the city arrived in", () => {
  const plan = twoCities();
  assert.deepEqual(
    plan.days.map((d) => segmentOfDay(plan, d.date).id),
    ["seg_a", "seg_a", "seg_b", "seg_b", "seg_b"],
  );
  assert.equal(routeLabel(plan), "Kyoto → Osaka");
});

test("days are grouped by city, using the date when a day names none", () => {
  const plan = twoCities();
  for (const d of plan.days) delete d.segment_id;
  const groups = daysBySegment(plan);
  assert.deepEqual(
    groups.map((g) => [g.segment.id, g.days.length]),
    [
      ["seg_a", 2],
      ["seg_b", 3],
    ],
  );
  assert.equal(daysBySegment(oldPlan())[0].days.length, 2);
});

test("the stay of each night and of the last morning", () => {
  const plan = twoCities();
  assert.deepEqual(
    plan.days.map((d) => stayOn(plan, d.date)?.id),
    ["hotel_a", "hotel_a", "hotel_b", "hotel_b", "hotel_b"],
  );
  assert.deepEqual(
    staysIn(plan, "seg_b").map((s) => s.id),
    ["hotel_b"],
  );
  assert.deepEqual(staysIn(plan, "seg_none"), []);
});

test("a night with no stay has none, and a lone stay with drifted dates is still the stay", () => {
  const plan = twoCities();
  plan.hotels = plan.hotels.slice(0, 1);
  assert.equal(stayOn(plan, "2026-12-06"), null);
  assert.equal(stayOn(plan, "2026-12-03")?.id, "hotel_a");
  const old = oldPlan();
  assert.equal(stayOn(old, "2026-12-03")?.id, "hotel_1");
});

test("a stay without a city belongs to the city of its check-in", () => {
  const plan = twoCities();
  for (const s of plan.hotels) delete s.segment_id;
  assert.deepEqual(
    staysIn(plan, "seg_a").map((s) => s.id),
    ["hotel_a"],
  );
});

test("the options of a leg and the one chosen", () => {
  const plan = twoCities();
  assert.deepEqual(
    optionsFor(plan, "leg_ab").map((t) => t.id),
    ["t_ab_train", "t_ab_bus"],
  );
  assert.equal(chosenFor(plan, "leg_ab")?.id, "t_ab_train");
  assert.equal(chosenFor(plan, "leg_home")?.id, "t_home_1");
  assert.equal(chosenFor(plan, "leg_none"), undefined);
});

test("a plan from before legs has one outbound leg whose options are every ticket", () => {
  const plan = oldPlan();
  assert.deepEqual(
    legsOf(plan).map((l) => [l.id, l.kind]),
    [["leg_out", "outbound"]],
  );
  assert.deepEqual(
    optionsFor(plan, "leg_out").map((t) => t.id),
    ["t1", "t2"],
  );
  assert.equal(chosenFor(plan, "leg_out")?.id, "t2");
  assert.deepEqual(legsOf({ ...oldPlan(), tickets: [] }), []);
});

test("the leg into a city and the leg home", () => {
  const plan = twoCities();
  assert.equal(legInto(plan, "seg_a")?.id, "leg_out");
  assert.equal(legInto(plan, "seg_b")?.id, "leg_ab");
  assert.equal(legInto(plan, "nope"), undefined);
  assert.equal(legHome(plan)?.id, "leg_home");
  assert.equal(legHome(oldPlan()), undefined);
});

test("nights between two dates", () => {
  assert.equal(nightsBetween("2026-12-03", "2026-12-05"), 2);
  assert.equal(nightsBetween("2026-12-05", "2026-12-03"), 0);
  assert.equal(nightsBetween("nonsense", "2026-12-03"), 0);
});
