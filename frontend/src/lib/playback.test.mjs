import assert from "node:assert/strict";
import test from "node:test";

import { advanceClock, playbackDurationMs, minutesPerMs } from "./playback.ts";

test("the 600-minute day keeps its 33 s wall-clock at 1x", () => {
  assert.equal(Math.round(playbackDurationMs(600, 1)), 33000);
  assert.equal(Math.round(playbackDurationMs(600, 2)), 16500);
  assert.equal(Math.round(playbackDurationMs(600, 4)), 8250);
  assert.ok(Math.abs(minutesPerMs(1) - 5 / 275) < 1e-12);
});

test("a stalled/background frame is clamped, not teleported", () => {
  const afterLongFrame = advanceClock(0, 60000, 1, 1440);
  assert.ok(afterLongFrame <= (250 * 5) / 275 + 1e-9);
});

test("advanceClock is linear in elapsed time and stops at the cap", () => {
  assert.ok(Math.abs(advanceClock(0, 100, 2, 1440) - (100 * 10) / 275) < 1e-9);
  assert.equal(advanceClock(1439.5, 250, 4, 1440), 1440);
  assert.equal(advanceClock(100, -50, 1, 1440), 100);
});
