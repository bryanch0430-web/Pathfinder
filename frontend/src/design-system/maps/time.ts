/** Playback clock helpers shared by the itinerary and the map. */

export const CLOCK_MIN = 6 * 60;
export const CLOCK_MAX = 23 * 60;
export const CLOCK_STEP = 15;

export function minutesOf(value?: string | null): number | null {
  if (!value) return null;
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

export function clockText(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  return `${String(hours).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
