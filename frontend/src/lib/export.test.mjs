import assert from "node:assert/strict";
import test from "node:test";

import PptxGenJS from "pptxgenjs";

import { planFileName, planMarkdown } from "./export.ts";
import { buildPlanDeck } from "./slides.ts";

const place = (name) => ({ name, country_code: "JP", geo: null, timezone: "" });
const item = (id, name) => ({
  id,
  type: "attraction",
  name,
  start: "09:00",
  end: "10:00",
  geo: null,
  address: "",
  notes: "",
  source_refs: [],
  data_status: "ok",
  price: null,
  price_kind: "none",
  locked: false,
  confirmed: false,
  tags: [],
});
const day = (date, segment_id, name) => ({ date, title: "", weather: null, items: [item(`i_${date}`, name)], note: "", segment_id });
const stay = (id, name, check_in, check_out, segment_id) => ({
  id,
  name,
  area: "Centre",
  check_in,
  check_out,
  segment_id,
  price_per_night: { amount: 1000, currency: "JPY" },
  price_kind: "reference",
  rating: 4.5,
  review_count: 10,
  booking_url: "",
  notes: "",
  confirmed: false,
});
const ticket = (id, leg_id, mode, name, extra = {}) => ({
  id,
  leg_id,
  mode,
  name,
  origin: "",
  destination: "",
  depart_at: "2026-12-05T09:00:00",
  arrive_at: "2026-12-05T11:40:00",
  duration_minutes: 160,
  price: null,
  price_kind: "none",
  operator: "",
  booking_url: "",
  confirmed: false,
  ...extra,
});

const base = {
  version: 2,
  status: "ready",
  origin: "Hong Kong",
  party_size: 2,
  budget: null,
  summary: "A trip.",
  cost: { currency: "JPY", lines: [{ label: "Hotel", amount: 4000, kind: "reference", note: "" }], total: 4000, budget: null, notes: ["Kyoto to Osaka is an estimate."] },
  data_quality: { sources_used: ["test"], gaps: [], notes: [], search_window_months: 24, generated_at: "2026-11-01T00:00:00Z" },
  hotel_options: [],
  constraints: [],
  disruptions: [],
  reservations: [],
};

function single() {
  return {
    ...base,
    destination: place("Kyoto"),
    dates: { start: "2026-12-03", end: "2026-12-04" },
    days: [day("2026-12-03", undefined, "Gion"), day("2026-12-04", undefined, "Nishiki")],
    hotels: [stay("h1", "Granvia", "2026-12-03", "2026-12-04")],
    tickets: [ticket("t1", undefined, "flight", "Cathay")],
  };
}

function twoCities() {
  return {
    ...base,
    destination: place("Kyoto"),
    dates: { start: "2026-12-03", end: "2026-12-07" },
    segments: [
      { id: "seg_a", destination: place("Kyoto"), dates: { start: "2026-12-03", end: "2026-12-05" } },
      { id: "seg_b", destination: place("Osaka"), dates: { start: "2026-12-05", end: "2026-12-07" } },
    ],
    days: [
      day("2026-12-03", "seg_a", "Gion"),
      day("2026-12-04", "seg_a", "Nishiki"),
      day("2026-12-05", "seg_b", "Dotonbori"),
      day("2026-12-06", "seg_b", "Castle"),
      day("2026-12-07", "seg_b", "Aquarium"),
    ],
    hotels: [stay("hotel_a", "Kyoto Inn", "2026-12-03", "2026-12-05", "seg_a"), stay("hotel_b", "Osaka Inn", "2026-12-05", "2026-12-07", "seg_b")],
    legs: [
      { id: "leg_out", from_place: "Hong Kong", to_place: "Kyoto", depart_date: "2026-12-03", kind: "outbound" },
      { id: "leg_ab", from_place: "Kyoto", to_place: "Osaka", depart_date: "2026-12-05", kind: "between" },
      { id: "leg_home", from_place: "Osaka", to_place: "Hong Kong", depart_date: "2026-12-07", kind: "return" },
    ],
    tickets: [
      ticket("t_out", "leg_out", "flight", "Cathay CX500", { selected: true, price: { amount: 5000, currency: "JPY" } }),
      ticket("t_train", "leg_ab", "train", "Kyoto to Osaka by train", { selected: true, estimated: true }),
      ticket("t_bus", "leg_ab", "bus", "Kyoto to Osaka by bus", { estimated: true }),
      ticket("t_home", "leg_home", "flight", "Cathay CX565", { selected: true }),
    ],
  };
}

test("a one-city plan is written as it always was", () => {
  const text = planMarkdown(single());
  assert.match(text, /^# Kyoto — 2-day trip/);
  assert.ok(text.includes("### Day 1 · 2026-12-03"));
  assert.ok(!text.includes("####") && !text.includes("Stay:"));
  assert.equal(planFileName(single(), "md"), "kyoto-2026-12-03.md");
});

test("a plan of two cities is written by the route, city by city", () => {
  const text = planMarkdown(twoCities());
  assert.match(text, /^# Kyoto → Osaka — 5-day trip/);
  const lines = text.split("\n");
  const at = (needle) => lines.findIndex((line) => line.startsWith(needle));
  assert.ok(at("#### Hong Kong → Kyoto") > 0);
  assert.ok(at("### Kyoto · 2026-12-03 – 2026-12-05 · 2 nights") > at("#### Hong Kong → Kyoto"));
  assert.ok(at("#### Kyoto → Osaka") > at("### Kyoto ·"));
  assert.ok(at("### Osaka · 2026-12-05 – 2026-12-07 · 2 nights") > at("#### Kyoto → Osaka"));
  assert.ok(at("#### Osaka → Hong Kong") > at("### Osaka ·"));
  assert.ok(text.includes("- Stay: **Osaka Inn** (Centre), 2026-12-05 → 2026-12-07 · JPY 1,000 per night"));
  assert.ok(text.includes("#### Day 3 · 2026-12-05"));
  assert.ok(text.includes("- Chosen: Kyoto to Osaka by train (train, estimate) · about 09:00 – about 11:40 · no fare"));
  assert.ok(text.includes("- Other: Kyoto to Osaka by bus (bus, estimate)"));
  assert.ok(text.includes("- Note: Kyoto to Osaka is an estimate."));
  assert.equal(planFileName(twoCities(), "md"), "kyoto-osaka-2026-12-03.md");
});

test("a deck for two cities is built, and one for one city too", async () => {
  for (const plan of [single(), twoCities()]) {
    const deck = buildPlanDeck(plan, PptxGenJS);
    assert.ok(deck.slides.length >= 5);
    const bytes = await deck.write({ outputType: "nodebuffer" });
    assert.ok(bytes.length > 10_000);
  }
  assert.equal(buildPlanDeck(twoCities(), PptxGenJS).title, "Kyoto → Osaka travel plan");
  assert.equal(buildPlanDeck(single(), PptxGenJS).title, "Kyoto travel plan");
});
