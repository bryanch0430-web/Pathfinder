import assert from "node:assert/strict";
import test from "node:test";

import { dayRoute, dayScope, distanceKm, overviewStops, travelLine, withinKm } from "./mapScope.ts";

const place = (name, lat, lng) => ({ name, country_code: "JP", geo: lat === undefined ? null : { lat, lng }, timezone: "" });
const stay = (id, check_in, check_out, segment_id, geo) => ({ id, name: id, check_in, check_out, segment_id, geo });

function plan() {
  return {
    destination: place("Kyoto", 35.01, 135.77),
    origin: "Hong Kong",
    dates: { start: "2026-12-03", end: "2026-12-07" },
    segments: [
      { id: "seg_a", destination: place("Kyoto", 35.01, 135.77), dates: { start: "2026-12-03", end: "2026-12-05" } },
      { id: "seg_b", destination: place("Osaka", 34.69, 135.5), dates: { start: "2026-12-05", end: "2026-12-07" } },
    ],
    days: [],
    hotels: [
      stay("hotel_a", "2026-12-03", "2026-12-05", "seg_a", { lat: 35.0, lng: 135.76 }),
      stay("hotel_b", "2026-12-05", "2026-12-07", "seg_b", { lat: 34.7, lng: 135.51 }),
    ],
    legs: [
      { id: "leg_out", from_place: "Hong Kong", to_place: "Kyoto", depart_date: "2026-12-03", kind: "outbound" },
      { id: "leg_ab", from_place: "Kyoto", to_place: "Osaka", depart_date: "2026-12-05", kind: "between" },
    ],
    tickets: [
      { id: "t_train", leg_id: "leg_ab", mode: "train", name: "train", selected: true },
      { id: "t_bus", leg_id: "leg_ab", mode: "bus", name: "bus", selected: false },
    ],
  };
}

test("a day route leaves the stay, visits every stop, and returns", () => {
  const hotel = { id: "hotel_a", type: "hotel", name: "Stay", start: "00:00", end: "00:10", geo: { lat: 35.0, lng: 135.76 } };
  const stops = [
    { id: "late", type: "attraction", name: "Late", start: "15:00", end: "16:00", geo: { lat: 35.01, lng: 135.77 } },
    { id: "early", type: "meal", name: "Early", start: "09:00", end: "10:00", geo: { lat: 35.02, lng: 135.78 } },
    { id: "transit_from_stay_x", type: "transit", name: "Stay → KIX airport", start: "18:00", end: "19:00", geo: null },
  ];
  const chain = dayRoute(stops, hotel);
  assert.deepEqual(chain.map((item) => item.id), ["hotel_a", "early", "late", "hotel_a"]);
});

test("an arrival taxi with a point is before the stay, and a departure taxi ends the day", () => {
  const hotel = { id: "hotel_a", type: "hotel", name: "Stay", start: "00:00", end: "00:10", geo: { lat: 22.32, lng: 114.17 } };
  const stops = [
    { id: "transit_to_stay_seg", type: "transit", name: "HKG airport → Stay", start: "21:20", end: "21:55", geo: { lat: 22.308, lng: 113.918 } },
    { id: "sight", type: "attraction", name: "Harbour", start: "10:00", end: "11:00", geo: { lat: 22.29, lng: 114.17 } },
    { id: "transit_from_stay_seg", type: "transit", name: "Stay → HKG airport", start: "19:00", end: "19:40", geo: { lat: 22.308, lng: 113.918 } },
  ];
  assert.deepEqual(dayRoute(stops, hotel).map((item) => item.id), [
    "transit_to_stay_seg",
    "hotel_a",
    "sight",
    "transit_from_stay_seg",
  ]);
});

test("kilometres between two cities", () => {
  const km = distanceKm({ lat: 35.01, lng: 135.77 }, { lat: 34.69, lng: 135.5 });
  assert.ok(km > 35 && km < 50, String(km));
});

test("a stop within 150 km of its city is kept and one far away is not", () => {
  const kyoto = { lat: 35.01, lng: 135.77 };
  assert.equal(withinKm(kyoto, { lat: 34.69, lng: 135.5 }), true);
  assert.equal(withinKm(kyoto, { lat: 35.68, lng: 139.69 }), false);
  assert.equal(withinKm(null, { lat: 35.68, lng: 139.69 }), true);
  assert.equal(withinKm(kyoto, null), true);
});

test("the scope of a day is the centre of its city and the stay of that night", () => {
  const trip = plan();
  const first = dayScope(trip, "2026-12-04");
  assert.equal(first.segment.id, "seg_a");
  assert.deepEqual(first.centre, { lat: 35.01, lng: 135.77 });
  assert.equal(first.stay?.id, "hotel_a");
  const shared = dayScope(trip, "2026-12-05");
  assert.equal(shared.segment.id, "seg_b");
  assert.equal(shared.stay?.id, "hotel_b");
});

test("a stay far from its city is left off the map", () => {
  const trip = plan();
  trip.hotels[1].geo = { lat: 35.68, lng: 139.69 };
  assert.equal(dayScope(trip, "2026-12-06").stay, null);
});

test("a city with no coordinates has no centre and keeps everything", () => {
  const trip = plan();
  trip.segments[1].destination.geo = null;
  const scope = dayScope(trip, "2026-12-06");
  assert.equal(scope.centre, null);
  assert.equal(withinKm(scope.centre, { lat: 0, lng: 0 }), true);
});

test("only the day a city is reached draws the line from the city before", () => {
  const trip = plan();
  const line = travelLine(trip, "2026-12-05");
  assert.equal(line?.mode, "train");
  assert.equal(line?.label, "Kyoto → Osaka");
  assert.deepEqual(line?.from, { lat: 35.01, lng: 135.77 });
  assert.equal(travelLine(trip, "2026-12-04"), null);
  assert.equal(travelLine(trip, "2026-12-06"), null);
  trip.segments[0].destination.geo = null;
  assert.equal(travelLine(trip, "2026-12-05"), null);
});

test("the whole trip is every city with coordinates, numbered in order", () => {
  const trip = plan();
  assert.deepEqual(
    overviewStops(trip).map((s) => [s.number, s.name]),
    [
      [1, "Kyoto"],
      [2, "Osaka"],
    ],
  );
  trip.segments[0].destination.geo = null;
  assert.deepEqual(
    overviewStops(trip).map((s) => [s.number, s.name]),
    [[2, "Osaka"]],
  );
  delete trip.segments;
  assert.deepEqual(
    overviewStops(trip).map((s) => s.name),
    ["Kyoto"],
  );
});
