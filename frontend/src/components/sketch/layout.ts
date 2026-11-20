/*
 * layoutSketch: trip document → sketchbook pages (an overview, then one page per day).
 *
 * Pure and deterministic: the same document always gives the same pages, ids and
 * wobble, so pages can be tested, diffed and animated element by element. Every word
 * on a page comes from the document; nothing is made up to fill space.
 */
import type { TripDocument, TripDocumentItem } from '../../services/tripService';
import { categoryDoodle, modeDoodle, type DoodleName } from './doodles';
import { PAGE, type Point, type SketchElement, type SketchPage } from './types';

const M = PAGE.margin;

// ---- text helpers ---------------------------------------------------------

const WIDE = /[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}\p{sc=Hangul}]/u;

const CAPS = /[A-Z]/, DIGIT = /[0-9]/;

/**
 * Width of handwritten text in ems, per character, measured on Caveat in the browser
 * (lowercase ~0.36, capitals and W/M up to 0.55, CJK 1.0) and rounded up, so text never overruns.
 */
export const textWidth = (text: string, size: number) =>
  [...text].reduce((sum, char) => sum + (WIDE.test(char) ? 1 : CAPS.test(char) ? 0.55 : DIGIT.test(char) ? 0.46 : 0.4), 0) * size;

/** Greedy word wrap to `maxLines`, ending in "…" when the text doesn't fit. */
export function wrap(text: string, width: number, size: number, maxLines: number): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let line = '';
  const fit = (value: string) => {
    let out = value;
    while (out.length > 1 && textWidth(`${out}…`, size) > width) out = out.slice(0, -1);
    return out;
  };
  for (let index = 0; index < words.length; index++) {
    const word = words[index];
    const next = line ? `${line} ${word}` : word;
    if (textWidth(next, size) <= width) { line = next; continue; }
    if (line) lines.push(line);
    line = textWidth(word, size) <= width ? word : fit(word);
    if (lines.length === maxLines) {
      lines[maxLines - 1] = `${fit(lines[maxLines - 1])}…`;
      return lines;
    }
  }
  if (line) lines.push(line);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = `${fit(kept[maxLines - 1])}…`;
    return kept;
  }
  return lines;
}

/** Small stable hash, for per-element wobble that never changes between renders. */
export const hash = (value: string) => {
  let h = 2166136261;
  for (let index = 0; index < value.length; index++) h = Math.imul(h ^ value.charCodeAt(index), 16777619);
  return h >>> 0;
};
const tilt = (id: string, max: number) => ((hash(id) % 1000) / 1000 * 2 - 1) * max;

const dateLabel = (iso: string | null, options: Intl.DateTimeFormatOptions) => {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-GB', { ...options, timeZone: 'UTC' });
};
export const dayTitle = (day: number, date: string | null) => {
  const label = dateLabel(date, { weekday: 'short', day: 'numeric', month: 'short' });
  return label ? `Day ${day} · ${label}` : `Day ${day}`;
};

export const money = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount);
  } catch {
    return `${Math.round(amount)} ${currency}`;
  }
};

const SOURCES = ['reddit', 'quora', 'tripadvisor'];
const sourceTag = (url: string | null) => SOURCES.find((name) => url?.toLowerCase().includes(name)) ?? null;

