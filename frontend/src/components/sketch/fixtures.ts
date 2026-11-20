/* Demo trips for the sketch tests and for eyeballing pages in the browser. */
import type { TripDocument, TripDocumentItem } from '../../services/tripService';

export const item = (name: string, extra: Partial<TripDocumentItem> = {}): TripDocumentItem => ({
  name, category: 'sight', time_of_day: null, area: null, est_cost: null, duration_hours: null, tip: null,
  source_url: null, option: false, ...extra,
});

export const base = (extra: Partial<TripDocument>): TripDocument => ({
  trip_id: 't', mode: 'agent', status: 'complete', destination: 'Japan', origin: 'Delhi', duration_days: 1,
  start_date: null, end_date: null, travelers: { adults: 2, children: 0 }, comfort: 'mid_range', travel_mode: 'transit',
  pace: 'balanced', interests: [], avoid: [], must_see: [], guide: null, legs: [], graph: null, enrichment: null,
  budget: { currency: 'JPY', target: null, planned: null, entered: 0, projected: 0 }, days: [], ...extra,
});

export const tokyo = base({
  destination: 'Tokyo & Kyoto', duration_days: 4, start_date: '2026-10-12', end_date: '2026-10-15',
  interests: ['food', 'temples'], avoid: ['museums'], must_see: ['Fushimi Inari'],
  legs: [{ source: 'Tokyo', target: 'Kyoto', mode: 'Shinkansen', duration: '2h 15m', distance: null, cost: null }],
  budget: { currency: 'JPY', target: 120000, planned: 110000, entered: 20000, projected: 131000 },
  days: [
    { day: 1, date: '2026-10-12', base: 'Tokyo', est_cost: 14000, hours: 7, items: [
      item('Senso-ji', { time_of_day: 'morning', area: 'Asakusa', duration_hours: 1.5, tip: 'Go before 8 for an empty Nakamise street', source_url: 'https://www.reddit.com/r/JapanTravel/x' }),
      item('Tsukiji Outer Market', { time_of_day: 'morning', category: 'food', est_cost: 3000 }),
      item('teamLab Planets', { time_of_day: 'afternoon', category: 'experience', est_cost: 3800, tip: 'Wear shorts; you wade through water', source_url: 'https://www.quora.com/y' }),
      item('Omoide Yokocho', { time_of_day: 'evening', category: 'nightlife' }),
    ] },
    { day: 2, date: '2026-10-13', base: 'Tokyo', est_cost: null, hours: null, items: [] },
    { day: 3, date: '2026-10-14', base: 'Kyoto', est_cost: 9000, hours: 6, items: [
      item('Fushimi Inari Taisha', { time_of_day: 'morning', tip: 'Climb past the Yotsutsuji crossing, the crowds thin out' }),
      item('Nishiki Market', { time_of_day: 'afternoon', category: 'food' }),
      item('Gion walk', { time_of_day: 'evening' }), item('Pontocho dinner', { category: 'food', option: true }),
    ] },
    { day: 4, date: '2026-10-15', base: 'Kyoto', est_cost: null, hours: null, items: [item('嵐山 竹林の小径', { time_of_day: 'morning', category: 'nature' })] },
  ],
  enrichment: {
    weather: [{ day: 1, date: '2026-10-12', base: 'Tokyo', kind: 'forecast', label: 'forecast', condition: 'rain', temp_min: 16, temp_max: 21, temp_mean: 18, rain_mm: 8, badge: null, notes: [] }],
    holidays: [{ day: 1, date: '2026-10-12', name: 'Sports Day', local_name: 'スポーツの日', country: 'JP', regional: false }],
    exchange: null, sources: [],
  },
});

export const kyoto = base({
  destination: 'Kyoto', duration_days: 2, travel_mode: 'walk',
  budget: { currency: 'INR', target: 50000, planned: 30000, entered: 0, projected: 30000 },
  days: [
    { day: 1, date: null, base: 'Kyoto', est_cost: null, hours: null, items: [item('Kinkaku-ji'), item('Ryoan-ji'), item('Kitano Tenmangu')] },
    { day: 2, date: null, base: 'Kyoto', est_cost: null, hours: null, items: [item('Kiyomizu-dera', { time_of_day: 'morning' })] },
  ],
});
