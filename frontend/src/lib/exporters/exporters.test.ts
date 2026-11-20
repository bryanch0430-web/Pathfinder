import { describe, expect, it } from 'vitest';
import type { TripBudget, TripDocument } from '../../services/tripService';
import { budgetCsv, csvCell } from './csv';
import { fileBase } from './download';
import { toGpx } from './gpx';
import { datedFrom, foldLine, icsText, toIcs } from './ics';
import { toKml } from './kml';
import { dayDirectionsUrl } from './maps';
import { pointsRequest, xml } from './points';

const item = (name: string, extra: Partial<TripDocument['days'][number]['items'][number]> = {}) => ({
  name, category: 'sight', time_of_day: null, area: null, est_cost: null, duration_hours: null, tip: null,
  source_url: null, option: false, ...extra,
});

const doc: TripDocument = {
  trip_id: 'trip-1', mode: 'agent', status: 'complete', destination: 'Kyōto', origin: 'Delhi', duration_days: 2,
  start_date: '2026-10-12', end_date: '2026-10-13', travelers: { adults: 2, children: 0 }, comfort: 'mid_range',
  travel_mode: 'walk', pace: 'balanced', interests: [], avoid: [], must_see: [], guide: null, legs: [], graph: null,
  budget: { currency: 'JPY', target: null, planned: null, entered: 0, projected: 0 },
  days: [
    { day: 1, date: '2026-10-12', base: 'Kyoto', est_cost: null, hours: null, items: [
      item('Fushimi Inari', { time_of_day: 'morning', duration_hours: 2, tip: 'Go before 8, it\'s quiet; bring water' }),
      item('Tofuku-ji', { time_of_day: 'morning' }),
      item('Nishiki Market', { time_of_day: 'evening', category: 'food', est_cost: 1500 }),
      item('Kyoto Station walk'),
    ] },
    { day: 2, date: '2026-10-13', base: 'Kyoto', est_cost: null, hours: null, items: [
      item('Arashiyama', { time_of_day: 'afternoon' }), item('Nara day trip', { option: true }),
    ] },
  ],
  enrichment: { weather: [], exchange: null, sources: [], holidays: [
    { day: 1, date: '2026-10-12', name: 'Sports Day', local_name: 'スポーツの日', country: 'JP', regional: false },
  ] },
};

describe('a trip with no start date', () => {
  const undated: TripDocument = { ...doc, start_date: null, end_date: null, days: doc.days.map((day) => ({ ...day, date: null })) };

  it('has no calendar until a start day is picked, then dates every day from it', () => {
    expect(toIcs(undated)).toBeNull();
    const dated = datedFrom(undated, '2026-12-30');
    expect([dated.start_date, dated.end_date, dated.days.map((day) => day.date)]).toEqual(
      ['2026-12-30', '2026-12-31', ['2026-12-30', '2026-12-31']]);
    expect(toIcs(dated)).toContain('DTSTART:20261231T130000'); // day 2's afternoon, across the month edge
    expect(undated.days[0].date).toBeNull(); // the trip document itself is untouched
  });
});

describe('ics', () => {
  const ics = toIcs(doc, new Date('2026-09-28T10:15:00Z'))!;
  const events = ics.split('BEGIN:VEVENT').slice(1);

  it('times items from their block, back to back, and makes untimed items all-day', () => {
    expect(events).toHaveLength(7); // 6 items + 1 holiday
    expect(events[0]).toContain('DTSTART:20261012T090000\r\nDTEND:20261012T110000'); // 2 h from duration_hours
    expect(events[1]).toContain('DTSTART:20261012T110000'); // after it, in the same morning block
    expect(events[2]).toContain('DTSTART:20261012T180000\r\nDTEND:20261012T210000');
    expect(events[3]).toContain('DTSTART;VALUE=DATE:20261012\r\nDTEND;VALUE=DATE:20261013');
    expect(events[4]).toContain('DTSTART:20261013T130000\r\nDTEND:20261013T170000');
    expect(events[6]).toContain('SUMMARY:Public holiday: Sports Day');
    expect(ics).toContain('SUMMARY:Nara day trip (option)');
    expect(ics).not.toContain('BEGIN:VEVENT\r\nUID:trip-1-leg-');
    expect(ics).toContain('DTSTAMP:20260928T101500Z');
  });

  it('escapes text, uses CRLF and folds long lines at 75 octets without splitting characters', () => {
    expect(icsText('a,b;c\\d\ne')).toBe('a\\,b\\;c\\\\d\\ne');
    expect(ics).toContain("Go before 8\\, it's quiet\\; bring water");
    expect(ics.split('\r\n').every((line) => new TextEncoder().encode(line).length <= 75)).toBe(true);
    const folded = foldLine(`DESCRIPTION:${'京都'.repeat(40)}`);
    expect(folded.split('\r\n ').join('')).toBe(`DESCRIPTION:${'京都'.repeat(40)}`);
    expect(folded.split('\r\n').every((line) => new TextEncoder().encode(line).length <= 75)).toBe(true);
  });

  it('puts a stay on the calendar as an all-day event and includes the route leg', () => {
    const withTravel: TripDocument = {
      ...doc,
      days: [{ ...doc.days[0], items: [...doc.days[0].items, item('Cross Hotel', { category: 'stay' })] }, doc.days[1]],
      legs: [{ source: 'Delhi', target: 'Kyoto', mode: 'flight', duration: '8h', distance: null, cost: null }],
    };
    const file = toIcs(withTravel, new Date('2026-09-28T10:15:00Z'))!;
    expect(file).toContain('SUMMARY:Stay: Cross Hotel');
    expect(file).toContain('SUMMARY:Delhi → Kyoto');
    expect(file).toContain('Estimated: no timetable was looked up.');
    expect(file).toContain('UID:trip-1-leg-0@pathfinder');
  });

  it('needs a start date', () => {
    expect(toIcs({ ...doc, start_date: null })).toBeNull();
  });
});