const norm = (value: string) => value.toLowerCase().normalize('NFKD').replace(/\p{M}/gu, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
const isMustSee = (name: string, mustSee: string[]) => {
  const item = norm(name);
  return mustSee.some((entry) => { const want = norm(entry); return !!want && (item.includes(want) || want.includes(item)); });
};

// ---- overview page ------------------------------------------------------------

const BUBBLE = { w: 156, h: 78 };

// ---- page geometry --------------------------------------------------------------

export type Orientation = 'landscape' | 'portrait';

/**
 * Where everything sits on a page. Landscape is the A4 sheet used by the studio and the
 * PDF; portrait is for phones held upright, with the time slots stacked as rows and the
 * sticky notes along the bottom. Both give every element the same id.
 */
interface Frame {
  w: number; h: number; stacked: boolean;
  stopsPerRow: number; maxRows: number; routeTop: number; routeBottom: number;
  stamps: { top: number; right: number; bottom: number };
  receipt: { x: number; y: number; w: number; bottom: number };
  card: { w: number; h: number; perSlot: number };
  slot: (c: number) => Point;             // top-left of a slot's header (doodle, then label)
  cardAt: (c: number, row: number) => Point;
  more: (c: number) => { x: number; y: number; anchor: 'middle' | 'end' };
  notes: { at: (i: number) => Point; w: number; h: number; lines: number; max: number };
  weather: { x: number; w: number };
  subWidth: number; holidayEnd: number; empty: Point;
  /** Portrait has no room beside the subtitle, so a holiday gets its own line under it. */
  holidayBelow: boolean;
}

export const FRAMES: Record<Orientation, Frame> = {
  landscape: {
    w: PAGE.w, h: PAGE.h, stacked: false,
    stopsPerRow: 5, maxRows: 3, routeTop: 190, routeBottom: 520,
    stamps: { top: 590, right: 760, bottom: 720 },
    receipt: { x: 790, y: 560, w: PAGE.w - M - 790, bottom: PAGE.h - 24 },
    card: { w: 225, h: 150, perSlot: 3 },
    slot: (c) => [M + c * 265, 146],
    cardAt: (c, row) => [M + c * 265, 196 + row * 180],
    more: (c) => ({ x: M + c * 265 + 225 / 2, y: 196 + 3 * 180 + 16, anchor: 'middle' }),
    notes: { at: (i) => [858, 176 + i * 184], w: 217, h: 150, lines: 4, max: 3 },
    weather: { x: 858, w: 217 },
    subWidth: 560, holidayEnd: 780, empty: [M + (265 * 3 - 40) / 2, 420], holidayBelow: false,
  },
  portrait: {
    w: PAGE.h, h: PAGE.w, stacked: true,
    stopsPerRow: 3, maxRows: 5, routeTop: 190, routeBottom: 760,
    stamps: { top: 810, right: PAGE.h - M - 290, bottom: 1080 },
    receipt: { x: PAGE.h - M - 260, y: 800, w: 260, bottom: PAGE.w - 24 },
    card: { w: 200, h: 150, perSlot: 3 },
    slot: (c) => [M, 170 + c * 270],
    cardAt: (c, row) => [M + row * 249, 210 + c * 270],
    more: (c) => ({ x: PAGE.h - M, y: 192 + c * 270, anchor: 'end' }),
    notes: { at: (i) => [M + i * 249, 950], w: 200, h: 136, lines: 3, max: 3 },
    weather: { x: PAGE.h - M - 200, w: 200 },
    subWidth: 440, holidayEnd: 540, empty: [PAGE.h / 2, 560], holidayBelow: true,
  },
};

interface Stop { name: string; days: number[] }

/** Overnight bases in trip order; a base you come back to later is a new stop. */
export function tripStops(doc: TripDocument): Stop[] {
  const stops: Stop[] = [];
  for (const day of doc.days) {
    const name = day.base?.trim() || doc.destination || 'Base';
    const last = stops[stops.length - 1];
    if (last && norm(last.name) === norm(name)) last.days.push(day.day);
    else stops.push({ name, days: [day.day] });
  }
  if (!stops.length && doc.destination) stops.push({ name: doc.destination, days: [] });
  return stops;
}

function legBetween(doc: TripDocument, a: string, b: string) {
  const [x, y] = [norm(a), norm(b)];
  return doc.legs.find((leg) => (norm(leg.source) === x && norm(leg.target) === y) || (norm(leg.source) === y && norm(leg.target) === x)) ?? null;
}

function overviewPage(doc: TripDocument, f: Frame): SketchPage {
  const els: SketchElement[] = [];
  const W = f.w;
  const title = doc.destination || 'Your trip';
  const dates = doc.start_date
    ? `${dateLabel(doc.start_date, { day: 'numeric', month: 'short' })} – ${dateLabel(doc.end_date || doc.start_date, { day: 'numeric', month: 'short', year: 'numeric' })}`
    : null;
  const people = doc.travelers.adults + doc.travelers.children;
  const sub = [
    dates, doc.duration_days && `${doc.duration_days} days`, `${people} traveler${people === 1 ? '' : 's'}`,
    doc.origin && `from ${doc.origin}`,
  ].filter(Boolean).join(' · ');
  els.push({ id: 'ov-title', kind: 'text', x: M, y: 92, lines: wrap(title, W - 2 * M, 50, 1), size: 50, tone: 'ink', bold: true });
  els.push({ id: 'ov-rule', kind: 'path', points: [[M, 108], [M + Math.min(textWidth(title, 50), W - 2 * M), 112]], tone: 'accent' });
  els.push({ id: 'ov-sub', kind: 'text', x: M, y: 146, lines: [sub], size: 24, tone: 'muted' });

  // The route: city bubbles snaking across up to three rows.
  const stops = tripStops(doc);
  const STOPS_PER_ROW = f.stopsPerRow;
  const shown = stops.slice(0, STOPS_PER_ROW * f.maxRows);
  const rows = Math.max(1, Math.ceil(shown.length / STOPS_PER_ROW));
  const top = f.routeTop, bottom = f.routeBottom;
  const rowGap = (bottom - top) / rows;
  const left = M + BUBBLE.w / 2 + 20, right = W - M - BUBBLE.w / 2 - 20;
  const centers: Point[] = shown.map((_, index) => {
    const row = Math.floor(index / STOPS_PER_ROW);
    const col = index % STOPS_PER_ROW;
    const inRow = Math.min(STOPS_PER_ROW, shown.length - row * STOPS_PER_ROW);
    const span = inRow === 1 ? 0 : (right - left) * (inRow - 1) / (STOPS_PER_ROW - 1);
    const start = (left + right) / 2 - span / 2;
    const step = inRow === 1 ? 0 : span / (inRow - 1);
    const x = row % 2 === 0 ? start + col * step : start + span - col * step; // snake
    const y = top + rowGap * (row + 0.5) + (col % 2 === 0 ? -14 : 14);        // wave
    return [Math.round(x), Math.round(y)];
  });

  shown.forEach((stop, index) => {
    const [cx, cy] = centers[index];
    const id = `ov-stop-${index}`;
    els.push({ id, kind: 'ellipse', cx, cy, w: BUBBLE.w, h: BUBBLE.h, tone: 'accent', fill: true });
    const nameLines = wrap(stop.name, BUBBLE.w - 28, 24, 2);
    els.push({ id: `${id}-name`, kind: 'text', x: cx, y: cy - (nameLines.length - 1) * 12 + (stop.days.length ? -2 : 8), lines: nameLines, size: 24, tone: 'ink', anchor: 'middle', bold: true });
    if (stop.days.length) {
      const [first, last] = [stop.days[0], stop.days[stop.days.length - 1]];
      els.push({ id: `${id}-days`, kind: 'text', x: cx, y: cy + BUBBLE.h / 2 + 24, lines: [first === last ? `day ${first}` : `days ${first}–${last}`], size: 20, tone: 'muted', anchor: 'middle' });
    }
  });

  for (let index = 1; index < shown.length; index++) {
    const [a, b] = [centers[index - 1], centers[index]];
    const leg = legBetween(doc, shown[index - 1].name, shown[index].name);
    const doodle = modeDoodle(leg?.mode) ?? modeDoodle(doc.travel_mode) ?? 'train';
    const sameRow = Math.floor((index - 1) / STOPS_PER_ROW) === Math.floor(index / STOPS_PER_ROW);
    const dir = Math.sign(b[0] - a[0]) || 1;
    const from: Point = sameRow ? [a[0] + dir * (BUBBLE.w / 2 + 6), a[1]] : [a[0], a[1] + BUBBLE.h / 2 + 34];
    const to: Point = sameRow ? [b[0] - dir * (BUBBLE.w / 2 + 6), b[1]] : [b[0], b[1] - BUBBLE.h / 2 - 6];
    const flight = doodle === 'plane';
    const mid: Point = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2 - (flight ? 34 : 0)];
    const id = `ov-leg-${index}`;
    els.push({ id, kind: 'path', points: flight ? [from, mid, to] : [from, to], tone: 'ink', dashed: flight, arrow: true, curve: flight });
    const doodleAt: Point = sameRow ? [mid[0] - 14, mid[1] - 40] : [mid[0] + 18, mid[1] - 14];
    els.push({ id: `${id}-mode`, kind: 'doodle', name: doodle, x: doodleAt[0], y: doodleAt[1], size: 28, tone: 'accent' });
    if (leg?.duration) {
      els.push({ id: `${id}-time`, kind: 'text', x: sameRow ? mid[0] : doodleAt[0] + 34, y: sameRow ? mid[1] + 26 : doodleAt[1] + 20, lines: [wrap(leg.duration, 110, 18, 1)[0]], size: 18, tone: 'muted', anchor: sameRow ? 'middle' : 'start' });
    }
  }
  if (stops.length > shown.length) {
    els.push({ id: 'ov-more-stops', kind: 'text', x: W - M, y: bottom + 20, lines: [`+${stops.length - shown.length} more stops`], size: 20, tone: 'accent', anchor: 'end' });
  }
  if (!doc.days.length) {
    els.push({ id: 'ov-empty', kind: 'text', x: W / 2, y: bottom - 40, lines: ['The days get drawn here once the plan has some.'], size: 22, tone: 'muted', anchor: 'middle' });
  }

  // Preference stamps: what they love, and what to keep out.
  const stamps = [
    ...doc.interests.map((text) => ({ text: `loves ${text}`, tone: 'green' as const })),
    ...doc.avoid.map((text) => ({ text: `no ${text} ×`, tone: 'red' as const })),
  ].slice(0, 8);
  let sx = M, sy = f.stamps.top;
  stamps.forEach((stamp, index) => {
    const text = wrap(stamp.text, 260, 22, 1)[0];
    const w = Math.round(textWidth(text, 22) + 32);
    if (sx + w > f.stamps.right) { sx = M; sy += 64; }
    if (sy > f.stamps.bottom) return;
    els.push({ id: `ov-stamp-${index}`, kind: 'stamp', x: sx, y: sy, w, text, tone: stamp.tone, rotate: Math.round(tilt(`stamp${index}${text}`, 5) * 10) / 10 });
    sx += w + 18;
  });

  els.push(...receipt(doc, f));

  const route = stops.map((stop) => stop.name).join(' → ');
  const alt = [
    `${title}${sub ? `, ${sub}` : ''}.`, route && `Route: ${route}.`,
    stamps.length ? `Preferences: ${stamps.map((stamp) => stamp.text.replace(' ×', '')).join(', ')}.` : '',
  ].filter(Boolean).join(' ');
  return { id: 'overview', kind: 'overview', day: null, title, alt, w: f.w, h: f.h, elements: els };
}

