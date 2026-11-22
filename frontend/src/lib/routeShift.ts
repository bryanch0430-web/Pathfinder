/** A hop the traveller picked on the map. Times on the plan are saved separately. */
export type RouteChoice = {
  mode: string;
  minutes: number;
  km: number | null;
  path: [number, number][];
  steps: { mode: string; minutes: number | null; path: [number, number][]; instruction: string }[];
  source: string;
};

function minutesOf(value?: string | null): number | null {
  if (!value) return null;
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function clockText(minutes: number): string {
  const whole = Math.max(0, Math.min(23 * 60 + 59, Math.floor(minutes)));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

export type RouteMove = { id: string; day: number; start: string; end: string };

const FIXED = new Set(["transit", "ticket", "hotel"]);

function clamp(minutes: number): number {
  return Math.max(0, Math.min(23 * 60 + 59, minutes));
}

/**
 * Move the destination stop and every later stop by `deltaMinutes`.
 * `day` is 1-based, matching the layout save. Transit, tickets, hotels, and locked stops stay put.
 */
export function shiftLaterStops(
  items: { id: string; type: string; start: string; end: string; locked?: boolean; confirmed?: boolean }[],
  toId: string,
  deltaMinutes: number,
  day: number,
): RouteMove[] {
  if (!deltaMinutes || !toId) return [];
  const anchor = items.find((item) => item.id === toId);
  const at = minutesOf(anchor?.start);
  if (at === null) return [];
  const moves: RouteMove[] = [];
  for (const item of items) {
    const start = minutesOf(item.start);
    const end = minutesOf(item.end);
    if (start === null || end === null || start < at) continue;
    if (FIXED.has(item.type) || item.locked || item.confirmed) continue;
    const nextStart = clamp(start + deltaMinutes);
    const nextEnd = clamp(end + deltaMinutes);
    if (nextStart >= nextEnd) continue;
    const startText = clockText(nextStart);
    const endText = clockText(nextEnd);
    if (startText === item.start && endText === item.end) continue;
    moves.push({ id: item.id, day, start: startText, end: endText });
  }
  return moves;
}

const storeKey = (tripId: string) => `pf-route:${tripId}`;

export function readRouteChoices(tripId: string, version: number): Record<string, RouteChoice> {
  if (!tripId || typeof sessionStorage === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(storeKey(tripId));
    if (!raw) return {};
    const saved = JSON.parse(raw) as { version?: number; choices?: Record<string, RouteChoice> };
    return saved.version === version ? saved.choices ?? {} : {};
  } catch {
    return {};
  }
}

export function writeRouteChoices(tripId: string, version: number, choices: Record<string, RouteChoice>): void {
  if (!tripId || typeof sessionStorage === "undefined") return;
  sessionStorage.setItem(storeKey(tripId), JSON.stringify({ version, choices }));
}

/** The hop whose destination is this stop, when the traveller has picked a mode for it. */
export function choiceInto(choices: Record<string, RouteChoice>, itemId: string): RouteChoice | undefined {
  for (const [key, choice] of Object.entries(choices)) {
    const split = key.lastIndexOf("|");
    if (split >= 0 && key.slice(split + 1) === itemId) return choice;
  }
  return undefined;
}
