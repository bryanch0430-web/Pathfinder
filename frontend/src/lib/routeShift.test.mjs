import assert from "node:assert/strict";
import test from "node:test";

import { choiceInto, shiftLaterStops } from "./routeShift.ts";

const park = { id: "park", type: "attraction", start: "08:00", end: "09:30" };
const zoo = { id: "zoo", type: "attraction", start: "09:55", end: "11:25" };
const lunch = { id: "lunch", type: "meal", start: "12:30", end: "13:30" };
const taxi = { id: "taxi", type: "transit", start: "01:30", end: "02:23" };

test("a shorter hop moves the destination and later stops, and leaves the taxi", () => {
  const moves = shiftLaterStops([taxi, park, zoo, lunch], "zoo", -40, 2);
  assert.deepEqual(moves, [
    { id: "zoo", day: 2, start: "09:15", end: "10:45" },
    { id: "lunch", day: 2, start: "11:50", end: "12:50" },
  ]);
});

test("a locked stop and a zero change stay where they are", () => {
  assert.deepEqual(shiftLaterStops([{ ...zoo, locked: true }, lunch], "zoo", -40, 1), [
    { id: "lunch", day: 1, start: "11:50", end: "12:50" },
  ]);
  assert.deepEqual(shiftLaterStops([zoo, lunch], "zoo", 0, 1), []);
  assert.deepEqual(shiftLaterStops([zoo], "missing", -10, 1), []);
});

test("a saved choice is the hop that ends at that stop", () => {
  const picked = { mode: "transit", minutes: 12, km: 3.98, path: [], steps: [], source: "google-routes" };
  assert.equal(choiceInto({ "park|zoo": picked }, "zoo"), picked);
  assert.equal(choiceInto({ "park|zoo": picked }, "park"), undefined);
});
