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

const WORD_COUNT: Record<string, number> = {
  a: 1,
  an: 1,
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
};

function localDay(today: Date): Date {
  return new Date(today.getFullYear(), today.getMonth(), today.getDate());
}

function isoOf(day: Date): string {
  const month = String(day.getMonth() + 1).padStart(2, "0");
  const date = String(day.getDate()).padStart(2, "0");
  return `${day.getFullYear()}-${month}-${date}`;
}

function shiftDays(day: Date, days: number): Date {
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  next.setDate(next.getDate() + days);
  return next;
}

/** Same month step as the planner: the day clamps to the last day of the target month. */
function monthsLater(day: Date, months: number): Date {
  const index = day.getMonth() + months;
  const year = day.getFullYear() + Math.floor(index / 12);
  const month = ((index % 12) + 12) % 12;
  const last = new Date(year, month + 1, 0).getDate();
  return new Date(year, month, Math.min(day.getDate(), last));
}

function countOf(raw: string): number {
  return WORD_COUNT[raw] ?? (raw.startsWith("0") || Number.isNaN(Number(raw)) ? 0 : Number(raw));
}

/**
 * The dates a clarify chip means. Weekend phrases match the planner (Saturday
 * through the next day; on a Sunday, "this weekend" starts today). A phrase
 * that only names a start becomes a 7-day trip so both fields fill and
 * Continue can enable. The end stays editable.
 * ponytail: the 7-day fill is a stand-in, not a duration the phrase stated.
 * Upgrade path: pass the trip's known day count in and use that as the end.
 */
export function phraseDates(phrase: string, today = new Date()): { start: string; end: string } | null {
  const lower = phrase.trim().toLowerCase();
  const day = localDay(today);
  const weekday = (day.getDay() + 6) % 7;

  if (/\bthis weekend\b/.test(lower)) {
    const ahead = weekday === 6 ? 0 : (5 - weekday + 7) % 7;
    const start = shiftDays(day, ahead);
    return { start: isoOf(start), end: isoOf(shiftDays(start, 1)) };
  }
  if (/\bnext weekend\b/.test(lower)) {
    const ahead = ((5 - weekday) % 7) + 7;
    const start = shiftDays(day, ahead);
    return { start: isoOf(start), end: isoOf(shiftDays(start, 1)) };
  }

  let start: Date | null = null;
  if (/\bnext month\b/.test(lower)) start = new Date(day.getFullYear(), day.getMonth() + 1, 1);
  else if (/\bnext week\b/.test(lower)) start = shiftDays(day, 7);
  else {
    const match =
      lower.match(/\bin\s+(one|two|three|four|five|six|seven|eight|nine|ten|a|an|\d{1,2})\s+(day|week|month|year)s?\b/) ??
      lower.match(/\b(one|two|three|four|five|six|seven|eight|nine|ten|a|an|\d{1,2})\s+(day|week|month|year)s?\s+from\s+now\b/);
    if (match) {
      const count = countOf(match[1]);
      const unit = match[2];
      if (count) {
        if (unit === "day") start = shiftDays(day, count);
        else if (unit === "week") start = shiftDays(day, 7 * count);
        else if (unit === "month") start = monthsLater(day, count);
        else start = monthsLater(day, 12 * count);
      }
    }
  }
  if (!start) return null;
  return { start: isoOf(start), end: isoOf(shiftDays(start, 6)) };
}
