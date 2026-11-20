/*
 * Calendar export (RFC 5545). One event per planned item, timed from its time-of-day block;
 * items without one become all-day events. Times are "floating" (no time zone), so they read
 * as local time at the destination, which is what a traveler expects on the day.
 */
import type { TripDocument } from '../../services/tripService';
import { clockOf } from '../dayRoute';
import { tripTitle } from './download';

type Item = TripDocument['days'][number]['items'][number];
const BLOCKS = { morning: [9, 12], afternoon: [13, 17], evening: [18, 21] } as const;

/** RFC 5545 TEXT escaping. */
export const icsText = (value: string) =>
  value.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');

/** Fold a content line at 75 octets, never splitting a UTF-8 character (continuations start with a space). */
export function foldLine(line: string): string {
  const encoder = new TextEncoder();
  const parts: string[] = [];
  let current = '';
  let size = 0;
  for (const char of line) {
    const bytes = encoder.encode(char).length;
    if (size + bytes > (parts.length ? 74 : 75)) {
      parts.push(current);
      current = '';
      size = 0;
    }
    current += char;
    size += bytes;
  }
  parts.push(current);
  return parts.join('\r\n ');
}

const ymd = (iso: string) => iso.slice(0, 10).replace(/-/g, '');
const nextDay = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
};
const at = (iso: string, minutes: number) =>
  `${ymd(iso)}T${String(Math.floor(minutes / 60)).padStart(2, '0')}${String(minutes % 60).padStart(2, '0')}00`;

function describe(item: Item, day: TripDocument['days'][number], currency: string): string {
  return [
    `Day ${day.day} · ${day.base}`,
    item.category !== 'other' ? item.category : '',
    item.est_cost !== null ? `About ${item.est_cost} ${currency} per person` : '',
    item.tip || '',
    item.source_url || '',
  ].filter(Boolean).join('\n');
}

/** The trip with every day dated from `start` (YYYY-MM-DD): a trip planned without dates still gets a calendar. */
export function datedFrom(doc: TripDocument, start: string): TripDocument {
  let date = start;
  const days = doc.days.map((day, index) => ({ ...day, date: index ? (date = nextDay(date)) : date }));
  return { ...doc, start_date: start, end_date: date, days };
}

/** The trip as an .ics calendar, or null when it has no start date to anchor the days. */
export function toIcs(doc: TripDocument, now = new Date()): string | null {
  if (!doc.start_date) return null;
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Pathfinder//Trip export//EN', 'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH', `X-WR-CALNAME:${icsText(tripTitle(doc))}`];
  const event = (uid: string, summary: string, when: [string, string] | { date: string }, description = '', location = '') => {
    lines.push('BEGIN:VEVENT', `UID:${uid}@pathfinder`, `DTSTAMP:${stamp}`,
      ...('date' in when ? [`DTSTART;VALUE=DATE:${ymd(when.date)}`, `DTEND;VALUE=DATE:${ymd(nextDay(when.date))}`]
        : [`DTSTART:${when[0]}`, `DTEND:${when[1]}`]),
      `SUMMARY:${icsText(summary)}`,
      ...(description ? [`DESCRIPTION:${icsText(description)}`] : []),
      ...(location ? [`LOCATION:${icsText(location)}`] : []),
      'END:VEVENT');
  };

  for (const day of doc.days) {
    if (!day.date) continue;
    const booked = { morning: 0, afternoon: 0, evening: 0 }; // minutes already used in each block
    day.items.forEach((item, index) => {
      const stay = item.category === 'stay';
      const summary = (stay ? 'Stay: ' : '') + item.name + (item.option ? ' (option)' : '');
      const uid = `${doc.trip_id}-d${day.day}-${index}`;
      const location = [item.area, day.base].filter(Boolean).join(', ');
      const block = item.time_of_day;
      const stated = stay ? null : clockOf(item.start);
      // A stay is the night, not a timed stop. A stated clock time beats the morning/afternoon block.
      if (stated != null && day.date) {
        const end = clockOf(item.end);
        const length = end != null && end > stated ? end - stated : Math.max(30, Math.round((item.duration_hours || 1.5) * 60));
        event(uid, summary, [at(day.date, stated), at(day.date, Math.min(stated + length, 23 * 60 + 59))],
          describe(item, day, doc.budget.currency), location);
        return;
      }
      if (stay || !block) {
        event(uid, summary, { date: day.date! }, describe(item, day, doc.budget.currency), location);
        return;
      }
      const [open, close] = BLOCKS[block];
      const share = (close - open) * 60 / day.items.filter((other) => other.time_of_day === block).length;
      const length = Math.max(30, Math.round(item.duration_hours ? item.duration_hours * 60 : share));
      const start = Math.min(open * 60 + booked[block], 23 * 60 - 30);
      booked[block] += length;
      event(uid, summary, [at(day.date!, start), at(day.date!, Math.min(start + length, 23 * 60 + 59))],
        describe(item, day, doc.budget.currency), location);
    });
  }
  doc.legs.forEach((leg, index) => {
    if (!doc.start_date) return;
    const description = [leg.mode, leg.duration, leg.distance, leg.cost, 'Estimated: no timetable was looked up.'].filter(Boolean).join('\n');
    event(`${doc.trip_id}-leg-${index}`, `${leg.source} → ${leg.target}`, { date: doc.start_date }, description, leg.target);
  });
  for (const holiday of doc.enrichment?.holidays ?? []) {
    event(`${doc.trip_id}-holiday-${holiday.date}-${holiday.name}`.replace(/\s+/g, '-'),
      `Public holiday: ${holiday.name}`, { date: holiday.date },
      `Some places may close, and popular spots get busier.${holiday.regional ? ' Observed in some regions only.' : ''}\nSource: Nager.Date`);
  }
  lines.push('END:VCALENDAR');
  return lines.map(foldLine).join('\r\n') + '\r\n';
}
