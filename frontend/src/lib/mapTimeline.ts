import type { PlanItem } from "../design-system/types";

function minutes(value: string | null | undefined): number | null {
  if (!value || !value.includes(":")) return null;
  const [hour, minute] = value.split(":").map(Number);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  return hour * 60 + minute;
}

export interface TimelinePosition {
  active: PlanItem | null;
  from: PlanItem | null;
  to: PlanItem | null;
  /** 0 at from.end, 1 at to.start. */
  progress: number;
}

/**
 * Pure day-playback resolver. The token must sit at the active stop and only
 * interpolate across the real gap between `from.end` and `to.start`.
 * This is deliberately side-effect free so playback bugs are testable.
 */
export function timelineAt(items: PlanItem[], clock: number): TimelinePosition {
  const ordered = items
    .filter((item) => item.geo)
    .slice()
    .sort((a, b) => (minutes(a.start) ?? 0) - (minutes(b.start) ?? 0));

  const active =
    ordered.find((item) => {
      const start = minutes(item.start);
      const end = minutes(item.end);
      return start !== null && start <= clock && (end === null || end > clock);
    }) ?? null;
  if (active) return { active, from: active, to: null, progress: 0 };

  const previous = [...ordered].reverse().find((item) => (minutes(item.start) ?? 0) <= clock) ?? null;
  const next = ordered.find((item) => (minutes(item.start) ?? 0) > clock) ?? null;
  if (!previous || !next) {
    return { active: null, from: previous, to: next, progress: 0 };
  }
  const start = minutes(previous.end) ?? minutes(previous.start) ?? 0;
  const end = minutes(next.start) ?? start;
  const progress = end > start ? Math.max(0, Math.min(1, (clock - start) / (end - start))) : 0;
  return { active: null, from: previous, to: next, progress };
}
