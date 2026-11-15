import { samplePlan } from "./fixtures";
import type { DayPlan, HotelStay, PlanItem, TicketOption, TransportLeg, TripPlan, TripSegment } from "./types";

const now = "2026-10-30T08:00:00Z";
const source = [{ title: "Place page", url: "https://example.com", source: "google-places", fetched_at: now }];

const place = (name: string, lat: number, lng: number, timezone = "Asia/Tokyo") => ({
  name,
  country_code: "JP",
  geo: { lat, lng },
  timezone,
});

function stop(id: string, name: string, lat: number, lng: number, start = "09:30", end = "11:30"): PlanItem {
  return {
    id,
    type: "attraction",
    name,
    start,
    end,
    geo: { lat, lng },
    address: "",
    notes: "",
    source_refs: source,
    data_status: "ok",
    fetched_at: now,
    price: null,
    price_kind: "none",
    locked: false,
    confirmed: false,
    tags: [],
  };
}

function stay(id: string, name: string, segment: TripSegment, area: string, nightly: number, from = segment.dates.start, to = segment.dates.end): HotelStay {
  return {
    ...samplePlan.hotels[0],
    id,
    name,
    area,
    check_in: from,
    check_out: to,
    price_per_night: { amount: nightly, currency: "JPY" },
    segment_id: segment.id,
  };
}

function ticket(id: string, leg: string, mode: TicketOption["mode"], name: string, depart: string, arrive: string, minutes: number, extra: Partial<TicketOption> = {}): TicketOption {
  return {
    id,
    leg_id: leg,
    mode,
    name,
    origin: "",
    destination: "",
    depart_at: depart,
    arrive_at: arrive,
    duration_minutes: minutes,
    price: null,
    price_kind: "none",
    operator: "",
    booking_url: "",
    notes: "",
    source_refs: [],
    data_status: "ok",
    confirmed: false,
    selected: false,
    ...extra,
  };
}

const tokyo: TripSegment = { id: "seg_1", destination: place("Tokyo", 35.6762, 139.6503), dates: { start: "2027-04-10", end: "2027-04-13" } };
const kyoto: TripSegment = { id: "seg_2", destination: place("Kyoto", 35.0116, 135.7681), dates: { start: "2027-04-13", end: "2027-04-16" } };
const osaka: TripSegment = { id: "seg_3", destination: place("Osaka", 34.6937, 135.5023), dates: { start: "2027-04-16", end: "2027-04-18" } };

const day = (date: string, segment: TripSegment, title: string, items: PlanItem[]): DayPlan => ({
  date,
  title,
  weather: null,
  items,
  note: "",
  segment_id: segment.id,
});

const legs: TransportLeg[] = [
  { id: "leg_out", from_place: "Hong Kong", to_place: "Tokyo", depart_date: "2027-04-10", kind: "outbound" },
  { id: "leg_1", from_place: "Tokyo", to_place: "Kyoto", depart_date: "2027-04-13", kind: "between" },
  { id: "leg_2", from_place: "Kyoto", to_place: "Osaka", depart_date: "2027-04-16", kind: "between" },
  { id: "leg_home", from_place: "Osaka", to_place: "Hong Kong", depart_date: "2027-04-18", kind: "return" },
];