/** The budget as a till receipt; the total is circled in red when it's over the target. */
function receipt(doc: TripDocument, f: Frame): SketchElement[] {
  const { currency, target, entered, projected, is_locked, locked_total } = doc.budget;
  const { x, y, w, bottom } = f.receipt;
  const lineH = 28;
  const rows: [string, string][] = [];
  if (!is_locked && entered) rows.push(['Entered', money(entered, currency)]);
  if (target != null) rows.push(['Budget', money(target, currency)]);
  const els: SketchElement[] = [
    { id: 'rc-box', kind: 'box', x, y, w, h: bottom - y, tone: 'ink' },
    { id: 'rc-head', kind: 'text', x: x + w / 2, y: y + 32, lines: ['RECEIPT'], size: 22, tone: 'muted', anchor: 'middle', bold: true },
  ];
  if (!rows.length && !projected && !is_locked) {
    els.push({ id: 'rc-empty', kind: 'text', x: x + w / 2, y: y + 100, lines: ['Nothing costed yet'], size: 22, tone: 'muted', anchor: 'middle' });
    return els;
  }
  rows.forEach(([label, value], index) => {
    const ry = y + 64 + index * lineH;
    els.push({ id: `rc-${index}-l`, kind: 'text', x: x + 18, y: ry, lines: [label], size: 21, tone: 'ink' });
    els.push({ id: `rc-${index}-v`, kind: 'text', x: x + w - 18, y: ry, lines: [value], size: 21, tone: 'ink', anchor: 'end' });
  });
  const ty = y + 64 + rows.length * lineH + 18;
  els.push({ id: 'rc-rule', kind: 'path', points: [[x + 16, ty - 22], [x + w - 16, ty - 22]], tone: 'muted', dashed: true });
  const sum = is_locked ? locked_total ?? entered : projected;
  const over = target != null && sum > target;
  const total = money(sum, currency);
  els.push({ id: 'rc-total-l', kind: 'text', x: x + 18, y: ty + 6, lines: [is_locked ? 'Final total' : 'Total'], size: 24, tone: 'ink', bold: true });
  els.push({ id: 'rc-total-v', kind: 'text', x: x + w - 18, y: ty + 6, lines: [sum ? total : 'not costed yet'], size: sum ? 24 : 20, tone: over ? 'red' : sum ? 'ink' : 'muted', anchor: 'end', bold: !!sum });
  if (over) {
    const tw = textWidth(total, 24);
    els.push({ id: 'rc-circle', kind: 'ellipse', cx: x + w - 18 - tw / 2, cy: ty - 2, w: tw + 30, h: 42, tone: 'red' });
    els.push({ id: 'rc-over', kind: 'text', x: x + w - 18, y: ty + 32, lines: [`over by ${money(sum - target!, currency)}`], size: 19, tone: 'red', anchor: 'end' });
  }
  return els;
}

