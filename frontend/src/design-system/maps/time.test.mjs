import assert from "node:assert/strict";
import test from "node:test";

import { clockText } from "./time.ts";

test("playback labels are whole minutes, even with fractional clock values", () => {
  assert.equal(clockText(9 * 60), "09:00");
  assert.equal(clockText(17 * 60 + 29.4), "17:29");
  assert.equal(clockText(0.9), "00:00");
  assert.equal(clockText(1439.99), "23:59");
});
