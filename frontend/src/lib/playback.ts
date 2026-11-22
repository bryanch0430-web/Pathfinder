/** Playback clock math, kept pure so the timing contract is testable without React.
 *
 * The historical mapping is one tick of 5 minutes every 275 ms at 1x; the rAF
 * loop preserves that wall-clock rate (600 minutes at 1x = 33 s) while advancing
 * by real elapsed time instead of a fixed interval.
 */

export const PLAYBACK_MINUTES_PER_TICK = 5;
export const PLAYBACK_TICK_MS = 275;
/** A background tab must not teleport the token on return. */
export const MAX_FRAME_MS = 250;

export function minutesPerMs(speed: number): number {
  return (Math.max(0, speed) * PLAYBACK_MINUTES_PER_TICK) / PLAYBACK_TICK_MS;
}

/** Wall-clock duration of `minutes` of playback at `speed`. */
export function playbackDurationMs(minutes: number, speed: number): number {
  const rate = minutesPerMs(speed);
  return rate > 0 ? (minutes / rate) : Number.POSITIVE_INFINITY;
}

/** Advance the clock by a frame delta, clamped and capped at the end of the day. */
export function advanceClock(clock: number, elapsedMs: number, speed: number, max: number): number {
  const delta = Math.min(MAX_FRAME_MS, Math.max(0, elapsedMs));
  return Math.min(max, clock + delta * minutesPerMs(speed));
}