describe('maps', () => {
  it('links a day through its places, leaving options out', () => {
    const url = new URL(dayDirectionsUrl(doc.days[0], 'walk')!);
    expect(url.searchParams.get('origin')).toBe('Fushimi Inari, Kyoto');
    expect(url.searchParams.get('destination')).toBe('Kyoto Station walk, Kyoto');
    expect(url.searchParams.get('waypoints')).toBe('Tofuku-ji, Kyoto|Nishiki Market, Kyoto');
    expect(url.searchParams.get('travelmode')).toBe('walking');
    expect(dayDirectionsUrl(doc.days[1])).toContain('/maps/search/?api=1&query=Arashiyama');
  });

  it('lets Google pick the mode for multi-stop transit, and caps the stops', () => {
    expect(new URL(dayDirectionsUrl(doc.days[0], 'transit')!).searchParams.has('travelmode')).toBe(false);
    const busy = { ...doc.days[0], items: Array.from({ length: 14 }, (_, i) => item(`Stop ${i}`)) };
    const url = new URL(dayDirectionsUrl(busy)!);
    expect(url.searchParams.get('waypoints')!.split('|')).toHaveLength(7);
    expect(dayDirectionsUrl({ ...doc.days[0], items: [] })).toBeNull();
  });
});

describe('gpx and kml', () => {
  const points = { 'd1-0': { lat: 34.9671, lon: 135.7727 }, 'd1-2': { lat: 35.005, lon: 135.7649 }, 'd2-0': { lat: 35.0094, lon: 135.6668 } };

  it('asks for every place with its base', () => {
    expect(pointsRequest(doc).places.map((p) => p.id)).toEqual(['d1-0', 'd1-1', 'd1-2', 'd1-3', 'd2-0', 'd2-1']);
  });

  it('writes located places as waypoints before routes, escaped', () => {
    const gpx = toGpx({ ...doc, days: [{ ...doc.days[0], base: 'Kyoto & <co>' }, doc.days[1]] }, points);
    expect(gpx).toContain('<wpt lat="34.967100" lon="135.772700"><name>Fushimi Inari</name>');
    expect(gpx).toContain('Day 1 · Kyoto &amp; &lt;co&gt;');
    expect(gpx.lastIndexOf('<wpt')).toBeLessThan(gpx.indexOf('<rte>'));
    expect(gpx.match(/<rte>/g)).toHaveLength(1); // day 2 has one located place: no route
    expect(gpx).not.toContain('Tofuku-ji'); // not located, so left out
  });

  it('writes KML coordinates as lon,lat with a path per day', () => {
    const kml = toKml(doc, points);
    expect(kml).toContain('<coordinates>135.772700,34.967100</coordinates>');
    expect(kml).toContain('<LineString><tessellate>1</tessellate><coordinates>135.772700,34.967100 135.764900,35.005000</coordinates>');
    expect(xml('a\u0001"\'')).toBe('a&quot;&apos;');
  });
});

describe('csv', () => {
  it('quotes, neutralises formulas and adds totals', () => {
    expect(csvCell('a,"b"')).toBe('"a,""b"""');
    expect(csvCell('=HYPERLINK("x")')).toBe('"\'=HYPERLINK(""x"")"');
    expect(csvCell('-5', false)).toBe('-5');
    const budget = {
      trip_id: 't', currency: 'JPY', target_amount: '150000.00', priced_total: '6000.00', remaining: null, unpriced_count: 0,
      projected_total: '9000.00', unestimated_count: 0, review_count: 0, category_totals: {}, place_totals: {},
      items: [{ id: '1', label: 'Ryokan, 2 nights', category: 'stay', place_name: 'Kyoto', quantity: '2', unit_amount: '3000.00',
        is_included: true, source_key: null, scope: 'stop', quote_text: null, estimate_amount: null, estimate_note: null,
        is_current: true, updated_at: '' }],
    } as TripBudget;
    const lines = budgetCsv(budget).split('\r\n');
    expect(lines[0].startsWith('﻿Category,Item')).toBe(true);
    expect(lines[1]).toBe('stay,"Ryokan, 2 nights",Kyoto,2,3000.00,6000.00,JPY,yes,,');
    expect(lines.at(-2)).toBe(',Budget,,,,150000.00,JPY,,,');
  });
});

it('names files after the trip', () => {
  expect(fileBase(doc)).toBe('kyoto-2-days');
});
