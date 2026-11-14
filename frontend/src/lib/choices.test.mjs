import assert from "node:assert/strict";
import test from "node:test";

import { openCount, slotCount, slotState, transportKinds } from "./choices.ts";
import { legsOf, optionsFor, optionsOfStay, transferInto, transferLegsOf, transferOutOf } from "./segments.ts";

const place = (name) => ({ name, country_code: "JP", geo: null, timezone: "" });
const stay = (id, check_in, check_out, segment_id, more = {}) => ({ id, name: id, check_in, check_out, segment_id, ...more });
const ticket = (id, leg_id, mode, selected = false) => ({ id, name: id, leg_id, mode, selected });
const leg = (id, kind, from_place, to_place, more = {}) => ({ id, kind, from_place, to_place, depart_date: "2026-12-03", ...more });

/** Kyoto 3-5 December then Osaka 5-7: a flight in with an airport transfer, a train between, no way home options. */
function plan(more = {}) {
  return {
    destination: place("Kyoto"),
    origin: "Hong Kong",
    dates: { start: "2026-12-03", end: "2026-12-07" },
    segments: [
      { id: "seg_a", destination: place("Kyoto"), dates: { start: "2026-12-03", end: "2026-12-05" } },
      { id: "seg_b", destination: place("Osaka"), dates: { start: "2026-12-05", end: "2026-12-07" } },
    ],
    days: [],
    hotels: [stay("hotel_a", "2026-12-03", "2026-12-05", "seg_a"), stay("hotel_b", "2026-12-05", "2026-12-07", "seg_b")],
    hotel_options: [
      stay("hotel_a", "2026-12-03", "2026-12-05", "seg_a"),
      stay("hotel_a2", "2026-12-03", "2026-12-05", "seg_a"),
      stay("hotel_b", "2026-12-05", "2026-12-07", "seg_b"),
    ],
    legs: [
      leg("leg_out", "outbound", "Hong Kong", "Kyoto"),
      leg("leg_ab", "between", "Kyoto", "Osaka"),
      leg("to_stay_seg_a", "to_stay", "KIX airport", "hotel_a", { segment_id: "seg_a", airport: "KIX" }),
      leg("from_stay_seg_b", "from_stay", "hotel_b", "KIX airport", { segment_id: "seg_b", airport: "KIX" }),
    ],
    tickets: [
      ticket("t_out_1", "leg_out", "flight", true),
      ticket("t_out_2", "leg_out", "flight"),
      ticket("t_ab_train", "leg_ab", "train", true),
      ticket("t_ab_bus", "leg_ab", "bus"),
      ticket("x_in_taxi", "to_stay_seg_a", "taxi", true),
      ticket("x_in_bus", "to_stay_seg_a", "bus"),
      ticket("x_out_taxi", "from_stay_seg_b", "taxi", true),
    ],
    ...more,
  };
}

test("slotState: locked wins, then none, one and several options", () => {
  assert.equal(slotState(true, 5), "locked");
  assert.equal(slotState(true, 0), "locked");
  assert.equal(slotState(false, 0), "empty");
  assert.equal(slotState(false, 1), "single");
  assert.equal(slotState(false, 2), "open");
  assert.equal(slotState(false, 7), "open");
});

test("transportKinds groups by kind in a fixed order and leaves empty kinds out", () => {
  const options = [
    ticket("a", "l", "taxi"),
    ticket("b", "l", "ground"),
    ticket("c", "l", "flight"),
    ticket("d", "l", "shuttle"),
    ticket("e", "l", "drive"),
    ticket("f", "l", "train"),
    ticket("g", "l", "ferry"),
    ticket("h", "l", "ticket"),
  ];

  const kinds = transportKinds(options);

  assert.deepEqual(
    kinds.map((group) => group.kind),
    ["flight", "train", "bus", "drive", "taxi"],
  );
  assert.deepEqual(kinds.find((group) => group.kind === "bus").options.map((o) => o.id), ["b", "d", "g", "h"]);
  assert.deepEqual(transportKinds([ticket("a", "l", "train")]).map((g) => g.kind), ["train"]);
  assert.deepEqual(transportKinds([]), []);
});

test("legsOf holds the hops only; transfers are found by city and kind", () => {
  const p = plan();

  assert.deepEqual(legsOf(p).map((l) => l.id), ["leg_out", "leg_ab"]);
  assert.deepEqual(transferLegsOf(p).map((l) => l.id), ["to_stay_seg_a", "from_stay_seg_b"]);
  assert.equal(transferInto(p, "seg_a").id, "to_stay_seg_a");
  assert.equal(transferInto(p, "seg_b"), undefined);
  assert.equal(transferOutOf(p, "seg_b").id, "from_stay_seg_b");
  assert.equal(transferOutOf(p, "seg_a"), undefined);
});

test("a transfer's options are its own and never belong to the first hop", () => {
  const p = plan();

  assert.deepEqual(optionsFor(p, "to_stay_seg_a").map((o) => o.id), ["x_in_taxi", "x_in_bus"]);
  assert.deepEqual(optionsFor(p, "leg_out").map((o) => o.id), ["t_out_1", "t_out_2"]);
});

test("optionsOfStay leads with the stay and lists its own city's options only", () => {
  const p = plan();

  assert.deepEqual(optionsOfStay(p, p.hotels[0]).map((s) => s.id), ["hotel_a", "hotel_a2"]);
  assert.deepEqual(optionsOfStay(p, p.hotels[1]).map((s) => s.id), ["hotel_b"]);
  assert.deepEqual(optionsOfStay({ ...p, hotel_options: undefined }, p.hotels[0]).map((s) => s.id), ["hotel_a"]);
});

test("openCount and slotCount count stays and legs with two or more options, transfers included", () => {
  const p = plan();

  // Kyoto stay (2 options), leg_out (2), leg_ab (2), to_stay_seg_a (2); Osaka stay and the transfer out have one.
  assert.equal(slotCount(p), 4);
  assert.equal(openCount(p), 4);

  const locked = plan({
    hotels: [stay("hotel_a", "2026-12-03", "2026-12-05", "seg_a", { locked: true }), plan().hotels[1]],
    legs: plan().legs.map((l) => (l.id === "to_stay_seg_a" || l.id === "leg_ab" ? { ...l, locked: true } : l)),
  });
  assert.equal(slotCount(locked), 4);
  assert.equal(openCount(locked), 1);
});

test("a plan with no legs and no options has nothing to decide, and an old plan with tickets has one leg", () => {
  assert.equal(slotCount({ ...plan(), hotels: [], hotel_options: [], legs: undefined, tickets: [] }), 0);
  const old = { ...plan(), hotels: [], hotel_options: [], legs: undefined, tickets: [ticket("f1", undefined, "flight", true), ticket("f2", undefined, "flight")] };
  assert.equal(slotCount(old), 1);
  assert.equal(openCount(old), 1);
});
