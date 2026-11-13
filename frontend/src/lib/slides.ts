import type PptxGenJS from "pptxgenjs";
import type { DayPlan, Money, PlanItem, TripPlan } from "../design-system/types";
import { planFileName } from "./export";
import { legsOf, optionsFor, routeLabel, segmentOfDay, segmentsOf } from "./segments";
import { safeHref } from "./safeHref";

// Colours follow tokens.css (brand red on warm paper).
const BRAND = "CC0D0D";
const INK = "2B2822";
const MUTED = "5C574D";
const PAPER = "F4F2EE";
const RULE = "E2DED5";
const OK = "1E7B4A";
const FONT = "Arial";

// LAYOUT_WIDE is 13.33 x 7.5 inches.
const W = 13.33;
const X = 0.5;
const CONTENT_W = W - X * 2;
const ROWS_PER_DAY_SLIDE = 6;
const ROWS_PER_TABLE_SLIDE = 9;
const NOTE_LIMIT = 220;

const TYPE_LABEL: Record<PlanItem["type"], string> = {
  attraction: "Sight",
  hotel: "Stay",
  meal: "Meal",
  transit: "Transit",
  ticket: "Ticket",
};

function money(amount: number, currency: string): string {
  return `${currency} ${Math.round(amount).toLocaleString("en-US")}`;
}

function priceLabel(price: Money | null, kind: string): string {
  if (!price) return "";
  const label = money(price.amount, price.currency);
  return kind === "none" || kind === "live" ? label : `${label} (${kind})`;
}