// ---- day pages ------------------------------------------------------------------

export const SLOTS = ['morning', 'afternoon', 'evening'] as const;
const SLOT_DOODLE: Record<(typeof SLOTS)[number], DoodleName> = { morning: 'sun', afternoon: 'partly', evening: 'moon' };

/** Items into morning/afternoon/evening; untimed ones go to whichever slot is emptiest. */
export function slotItems(items: TripDocumentItem[]) {
  const columns: { item: TripDocumentItem; index: number }[][] = [[], [], []];
  items.forEach((item, index) => {
    const slot = SLOTS.indexOf(item.time_of_day as (typeof SLOTS)[number]);
    if (slot >= 0) columns[slot].push({ item, index });
  });
  items.forEach((item, index) => {
    if (SLOTS.includes(item.time_of_day as (typeof SLOTS)[number])) return;
    const emptiest = columns.reduce((best, column, at) => (column.length < columns[best].length ? at : best), 0);
    columns[emptiest].push({ item, index });
  });
  return columns;
}

/**
 * Card ids come from the place's name, not its position, so adding or removing a place
 * leaves every other card's id alone (the live sketch only draws what's new).
 */
export function itemIds(prefix: string, items: TripDocumentItem[]) {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const slug = norm(item.name).replace(/ /g, '-').slice(0, 40) || 'place';
    const count = (seen.get(slug) ?? 0) + 1;
    seen.set(slug, count);
    return `${prefix}-p-${slug}${count > 1 ? `-${count}` : ''}`;
  });
}

