/** A small, typed TripPlan for component tests (two days in Kyoto, taken from a mock run). */
import type { Place, SourceRef, TripPlan } from '@/api'

const at = '2026-10-08T18:01:31Z'
const src = (tool: SourceRef['tool'], call: string): SourceRef => ({ tool, call_id: call, provider: `mock-${tool}`, fetched_at: at })

function place(id: string, name: string, category: string, lat: number, lng: number, price: number): Place {
  return {
    place_id: id,
    name,
    category,
    location: { lat, lng },
    address: `${name}, Kyoto`,
    rating: 4.5,
    indoor: false,
    price: { amount: price, currency: 'JPY' },
    needs_reservation: false,
    closed_dates: [],
    opening_hours: '09:00-17:00',
    source: src('places', 'tc_places'),
  }
}

export function makePlan(overrides: Partial<TripPlan> = {}): TripPlan {
  return {
    schema_version: '1.0',
    plan_id: 'plan-1',
    version: 1,
    destination: 'Kyoto',
    origin: 'Tokyo',
    start_date: '2026-11-10',
    end_date: '2026-11-11',
    party_size: 2,
    budget: { amount: 300000, currency: 'JPY' },
    days: [
      {
        date: '2026-11-10',
        items: [
          { item_id: 'kiyomizu@1', place_id: 'kiyomizu', title: 'Kiyomizu-dera', start_time: '10:00:00', end_time: '12:00:00', confirmed: false, needs_reservation: false },
          { item_id: 'inari@1', place_id: 'inari', title: 'Fushimi Inari Taisha', start_time: '12:30:00', end_time: '14:30:00', confirmed: true, needs_reservation: false },
        ],
        forecast: {
          date: '2026-11-10',
          summary: 'Partly cloudy',
          temp_min_c: 7,
          temp_max_c: 17,
          precipitation_chance: 0.25,
          source: src('weather', 'tc_weather'),
        },
      },
      {
        date: '2026-11-11',
        items: [
          { item_id: 'gion@2', place_id: 'gion', title: 'Gion District', start_time: '09:00:00', end_time: '11:00:00', confirmed: false, needs_reservation: false },
          { item_id: 'nijo@2', place_id: 'nijo', title: 'Nijo Castle', start_time: '11:30:00', end_time: '13:30:00', confirmed: false, needs_reservation: false },
        ],
        forecast: null,
      },
    ],
    places: [
      place('kiyomizu', 'Kiyomizu-dera', 'temple', 34.9949, 135.785, 500),
      place('inari', 'Fushimi Inari Taisha', 'shrine', 34.9671, 135.7727, 0),
      place('gion', 'Gion District', 'district', 35.0037, 135.7788, 0),
      place('nijo', 'Nijo Castle', 'castle', 35.0142, 135.7481, 1300),
    ],
    hotel: null,
    tickets: [],
    reservations: [],
    sections: [
      { agent: 'attraction', status: 'ok', fetched_at: at },
      { agent: 'hotel', status: 'ok', fetched_at: at },
      { agent: 'weather', status: 'ok', fetched_at: at },
      { agent: 'ticket', status: 'ok', fetched_at: at },
    ],
    cost: { currency: 'JPY', hotel: 15200, tickets: 61500, attractions: 16600, total: 93300, complete: true },
    violations: [],
    disruptions: [],
    saved_trip_refs: [],
    ...overrides,
  }
}