/** Tokyo three nights, Kyoto three, Osaka two: a stay in each, and a leg for every hop with its own choice. */
export const threeCityPlan: TripPlan = {
  ...samplePlan,
  destination: tokyo.destination,
  dates: { start: "2027-04-10", end: "2027-04-18" },
  summary: "Tokyo → Kyoto → Osaka: 9 days.",
  segments: [tokyo, kyoto, osaka],
  days: [
    day("2027-04-10", tokyo, "Arrive and Asakusa", [stop("t1", "Senso-ji", 35.7148, 139.7967, "15:00", "17:00")]),
    day("2027-04-11", tokyo, "Shibuya and Harajuku", [stop("t2", "Meiji Jingu", 35.6764, 139.6993)]),
    day("2027-04-12", tokyo, "Museums", [stop("t3", "Tokyo National Museum", 35.7188, 139.7765)]),
    day("2027-04-13", kyoto, "Train to Kyoto", [stop("k1", "Nishiki Market", 35.005, 135.765, "14:00", "16:00")]),
    day("2027-04-14", kyoto, "Fushimi Inari", [stop("k2", "Fushimi Inari Taisha", 34.9671, 135.7727, "08:00", "10:30")]),
    day("2027-04-15", kyoto, "Arashiyama", [stop("k3", "Arashiyama Bamboo Grove", 35.0094, 135.6668)]),
    day("2027-04-16", osaka, "On to Osaka", [stop("o1", "Dotonbori", 34.6687, 135.5013, "14:00", "16:30")]),
    day("2027-04-17", osaka, "Castle and aquarium", [stop("o2", "Osaka Castle", 34.6873, 135.5262)]),
    day("2027-04-18", osaka, "Home", []),
  ],
  hotels: [
    stay("hotel_t", "Asakusa View Hotel", tokyo, "Asakusa", 18500),
    stay("hotel_k", "Kyoto Garden Inn", kyoto, "Gion", 21400),
    stay("hotel_o", "Namba Riverside", osaka, "Namba", 16800),
  ],
  hotel_options: [
    stay("hotel_t", "Asakusa View Hotel", tokyo, "Asakusa", 18500),
    stay("hotel_t2", "Ueno Park Inn", tokyo, "Ueno", 15200),
    stay("hotel_k", "Kyoto Garden Inn", kyoto, "Gion", 21400),
    stay("hotel_k2", "Station Hostel Kyoto", kyoto, "Kyoto Station", 8900),
    stay("hotel_o", "Namba Riverside", osaka, "Namba", 16800),
  ],
  legs,
  tickets: [
    ticket("out_1", "leg_out", "flight", "Cathay Pacific CX524", "2027-04-10T08:10:00", "2027-04-10T13:35:00", 265, {
      origin: "HKG",
      destination: "HND",
      price: { amount: 52000, currency: "JPY" },
      price_kind: "reference",
      selected: true,
    }),
    ticket("out_2", "leg_out", "flight", "HK Express UO850", "2027-04-10T09:40:00", "2027-04-10T14:50:00", 250, {
      origin: "HKG",
      destination: "NRT",
      price: { amount: 34000, currency: "JPY" },
      price_kind: "reference",
    }),
    ticket("leg1_train", "leg_1", "train", "Train Tokyo to Kyoto (estimate)", "2027-04-13T09:00:00", "2027-04-13T11:40:00", 160, {
      origin: "Tokyo",
      destination: "Kyoto",
      estimated: true,
      selected: true,
    }),
    ticket("leg1_flight", "leg_1", "flight", "ANA NH21", "2027-04-13T10:00:00", "2027-04-13T11:15:00", 75, {
      origin: "HND",
      destination: "ITM",
      price: { amount: 14800, currency: "JPY" },
      price_kind: "reference",
    }),
    ticket("leg1_drive", "leg_1", "drive", "Drive (estimate)", "2027-04-13T09:00:00", "2027-04-13T13:50:00", 290, {
      origin: "Tokyo",
      destination: "Kyoto",
      estimated: true,
    }),
    ticket("leg2_ground", "leg_2", "ground", "Ground transport (rough estimate)", "2027-04-16T09:00:00", "2027-04-16T11:00:00", 120, {
      origin: "Kyoto",
      destination: "Osaka",
      estimated: true,
      selected: true,
    }),
    ticket("home_1", "leg_home", "flight", "Cathay Pacific CX565", "2027-04-18T18:30:00", "2027-04-18T21:50:00", 260, {
      origin: "KIX",
      destination: "HKG",
      price: { amount: 49000, currency: "JPY" },
      price_kind: "reference",
      selected: true,
    }),
  ],
  cost: {
    currency: "JPY",
    lines: [
      { label: "Hotel · Asakusa View Hotel × 3 nights", amount: 55500, kind: "reference", note: "" },
      { label: "Hotel · Kyoto Garden Inn × 3 nights", amount: 64200, kind: "reference", note: "" },
      { label: "Hotel · Namba Riverside × 2 nights", amount: 33600, kind: "reference", note: "" },
      { label: "Flights · Cathay Pacific CX524 (Hong Kong to Tokyo)", amount: 52000, kind: "reference", note: "" },
      { label: "Flights · Cathay Pacific CX565 (Osaka to Hong Kong)", amount: 49000, kind: "reference", note: "" },
    ],
    total: 254300,
    budget: 400000,
    remaining: 145700,
    status: "within",
    notes: ["Transport from Tokyo to Kyoto is an estimate and is not costed.", "Transport from Kyoto to Osaka is an estimate and is not costed."],
  },
};

