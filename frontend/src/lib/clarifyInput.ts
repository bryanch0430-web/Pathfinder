/** A trip is at most this many days, counting the start and the end. */
export const MAX_TRIP_DAYS = 30;

/** The first number in a chip such as "HKD 15,000". No number is null. */
export function amountIn(value: string): number | null {
  const match = value.replaceAll(",", "").match(/-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const amount = Number(match[0]);
  return Number.isFinite(amount) ? amount : null;
}

/** "Keep HKD 1 and plan anyway" is a sentence, not an amount. */
export function keepsPlanAnyway(value: string): boolean {
  return /\bplan anyway\b/i.test(value);
}

/** What a budget chip stores: the sentence for "plan anyway", otherwise the digits. */
export function budgetStored(option: string): string {
  if (keepsPlanAnyway(option)) return option;
  const amount = amountIn(option);
  return amount === null ? option : String(amount);
}

/** What a number field can show. The "plan anyway" sentence stays in the stored value. */
export function budgetShown(value: string): string {
  if (!value || keepsPlanAnyway(value)) return "";
  const amount = amountIn(value);
  return amount === null || amount < 0 ? "" : String(amount);
}

export function dayCount(start: string, end: string): number | null {
  if (!start || !end) return null;
  const from = Date.parse(`${start}T12:00:00Z`);
  const to = Date.parse(`${end}T12:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return null;
  return Math.round((to - from) / 86_400_000) + 1;
}

function addDays(iso: string, days: number): string {
  const parsed = Date.parse(`${iso}T12:00:00Z`);
  if (Number.isNaN(parsed)) return "";
  return new Date(parsed + days * 86_400_000).toISOString().slice(0, 10);
}

/** Dates the end calendar can accept: the start day through 30 days later. */
export function endBounds(start: string): { min: string; max: string } | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start)) return null;
  const max = addDays(start, MAX_TRIP_DAYS - 1);
  return max ? { min: start, max } : null;
}

/** The end calendar should open on the start's month when the end is empty or cannot be kept. */
export function endOpensOnStart(start: string, end: string): boolean {
  if (!endBounds(start)) return false;
  const count = dayCount(start, end);
  return !end || count === null || count < 1 || count > MAX_TRIP_DAYS;
}
