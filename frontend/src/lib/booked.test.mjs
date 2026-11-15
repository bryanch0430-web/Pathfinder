import assert from "node:assert/strict";
import test from "node:test";

import JSZip from "jszip";
import PptxGenJS from "pptxgenjs";

import { bookedLines, isBookedTicket } from "./booked.ts";
import { planMarkdown } from "./export.ts";
import { buildPlanDeck } from "./slides.ts";

const place = (name) => ({ name, country_code: "JP", geo: null, timezone: "" });
const day = (date, segment_id) => ({ date, title: "", weather: null, items: [], note: "", segment_id });
const stay = (id, name, check_in, check_out, segment_id, extra = {}) => ({
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
  locked: false,
  ...extra,
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

function twoCities({ booked = true } = {}) {
  return {
    version: 2,
    status: "ready",
    origin: "Hong Kong",
    party_size: 2,
    budget: null,
    summary: "A trip.",
    cost: { currency: "JPY", lines: [], total: 0, budget: null, notes: [] },
    data_quality: { sources_used: ["test"], gaps: [], notes: [], search_window_months: 24, generated_at: "2026-11-01T00:00:00Z" },
    hotel_options: [],
    constraints: [],
    disruptions: [],
    reservations: [],
    destination: place("Kyoto"),
    dates: { start: "2026-12-03", end: "2026-12-07" },
    segments: [
      { id: "seg_a", destination: place("Kyoto"), dates: { start: "2026-12-03", end: "2026-12-05" } },
      { id: "seg_b", destination: place("Osaka"), dates: { start: "2026-12-05", end: "2026-12-07" } },
    ],
    days: ["2026-12-03", "2026-12-04"].map((date) => day(date, "seg_a")).concat(
      ["2026-12-05", "2026-12-06", "2026-12-07"].map((date) => day(date, "seg_b")),
    ),
    hotels: [
      stay("hotel_a", "Kyoto Inn", "2026-12-03", "2026-12-05", "seg_a", { locked: booked, booked }),
      stay("hotel_b", "Osaka Inn", "2026-12-05", "2026-12-07", "seg_b"),
    ],
    legs: [
      { id: "leg_out", from_place: "Hong Kong", to_place: "Kyoto", depart_date: "2026-12-03", kind: "outbound" },
      { id: "leg_ab", from_place: "Kyoto", to_place: "Osaka", depart_date: "2026-12-05", kind: "between", locked: booked, booked },
      { id: "leg_home", from_place: "Osaka", to_place: "Hong Kong", depart_date: "2026-12-07", kind: "return" },
    ],
    tickets: [
      ticket("t_out", "leg_out", "flight", "Cathay CX500", { selected: true }),
      ticket("t_train", "leg_ab", "train", "Kyoto to Osaka by train", { selected: true, estimated: true }),
      ticket("t_bus", "leg_ab", "bus", "Kyoto to Osaka by bus", { estimated: true }),
      ticket("t_home", "leg_home", "flight", "Cathay CX565", { selected: true }),
    ],
  };
}

function oneCity() {
  const plan = twoCities();
  return {
    ...plan,
    segments: undefined,
    dates: { start: "2026-12-03", end: "2026-12-05" },
    days: [day("2026-12-03"), day("2026-12-04")],
    hotels: [stay("h1", "Granvia", "2026-12-03", "2026-12-05", undefined, { locked: true, booked: true })],
    legs: [],
    tickets: [ticket("t1", undefined, "flight", "Cathay")],
  };
}

async function deckText(plan) {
  const bytes = await buildPlanDeck(plan, PptxGenJS).write({ outputType: "nodebuffer" });
  const zip = await JSZip.loadAsync(bytes);
  const names = Object.keys(zip.files).filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name));
  const texts = await Promise.all(names.map((name) => zip.file(name).async("string")));
  return texts.join("\n");
}

test("the booked lines name each booked stay and leg, and nothing when none is booked", () => {
  assert.deepEqual(bookedLines(twoCities()), [
    "Kyoto Inn · stay 2026-12-03 → 2026-12-05",
    "Kyoto → Osaka · 2026-12-05 · Kyoto to Osaka by train",
  ]);
  assert.deepEqual(bookedLines(twoCities({ booked: false })), []);
});

test("a stay or leg marked booked but not locked is not counted as booked", () => {
  const plan = twoCities();
  plan.hotels[0].locked = false;
  plan.legs[1].locked = false;
  assert.deepEqual(bookedLines(plan), []);
});

test("only the chosen option of a booked leg is a booked ticket", () => {
  const plan = twoCities();
  assert.equal(isBookedTicket(plan, plan.tickets[1]), true);
  assert.equal(isBookedTicket(plan, plan.tickets[2]), false);
  assert.equal(isBookedTicket(plan, plan.tickets[0]), false);
});

test("the text export marks booked stays and legs and lists them", () => {
  const text = planMarkdown(twoCities());
  assert.ok(text.includes("- Stay: **Kyoto Inn** (Centre), 2026-12-03 → 2026-12-05 · JPY 1,000 per night · Booked"));
  assert.ok(text.includes("#### Kyoto → Osaka · 2026-12-05 · Booked"));
  assert.ok(text.includes("- Stay: **Osaka Inn** (Centre), 2026-12-05 → 2026-12-07 · JPY 1,000 per night\n"));
  assert.ok(text.includes("## Booked\n\n- Kyoto Inn · stay 2026-12-03 → 2026-12-05\n- Kyoto → Osaka · 2026-12-05 · Kyoto to Osaka by train"));
  assert.ok(text.includes("This app made no booking; what is marked Booked the traveller booked."));
});

test("a one-city plan lists its booked stay, and a plan with nothing booked is written as before", () => {
  const text = planMarkdown(oneCity());
  assert.ok(text.includes("## Booked\n\n- Granvia · stay 2026-12-03 → 2026-12-05"));
  const plain = planMarkdown(twoCities({ booked: false }));
  assert.ok(!plain.includes("Booked"));
  assert.ok(plain.endsWith("Prices and availability are reference estimates unless marked live. No booking was made."));
});

test("the slides mark the booked stay and leg", async () => {
  const text = await deckText(twoCities());
  assert.ok(text.includes("✓ Booked"));
  assert.ok((text.match(/>Booked</g) ?? []).length >= 2);
  const plain = await deckText(twoCities({ booked: false }));
  assert.ok(!plain.includes("Booked<") && !plain.includes("✓ Booked"));
});
