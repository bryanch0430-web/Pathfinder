import assert from "node:assert/strict";
import test from "node:test";

import { budgetShown, budgetStored, endBounds, endOpensOnStart } from "./clarifyInput.ts";

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
