import assert from "node:assert/strict";
import test from "node:test";

import { budgetShown, budgetStored, endBounds, endOpensOnStart, phraseDates } from "./clarifyInput.ts";

test("a money chip stores the digits, and plan anyway stores the sentence", () => {
  assert.equal(budgetStored("HKD 15,000"), "15000");
  assert.equal(budgetStored("Keep HKD 1 and plan anyway"), "Keep HKD 1 and plan anyway");
  assert.equal(budgetShown("15000"), "15000");
  assert.equal(budgetShown("Keep HKD 1 and plan anyway"), "");
});

test("the end calendar starts on the start day and stops 30 days later", () => {
  assert.deepEqual(endBounds("2026-11-21"), { min: "2026-11-21", max: "2026-12-20" });
  assert.equal(endOpensOnStart("2026-11-21", ""), true);
  assert.equal(endOpensOnStart("2026-11-21", "2026-11-19"), true);
  assert.equal(endOpensOnStart("2026-11-21", "2026-11-25"), false);
});

test("date chips fill a start and an end", () => {
  const saturday = new Date(2026, 9, 31);
  assert.deepEqual(phraseDates("This weekend", saturday), { start: "2026-10-31", end: "2026-11-01" });
  assert.deepEqual(phraseDates("Next month", saturday), { start: "2026-11-01", end: "2026-11-07" });
  assert.deepEqual(phraseDates("In three months", saturday), { start: "2027-01-31", end: "2027-02-06" });

  const sunday = new Date(2026, 10, 22);
  assert.deepEqual(phraseDates("This weekend", sunday), { start: "2026-11-22", end: "2026-11-23" });
  assert.deepEqual(phraseDates("Next month", sunday), { start: "2026-12-01", end: "2026-12-07" });
  assert.deepEqual(phraseDates("In three months", sunday), { start: "2027-02-22", end: "2027-02-28" });
  assert.equal(phraseDates("Hong Kong", sunday), null);
});
