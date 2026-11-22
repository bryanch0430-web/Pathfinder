import { useCallback, useEffect, useRef, useState } from "react";

import { advanceClock } from "./playback";

/**
 * The day-playback clock: one rAF loop, advanced by real elapsed time.
 *
 * `setClock` stays available for the day-reset and rollover paths, so the
 * component keeps owning playback policy while the loop owns time. React is
 * committed at ~30 Hz rather than every frame; the token easing in TripMap
 * smooths between commits.
 */
export function usePlaybackClock({
  playing,
  speed,
  max,
}: {
  playing: boolean;
  speed: number;
  max: number;
}) {
  const [clock, setClockState] = useState(0);
  const clockRef = useRef(0);

  const setClock = useCallback((value: number | ((current: number) => number)) => {
    setClockState((current) => {
      const next = typeof value === "function" ? value(current) : value;
      clockRef.current = next;
      return next;
    });
  }, []);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    let lastCommit = last;
    const step = (now: number) => {
      const delta = now - last;
      last = now;
      clockRef.current = advanceClock(clockRef.current, delta, speed, max);
      // ~30 Hz commits: enough for a smooth token, far fewer React renders than 60.
      if (now - lastCommit >= 33) {
        lastCommit = now;
        setClockState(clockRef.current);
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, max]);

  return { clock, setClock };
}