function dayPage(doc: TripDocument, day: TripDocument['days'][number], f: Frame): SketchPage {
  const CARD = f.card, NOTES = f.notes;
  const p = `d${day.day}`;
  const els: SketchElement[] = [];
  const title = dayTitle(day.day, day.date);
  els.push({ id: `${p}-title`, kind: 'text', x: M, y: 86, lines: [title], size: 46, tone: 'ink', bold: true });

  const holiday = doc.enrichment?.holidays.find((entry) => entry.day === day.day);
  const sub = [
    day.base && `in ${day.base}`,
    day.hours ? `about ${Math.round(day.hours)} h of plans` : null,
    day.est_cost ? `about ${money(day.est_cost, doc.budget.currency)}` : null,
  ].filter(Boolean).join(' · ');
  els.push({ id: `${p}-sub`, kind: 'text', x: M, y: 124, lines: [wrap(sub, f.subWidth, 23, 1)[0] ?? ''], size: 23, tone: 'muted' });
  if (holiday) {
    const [hx, hy] = f.holidayBelow ? [M, 154] : [M + Math.min(f.subWidth, textWidth(sub, 23)) + 28, 124];
    els.push({ id: `${p}-holiday-flag`, kind: 'doodle', name: 'flag', x: hx, y: hy - 20, size: 24, tone: 'red' });
    els.push({ id: `${p}-holiday`, kind: 'text', x: hx + 30, y: hy, lines: [wrap(`${holiday.name} (holiday)`, f.holidayEnd - hx, 21, 1)[0]], size: 21, tone: 'red' });
  }

  // Weather in the top-right corner, always with its "forecast" or "typical" label.
  const weather = doc.enrichment?.weather.find((entry) => entry.day === day.day);
  // Climate averages have no condition or min/max, only the mean and rain per day.
  const temps = weather && (weather.temp_min != null && weather.temp_max != null
    ? `${Math.round(weather.temp_min)}–${Math.round(weather.temp_max)}°C`
    : weather.temp_mean != null ? `~${Math.round(weather.temp_mean)}°C` : null);
  const sky: DoodleName | null = weather?.condition
    ?? (weather?.rain_mm == null ? null : weather.rain_mm >= 3 ? 'rain' : weather.rain_mm >= 1 ? 'partly' : 'sun');
  if (weather && (sky || temps)) {
    if (sky) els.push({ id: `${p}-wx`, kind: 'doodle', name: sky, x: f.weather.x, y: 44, size: 44, tone: 'accent' });
    if (temps) els.push({ id: `${p}-wx-t`, kind: 'text', x: f.weather.x + 56, y: 72, lines: [temps], size: 26, tone: 'ink', bold: true });
    const label = [weather.label, weather.badge?.text].filter(Boolean).join(' · ');
    els.push({ id: `${p}-wx-l`, kind: 'text', x: f.weather.x, y: 122, lines: wrap(label, f.weather.w, 18, 2), size: 18, tone: 'muted' });
  }

  const columns = slotItems(day.items);
  const ids = itemIds(p, day.items);
  const mustSee = doc.must_see;
  const visible: { id: string; x: number; y: number; slot: number; item: TripDocumentItem }[] = [];
  columns.forEach((column, c) => {
    const [hx, hy] = f.slot(c);
    els.push({ id: `${p}-slot-${c}`, kind: 'doodle', name: SLOT_DOODLE[SLOTS[c]], x: hx, y: hy, size: 26, tone: 'muted' });
    els.push({ id: `${p}-slot-${c}-t`, kind: 'text', x: hx + 34, y: hy + 22, lines: [SLOTS[c][0].toUpperCase() + SLOTS[c].slice(1)], size: 24, tone: 'muted', bold: true });
    column.slice(0, CARD.perSlot).forEach(({ item, index }, row) => {
      const [x, y] = f.cardAt(c, row);
      const id = ids[index];
      els.push({ id, kind: 'box', x, y, w: CARD.w, h: CARD.h, tone: item.option ? 'muted' : 'ink', dashed: item.option, label: item.name });
      els.push({ id: `${id}-icon`, kind: 'doodle', name: categoryDoodle(item.category), x: x + 12, y: y + 14, size: 34, tone: 'accent' });
      const name = wrap(item.name, CARD.w - 66, 25, 3);
      if (isMustSee(item.name, mustSee)) {
        const hw = Math.min(CARD.w - 60, Math.max(...name.map((line) => textWidth(line, 25))) + 10);
        els.push({ id: `${id}-hl`, kind: 'highlight', x: x + 52, y: y + 14, w: hw, h: name.length * 27 + 8 });
      }
      els.push({ id: `${id}-name`, kind: 'text', x: x + 56, y: y + 38, lines: name, size: 25, tone: 'ink', bold: true });
      const meta = [
        item.option ? 'optional' : null, item.area, item.duration_hours ? `${item.duration_hours} h` : null,
        item.est_cost ? money(item.est_cost, doc.budget.currency) : item.est_cost === 0 ? 'free' : null,
      ].filter(Boolean).join(' · ');
      if (meta) els.push({ id: `${id}-meta`, kind: 'text', x: x + 14, y: y + 38 + name.length * 27 + 14, lines: wrap(meta, CARD.w - 28, 18, name.length > 2 ? 1 : 2), size: 18, tone: 'muted' });
      visible.push({ id, x, y, slot: c, item });
    });
    const hidden = column.length - CARD.perSlot;
    if (hidden > 0) {
      const more = f.more(c);
      els.push({ id: `${p}-more-${c}`, kind: 'text', x: more.x, y: more.y, lines: [`+${hidden} more`], size: 22, tone: 'accent', anchor: more.anchor });
    }
  });

  // Arrows in visiting order; the hops between time slots carry a transit doodle.
  const hop = modeDoodle(doc.travel_mode) ?? 'walk';
  for (let index = 1; index < visible.length; index++) {
    const [a, b] = [visible[index - 1], visible[index]];
    const id = `${p}-a${index}`;
    if (a.slot === b.slot) {
      // Within a slot: down the column (landscape) or along the row (portrait).
      els.push({ id, kind: 'path', tone: 'muted', arrow: true, points: f.stacked
        ? [[a.x + CARD.w + 4, a.y + CARD.h / 2], [b.x - 4, b.y + CARD.h / 2]]
        : [[a.x + CARD.w / 2, a.y + CARD.h + 4], [b.x + CARD.w / 2, b.y - 4]] });
    } else if (f.stacked) {
      // Down to the next slot's row, landing clear of its header label.
      const from: Point = [a.x + CARD.w / 2, a.y + CARD.h + 4];
      const to: Point = [b.x + CARD.w - 30, b.y - 4];
      const mid: Point = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
      els.push({ id, kind: 'path', points: [from, mid, to], tone: 'muted', arrow: true, curve: true });
      els.push({ id: `${id}-mode`, kind: 'doodle', name: hop, x: mid[0] - 11, y: mid[1] - 11, size: 22, tone: 'muted' });
    } else {
      const from: Point = [a.x + CARD.w + 4, a.y + CARD.h / 2];
      const to: Point = [b.x - 4, b.y + CARD.h / 2];
      // Level hops arc over the gutter with the doodle on top; rows apart, the doodle rides the line.
      const level = from[1] === to[1];
      const mid: Point = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2 - (level ? 18 : 0)];
      els.push({ id, kind: 'path', points: [from, mid, to], tone: 'muted', arrow: true, curve: true });
      els.push({ id: `${id}-mode`, kind: 'doodle', name: hop, x: mid[0] - 11, y: level ? mid[1] - 32 : mid[1] - 11, size: 22, tone: 'muted' });
    }
  }

  // Margin sticky notes: each item's own traveler tip, tagged with where it came from.
  visible.filter(({ item }) => item.tip).slice(0, NOTES.max).forEach(({ id, item }, index) => {
    const [x, y] = NOTES.at(index);
    els.push({
      id: `${id}-note`, kind: 'note', x, y, w: NOTES.w, h: NOTES.h, rotate: Math.round(tilt(id, 2.5) * 10) / 10,
      title: wrap(item.name, NOTES.w - 24, 18, 1)[0], lines: wrap(item.tip!, NOTES.w - 24, 19, NOTES.lines), tag: sourceTag(item.source_url),
    });
  });

  if (!day.items.length) {
    els.push({ id: `${p}-empty`, kind: 'text', x: f.empty[0], y: f.empty[1], lines: ['A free day: nothing planned yet.'], size: 26, tone: 'muted', anchor: 'middle' });
  }

  const alt = [
    `${title}${day.base ? `, in ${day.base}` : ''}.`,
    ...columns.map((column, c) => column.length ? `${SLOTS[c][0].toUpperCase() + SLOTS[c].slice(1)}: ${column.map(({ item }) => item.name).join(', ')}.` : ''),
    holiday ? `${holiday.name} is a public holiday.` : '',
    weather ? `Weather ${weather.label}.` : '',
  ].filter(Boolean).join(' ');
  return { id: p, kind: 'day', day: day.day, title, alt, w: f.w, h: f.h, elements: els };
}

export function layoutSketch(doc: TripDocument, orientation: Orientation = 'landscape'): SketchPage[] {
  const frame = FRAMES[orientation];
  return [overviewPage(doc, frame), ...doc.days.map((day) => dayPage(doc, day, frame))];
}