/** Kyoto's three nights split in two: the first two at one hotel and the last at another. */
export const splitStayPlan: TripPlan = {
  ...threeCityPlan,
  hotels: [
    threeCityPlan.hotels[0],
    stay("hotel_k", "Kyoto Garden Inn", kyoto, "Gion", 21400, "2027-04-13", "2027-04-15"),
    stay("hotel_k2", "Station Hostel Kyoto", kyoto, "Kyoto Station", 8900, "2027-04-15", "2027-04-16"),
    threeCityPlan.hotels[2],
  ],
};

/** The three-city plan with every stay and leg locked in: each slot shows only the option chosen. */
export const lockedThreeCityPlan: TripPlan = {
  ...threeCityPlan,
  hotels: threeCityPlan.hotels.map((stay) => ({ ...stay, locked: true })),
  legs: (threeCityPlan.legs ?? []).map((leg) => ({ ...leg, locked: true })),
};

/**
 * Tokyo is flown into and flown out of (the flight on to Kyoto is the chosen one), so it has both airport transfers:
 * the one into the city is still to choose, the one out is locked in.
 */
export const airportTransferPlan: TripPlan = {
  ...threeCityPlan,
  legs: [
    ...(threeCityPlan.legs ?? []),
    { id: "to_stay_seg_1", from_place: "HND airport", to_place: "Asakusa View Hotel", depart_date: "2027-04-10", kind: "to_stay", segment_id: "seg_1", airport: "HND" },
    { id: "from_stay_seg_1", from_place: "Asakusa View Hotel", to_place: "HND airport", depart_date: "2027-04-13", kind: "from_stay", segment_id: "seg_1", airport: "HND", locked: true },
  ],
  tickets: [
    ...threeCityPlan.tickets.map((entry) =>
      entry.leg_id === "leg_1" ? { ...entry, selected: entry.id === "leg1_flight" } : entry,
    ),
    ticket("in_train", "to_stay_seg_1", "train", "Airport train (estimate)", "2027-04-10T14:20:00", "2027-04-10T15:05:00", 45, {
      origin: "HND airport",
      destination: "Asakusa View Hotel",
      estimated: true,
    }),
    ticket("in_bus", "to_stay_seg_1", "bus", "Airport bus or shuttle (estimate)", "2027-04-10T14:20:00", "2027-04-10T15:10:00", 50, {
      origin: "HND airport",
      destination: "Asakusa View Hotel",
      estimated: true,
    }),
    ticket("in_taxi", "to_stay_seg_1", "taxi", "Taxi or ride (estimate)", "2027-04-10T14:20:00", "2027-04-10T14:55:00", 35, {
      origin: "HND airport",
      destination: "Asakusa View Hotel",
      estimated: true,
      selected: true,
    }),
    ticket("out_taxi", "from_stay_seg_1", "taxi", "Taxi or ride (estimate)", "2027-04-13T07:25:00", "2027-04-13T08:00:00", 35, {
      origin: "Asakusa View Hotel",
      destination: "HND airport",
      estimated: true,
      selected: true,
    }),
    ticket("out_bus", "from_stay_seg_1", "bus", "Airport bus or shuttle (estimate)", "2027-04-13T07:10:00", "2027-04-13T08:00:00", 50, {
      origin: "Asakusa View Hotel",
      destination: "HND airport",
      estimated: true,
    }),
  ],
};