function dateLabel(iso: string): string {
  const parsed = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

// Times are shown as the plan states them (local to the leg), without time-zone conversion.
function dateTimeLabel(iso: string | null | undefined): string {
  return iso ? iso.replace("T", " ").slice(0, 16) : "—";
}

function duration(minutes: number | null | undefined): string {
  if (!minutes) return "—";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m`;
}

function nights(checkIn: string, checkOut: string): number {
  const span = Date.parse(`${checkOut}T00:00:00Z`) - Date.parse(`${checkIn}T00:00:00Z`);
  return Number.isFinite(span) ? Math.max(0, Math.round(span / 86_400_000)) : 0;
}

function clip(text: string, limit = NOTE_LIMIT): string {
  const flat = text.replaceAll(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1).trimEnd()}…` : flat;
}

function weatherLabel(day: DayPlan): string {
  const weather = day.weather;
  if (!weather || weather.status !== "ok") return "";
  const parts = [weather.summary];
  if (weather.low_c !== null && weather.high_c !== null) parts.push(`${Math.round(weather.low_c)}–${Math.round(weather.high_c)}°C`);
  if (weather.precipitation_probability !== null) parts.push(`${weather.precipitation_probability}% rain`);
  return parts.filter(Boolean).join(", ");
}

// Only absolute web links become clickable: a relative path means nothing inside a deck.
function webLink(url: string | undefined): string | undefined {
  return url && /^https?:\/\//i.test(url.trim()) ? safeHref(url) : undefined;
}

function chunk<T>(rows: T[], size: number): T[][] {
  const pages: T[][] = [];
  for (let index = 0; index < rows.length; index += size) pages.push(rows.slice(index, index + size));
  return pages.length ? pages : [[]];
}

type Cell = PptxGenJS.TableCell;

function head(labels: string[]): Cell[] {
  return labels.map((text) => ({ text, options: { bold: true, color: "FFFFFF", fill: { color: INK } } }));
}

function cell(text: string, options: PptxGenJS.TableCellProps = {}): Cell {
  return { text, options };
}

function stripe(rows: Cell[][]): Cell[][] {
  return rows.map((row, index) =>
    index % 2 ? row.map((entry) => ({ ...entry, options: { fill: { color: PAPER }, ...entry.options } })) : row,
  );
}

const TABLE_STYLE: PptxGenJS.TableProps = {
  x: X,
  y: 1.45,
  w: CONTENT_W,
  fontFace: FONT,
  fontSize: 12,
  color: INK,
  valign: "top",
  margin: 0.07,
  border: { type: "solid", pt: 0.75, color: RULE },
};

function contentSlide(deck: PptxGenJS, title: string, subtitle = ""): PptxGenJS.Slide {
  const slide = deck.addSlide({ masterName: "PF_CONTENT" });
  slide.addText(title, { x: X, y: 0.3, w: CONTENT_W, h: 0.65, fontFace: FONT, fontSize: 26, bold: true, color: INK, fit: "shrink" });
  if (subtitle) {
    slide.addText(subtitle, { x: X, y: 0.92, w: CONTENT_W, h: 0.4, fontFace: FONT, fontSize: 13, color: MUTED });
  }
  return slide;
}

function addCover(deck: PptxGenJS, plan: TripPlan): void {
  const slide = deck.addSlide();
  slide.background = { color: PAPER };
  slide.addShape(deck.ShapeType.rect, { x: 0, y: 0, w: 0.35, h: 7.5, fill: { color: BRAND }, line: { color: BRAND } });
  slide.addText("PATHFINDER TRAVEL PLAN", { x: 1, y: 1.6, w: 11, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: BRAND, charSpacing: 3 });
  slide.addText(routeLabel(plan), { x: 1, y: 2.1, w: 11.3, h: 1.3, fontFace: FONT, fontSize: 54, bold: true, color: INK, fit: "shrink" });
  const days = plan.days.length;
  slide.addText(`${dateLabel(plan.dates.start)} – ${dateLabel(plan.dates.end)} · ${days} ${days === 1 ? "day" : "days"}`, {
    x: 1, y: 3.5, w: 11, h: 0.5, fontFace: FONT, fontSize: 20, color: INK,
  });
  const facts = [
    `${plan.party_size} ${plan.party_size === 1 ? "traveller" : "travellers"}`,
    plan.origin ? `from ${plan.origin}` : "",
    plan.budget ? `budget ${money(plan.budget.amount, plan.budget.currency)}` : "",
  ].filter(Boolean);
  slide.addText(facts.join(" · "), { x: 1, y: 4.05, w: 11, h: 0.45, fontFace: FONT, fontSize: 16, color: MUTED });
  slide.addText(`Plan version ${plan.version} · ${plan.status === "ready" ? "ready" : "draft"} · exported ${new Date().toISOString().slice(0, 10)}`, {
    x: 1, y: 6.6, w: 11, h: 0.35, fontFace: FONT, fontSize: 11, color: MUTED,
  });
}

function addGlance(deck: PptxGenJS, plan: TripPlan): void {
  const slide = contentSlide(deck, "Trip at a glance", routeLabel(plan));
  const cost = plan.cost;
  const tiles: [string, string][] = [
    [String(plan.days.length), plan.days.length === 1 ? "day" : "days"],
    [String(plan.days.reduce((sum, day) => sum + day.items.length, 0)), "planned stops"],
    [String(plan.hotels.length), plan.hotels.length === 1 ? "place to stay" : "places to stay"],
    [cost.total ? money(cost.total, cost.currency) : "—", "estimated total"],
  ];
  const tileW = (CONTENT_W - 0.3 * 3) / 4;
  tiles.forEach(([value, label], index) => {
    const x = X + index * (tileW + 0.3);
    slide.addShape(deck.ShapeType.rect, { x, y: 1.5, w: tileW, h: 1.3, fill: { color: PAPER }, line: { color: RULE } });
    slide.addText(value, { x, y: 1.6, w: tileW, h: 0.7, align: "center", fontFace: FONT, fontSize: 26, bold: true, color: BRAND, fit: "shrink" });
    slide.addText(label, { x, y: 2.3, w: tileW, h: 0.4, align: "center", fontFace: FONT, fontSize: 12, color: MUTED });
  });
  slide.addText(plan.summary || "No overview was written for this plan.", {
    x: X, y: 3.1, w: CONTENT_W, h: 2.6, valign: "top", fontFace: FONT, fontSize: 16, color: INK, lineSpacingMultiple: 1.2, fit: "shrink",
  });
  if (cost.budget !== null && cost.status !== "unknown") {
    const over = cost.status === "over";
    const remaining = cost.remaining ?? cost.budget - cost.total;
    slide.addText(
      over
        ? `Over budget by ${money(Math.abs(remaining), cost.currency)} (budget ${money(cost.budget, cost.currency)})`
        : `Within budget — ${money(Math.max(0, remaining), cost.currency)} left of ${money(cost.budget, cost.currency)}`,
      { x: X, y: 5.9, w: CONTENT_W, h: 0.5, fontFace: FONT, fontSize: 15, bold: true, color: over ? BRAND : OK },
    );
  }
}

function addAgenda(deck: PptxGenJS, plan: TripPlan): void {
  const rows = plan.days.map((day, index) => [
    cell(`Day ${index + 1}`, { bold: true }),
    cell(dateLabel(day.date)),
    cell(day.title || "—"),
    cell(clip(day.items.map((item) => item.name).join(" → "), 160)),
    cell(weatherLabel(day) || "—", { color: MUTED }),
  ]);
  chunk(rows, ROWS_PER_TABLE_SLIDE).forEach((page, index) => {
    const slide = contentSlide(deck, index ? "Day by day (continued)" : "Day by day", "The whole trip on one page");
    slide.addTable([head(["Day", "Date", "Theme", "Route", "Weather"]), ...stripe(page)], {
      ...TABLE_STYLE,
      colW: [0.85, 1.75, 2.9, 4.6, 2.23],
    });
  });
}

function addTickets(deck: PptxGenJS, plan: TripPlan): void {
  if (!plan.tickets.length) return;
  const rows: Cell[][] = [];
  const multi = segmentsOf(plan).length > 1;
  // A trip of several cities lists the options of each leg in turn, the chosen one first.
  const listed = multi
    ? legsOf(plan).flatMap((leg) =>
        [...optionsFor(plan, leg.id)]
          .sort((a, b) => Number(Boolean(b.selected)) - Number(Boolean(a.selected)))
          .map((ticket) => ({ ticket, route: `${leg.from_place} → ${leg.to_place}` })),
      )
    : plan.tickets.map((ticket) => ({ ticket, route: `${ticket.origin || "—"} → ${ticket.destination || "—"}` }));
  for (const { ticket, route } of listed) {
    const link = webLink(ticket.booking_url);
    const when = (iso: string | null | undefined) => (ticket.estimated && iso ? `about ${dateTimeLabel(iso).slice(11)}` : dateTimeLabel(iso));
    rows.push([
      cell(`${ticket.name}\n${ticket.mode}${ticket.estimated ? " · estimate" : ""}${ticket.operator ? ` · ${ticket.operator}` : ""}${multi && ticket.selected ? " · chosen" : ""}`, { bold: true, ...(link ? { hyperlink: { url: link } } : {}) }),
      cell(route),
      cell(when(ticket.depart_at)),
      cell(when(ticket.arrive_at)),
      cell(duration(ticket.duration_minutes)),
      cell(priceLabel(ticket.price, ticket.price_kind) || "—"),
      cell(ticket.confirmed ? "Confirmed" : "Not booked", { color: ticket.confirmed ? OK : BRAND }),
    ]);
    if (ticket.return_depart_at) {
      rows.push([
        cell(`Return${ticket.return_flight_number ? ` · ${ticket.return_flight_number}` : ""}${ticket.return_airline ? `\n${ticket.return_airline}` : ""}`),
        cell(`${ticket.return_origin || ticket.destination || "—"} → ${ticket.return_destination || ticket.origin || "—"}`),
        cell(dateTimeLabel(ticket.return_depart_at)),
        cell(dateTimeLabel(ticket.return_arrive_at)),
        cell(duration(ticket.return_duration_minutes)),
        cell("included"),
        cell(""),
      ]);
    }
  }
  chunk(rows, ROWS_PER_TABLE_SLIDE - 2).forEach((page, index) => {
    const slide = contentSlide(deck, index ? "Getting there (continued)" : "Getting there", "Flights, trains and tickets in this plan");
    slide.addTable([head(["Option", "Route", "Departs", "Arrives", "Duration", "Price", "Status"]), ...stripe(page)], {
      ...TABLE_STYLE,
      colW: [2.9, 2.5, 1.65, 1.65, 1.0, 1.43, 1.2],
    });
  });
}

function addHotels(deck: PptxGenJS, plan: TripPlan): void {
  if (!plan.hotels.length) return;
  const multi = segmentsOf(plan).length > 1;
  const rows = plan.hotels.map((hotel) => {
    const link = webLink(hotel.booking_url);
    const stay = nights(hotel.check_in, hotel.check_out);
    const rating = hotel.rating !== null ? `${hotel.rating.toFixed(1)}★${hotel.review_count ? ` (${hotel.review_count.toLocaleString("en-US")})` : ""}` : "—";
    return [
      cell(hotel.name, { bold: true, ...(link ? { hyperlink: { url: link } } : {}) }),
      cell(multi ? [segmentOfDay(plan, hotel.check_in).destination.name, hotel.area].filter(Boolean).join(" · ") : hotel.area || "—"),
      cell(`${dateLabel(hotel.check_in)} → ${dateLabel(hotel.check_out)}\n${stay} ${stay === 1 ? "night" : "nights"}`),
      cell(priceLabel(hotel.price_per_night, hotel.price_kind) || "—"),
      cell(rating),
      cell(clip(hotel.notes || "", 140) || "—", { color: MUTED }),
    ];
  });
  chunk(rows, ROWS_PER_TABLE_SLIDE - 2).forEach((page, index) => {
    const slide = contentSlide(deck, index ? "Where you'll stay (continued)" : "Where you'll stay");
    slide.addTable([head(["Hotel", "Area", "Dates", "Per night", "Rating", "Notes"]), ...stripe(page)], {
      ...TABLE_STYLE,
      colW: [2.6, 1.6, 2.6, 1.7, 1.1, 2.73],
    });
  });
}

function itemDetails(item: PlanItem): string {
  const notes = (item.notes || "").trim();
  // Offline places often repeat the address as the note; show it once.
  const address = item.address && !notes.includes(item.address.trim()) ? `📍 ${clip(item.address, 90)}` : "";
  return [clip(notes), address].filter(Boolean).join("\n");
}

function speakerNotes(day: DayPlan, items: PlanItem[]): string {
  const lines = items.map((item) => {
    const extra = [
      item.address ? `Address: ${item.address}` : "",
      webLink(item.booking_url) ? `Booking: ${item.booking_url}` : "",
      ...item.source_refs.map((source) => `Source: ${source.source}${source.url ? ` — ${source.url}` : ""}`),
    ].filter(Boolean);
    return [`${item.start || "—"}–${item.end || "—"} ${item.name}`, item.notes, ...extra].filter(Boolean).join("\n");
  });
  return [day.note ? `Day note: ${day.note}` : "", ...lines].filter(Boolean).join("\n\n");
}

function addDay(deck: PptxGenJS, day: DayPlan, dayIndex: number, city = ""): void {
  const pages = chunk(day.items, ROWS_PER_DAY_SLIDE);
  const subtitle = [city, dateLabel(day.date), weatherLabel(day)].filter(Boolean).join(" · ");
  pages.forEach((items, pageIndex) => {
    const title = `Day ${dayIndex + 1}${day.title ? ` · ${day.title}` : ""}${pageIndex ? " (continued)" : ""}`;
    const slide = contentSlide(deck, title, subtitle);
    if (!items.length) {
      slide.addText("Nothing is scheduled for this day yet — a free day.", { x: X, y: 1.6, w: CONTENT_W, h: 0.6, fontFace: FONT, fontSize: 16, color: MUTED });
    } else {
      const rows = items.map((item) => {
        const link = webLink(item.booking_url);
        return [
          cell(`${item.start || "—"}\n${item.end || ""}`, { bold: true, color: BRAND }),
          {
            text: [
              { text: item.name, options: { bold: true, breakLine: true, ...(link ? { hyperlink: { url: link } } : {}) } },
              { text: `${TYPE_LABEL[item.type] ?? item.type}${item.confirmed ? " · confirmed" : ""}${item.locked ? " · locked" : ""}`, options: { fontSize: 11, color: MUTED } },
            ],
          },
          cell(itemDetails(item) || "—"),
          cell(priceLabel(item.price, item.price_kind) || "—", { color: MUTED }),
        ];
      });
      slide.addTable([head(["Time", "Stop", "Details", "Cost"]), ...stripe(rows)], {
        ...TABLE_STYLE,
        fontSize: 13,
        margin: 0.1,
        colW: [0.95, 3.1, 6.6, 1.68],
      });
    }
    if (day.note && pageIndex === pages.length - 1) {
      slide.addText(`Note: ${clip(day.note, 260)}`, { x: X, y: 6.35, w: CONTENT_W, h: 0.5, fontFace: FONT, fontSize: 12, italic: true, color: MUTED });
    }
    slide.addNotes(speakerNotes(day, items));
  });
}

function addBudget(deck: PptxGenJS, plan: TripPlan): void {
  const cost = plan.cost;
  if (!cost.lines.length && !cost.total) return;
  const slide = contentSlide(deck, "Budget", `All amounts in ${cost.currency}; estimates unless marked live`);
  const lines = cost.lines.slice(0, 10);
  const rows = lines.map((line) => [
    cell(line.label),
    cell(line.kind, { color: MUTED }),
    cell(money(line.amount, cost.currency), { align: "right" }),
  ]);
  rows.push([cell("Total", { bold: true }), cell(""), cell(money(cost.total, cost.currency), { bold: true, align: "right" })]);
  if (cost.budget !== null) {
    rows.push([cell("Budget"), cell(""), cell(money(cost.budget, cost.currency), { align: "right" })]);
    if (cost.remaining !== null) {
      rows.push([
        cell(cost.remaining < 0 ? "Over by" : "Remaining", { bold: true, color: cost.remaining < 0 ? BRAND : OK }),
        cell(""),
        cell(money(Math.abs(cost.remaining), cost.currency), { bold: true, align: "right", color: cost.remaining < 0 ? BRAND : OK }),
      ]);
    }
  }
  slide.addTable([head(["Item", "Kind", "Amount"]), ...stripe(rows)], { ...TABLE_STYLE, w: 5.9, colW: [2.9, 1.2, 1.8] });
  if (lines.length) {
    // Horizontal bars draw the first category at the bottom, so reverse to match the table.
    const bars = [...lines].reverse();
    slide.addChart(deck.ChartType.bar, [{ name: "Cost", labels: bars.map((line) => line.label), values: bars.map((line) => Math.round(line.amount)) }], {
      x: 6.8, y: 1.45, w: 6.0, h: 4.6,
      barDir: "bar",
      barGapWidthPct: 80,
      chartColors: [BRAND],
      // Headroom so the largest value label is not clipped at the plot edge.
      valAxisMaxVal: Math.max(...bars.map((line) => line.amount)) * 1.25,
      catAxisLabelFontFace: FONT,
      catAxisLabelFontSize: 11,
      valAxisHidden: true,
      valGridLine: { style: "none" },
      showValue: true,
      dataLabelFontSize: 10,
      dataLabelFormatCode: "#,##0",
    });
  }
  if (cost.notes.length) {
    slide.addText(clip(cost.notes.join(" "), 300), { x: X, y: 6.2, w: CONTENT_W, h: 0.6, fontFace: FONT, fontSize: 11, color: MUTED });
  }
}

const RESERVATION_STATUS: Record<TripPlan["reservations"][number]["status"], string> = {
  to_book: "☐ To book",
  optional: "○ Optional",
  booked: "✓ Booked",
};

function addChecklist(deck: PptxGenJS, plan: TripPlan): void {
  const rows: Cell[][] = plan.reservations.map((reservation) => {
    const link = webLink(reservation.url);
    return [
      cell(RESERVATION_STATUS[reservation.status], { bold: true, color: reservation.status === "booked" ? OK : reservation.status === "to_book" ? BRAND : MUTED }),
      cell(reservation.name, { bold: true, ...(link ? { hyperlink: { url: link } } : {}) }),
      cell(clip(reservation.reason, 160)),
      cell(reservation.needed_by ? dateLabel(reservation.needed_by) : "—"),
    ];
  });
  for (const disruption of plan.disruptions.filter((entry) => !entry.resolved)) {
    rows.push([cell("⚠ Watch", { bold: true, color: BRAND }), cell(disruption.kind, { bold: true }), cell(clip(disruption.summary, 160)), cell("—")]);
  }
  for (const check of plan.constraints.filter((entry) => entry.status !== "pass")) {
    rows.push([
      cell(check.status === "fail" ? "✗ Not met" : "? Check", { bold: true, color: check.status === "fail" ? BRAND : MUTED }),
      cell(clip(check.text, 80), { bold: true }),
      cell(clip(check.evidence || "", 160) || "—"),
      cell("—"),
    ]);
  }
  if (!rows.length) return;
  chunk(rows, ROWS_PER_TABLE_SLIDE).forEach((page, index) => {
    const slide = contentSlide(deck, index ? "Before you go (continued)" : "Before you go", "Bookings to make and things to double-check");
    slide.addTable([head(["Status", "What", "Why", "Needed by"]), ...stripe(page)], { ...TABLE_STYLE, colW: [1.5, 3.2, 5.7, 1.93] });
  });
}

function addSources(deck: PptxGenJS, plan: TripPlan): void {
  const slide = contentSlide(deck, "Sources and notes");
  const quality = plan.data_quality;
  const bullets = [
    ...quality.sources_used.map((source) => `Source: ${source}`),
    ...quality.gaps.map((gap) => `Missing data (${gap.section}): ${gap.message}`),
    ...quality.notes,
  ].slice(0, 14);
  slide.addText(
    (bullets.length ? bullets : ["No sources were recorded for this plan."]).map((text) => ({ text: clip(text, 180), options: { bullet: true, breakLine: true } })),
    { x: X, y: 1.45, w: CONTENT_W, h: 4.6, valign: "top", fontFace: FONT, fontSize: 13, color: INK, paraSpaceAfter: 4, fit: "shrink" },
  );
  slide.addText("Prices and availability are reference estimates unless marked live. No booking was made.", {
    x: X, y: 6.25, w: CONTENT_W, h: 0.45, fontFace: FONT, fontSize: 12, bold: true, color: BRAND,
  });
}

/** Builds the deck without touching the DOM, so it can run in a browser or in Node. */
export function buildPlanDeck(plan: TripPlan, Pptx: typeof PptxGenJS, disclaimer = ""): PptxGenJS {
  const deck = new Pptx();
  deck.layout = "LAYOUT_WIDE";
  deck.author = "Pathfinder";
  deck.title = `${routeLabel(plan)} travel plan`;
  deck.subject = `${plan.dates.start} to ${plan.dates.end}`;
  deck.theme = { headFontFace: FONT, bodyFontFace: FONT };
  deck.defineSlideMaster({
    title: "PF_CONTENT",
    background: { color: "FFFFFF" },
    objects: [
      { rect: { x: 0, y: 0, w: W, h: 0.12, fill: { color: BRAND }, line: { color: BRAND } } },
      { line: { x: X, y: 6.95, w: CONTENT_W, h: 0, line: { color: RULE, width: 0.75 } } },
      { text: { text: `Pathfinder · ${routeLabel(plan)} · ${plan.dates.start} to ${plan.dates.end}`, options: { x: X, y: 7.0, w: 9, h: 0.3, fontFace: FONT, fontSize: 10, color: MUTED } } },
      // A translated deck says so on every content slide.
      ...(disclaimer
        ? [{ text: { text: disclaimer, options: { x: X, y: 6.72, w: CONTENT_W, h: 0.22, fontFace: FONT, fontSize: 8, color: MUTED, fit: "shrink" as const } } }]
        : []),
    ],
    slideNumber: { x: 12.2, y: 7.0, w: 0.65, h: 0.3, fontFace: FONT, fontSize: 10, color: MUTED, align: "right" },
  });
  addCover(deck, plan);
  addGlance(deck, plan);
  addAgenda(deck, plan);
  addTickets(deck, plan);
  addHotels(deck, plan);
  const multi = segmentsOf(plan).length > 1;
  plan.days.forEach((day, index) => addDay(deck, day, index, multi ? segmentOfDay(plan, day.date).destination.name : ""));
  addBudget(deck, plan);
  addChecklist(deck, plan);
  addSources(deck, plan);
  return deck;
}

/** Loads pptxgenjs on demand so the main bundle stays small, then saves the deck. */
export async function downloadPlanSlides(plan: TripPlan, disclaimer = ""): Promise<void> {
  const { default: Pptx } = await import("pptxgenjs");
  await buildPlanDeck(plan, Pptx, disclaimer).writeFile({ fileName: planFileName(plan, "pptx"), compression: true });
}
