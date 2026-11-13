import type { HotelStay, TicketOption, TransportLeg, TripPlan } from "../design-system/types";
import { chosenFor, daysBySegment, legHome, legInto, nightsBetween, optionsFor, routeLabel, segmentsOf, staysIn } from "./segments";

function about(value: string | null | undefined): string {
  return value ? `about ${value.replace("T", " ").slice(11, 16)}` : "";
}

/** One way of making a leg, on a line: the name, the mode, the times (about, for an estimate) and the fare. */
function optionText(option: TicketOption, currency: string): string {
  const times = option.estimated
    ? [about(option.depart_at), about(option.arrive_at)].filter(Boolean).join(" – ")
    : [option.depart_at, option.arrive_at].filter(Boolean).map((value) => String(value).replace("T", " ").slice(0, 16)).join(" – ");
  const fare = option.price ? `${option.price.currency || currency} ${Math.round(option.price.amount).toLocaleString()}` : "no fare";
  return [`${option.name} (${option.mode}${option.estimated ? ", estimate" : ""})`, times, fare].filter(Boolean).join(" · ");
}

function legRows(plan: TripPlan, leg: TransportLeg): string[] {
  const options = optionsFor(plan, leg.id);
  const chosen = chosenFor(plan, leg.id);
  const rows = [`#### ${leg.from_place} → ${leg.to_place}${leg.depart_date ? ` · ${leg.depart_date}` : ""}`, ""];
  if (!chosen) return [...rows, "No transport was found for this leg.", ""];
  rows.push(`- Chosen: ${optionText(chosen, plan.cost.currency)}`);
  for (const option of options.filter((entry) => entry !== chosen)) rows.push(`- Other: ${optionText(option, plan.cost.currency)}`);
  return [...rows, ""];
}

function stayRow(stay: HotelStay): string {
  const rate = stay.price_per_night
    ? `${stay.price_per_night.currency} ${stay.price_per_night.amount.toLocaleString()} per night`
    : "no rate returned";
  return `- Stay: **${stay.name}**${stay.area ? ` (${stay.area})` : ""}, ${stay.check_in} → ${stay.check_out} · ${rate}`;
}

export function planMarkdown(plan: TripPlan, disclaimer = ""): string {
  const multi = segmentsOf(plan).length > 1;
  const rows: string[] = [
    `# ${routeLabel(plan)} — ${plan.days.length}-day trip`,
    "",
    ...(disclaimer ? [`> ${disclaimer}`, ""] : []),
    `${plan.dates.start} to ${plan.dates.end} · ${plan.party_size} ${plan.party_size === 1 ? "traveller" : "travellers"} · budget ${plan.cost.budget ?? "not set"} ${plan.cost.currency}`,
    "",
    "## Overview",
    "",
    plan.summary || "No overview was written.",
    "",
    "## Itinerary",
    "",
  ];
  const addDays = (days: TripPlan["days"], heading: string) => {
    for (const day of days) {
      const dayIndex = plan.days.findIndex((entry) => entry.date === day.date);
      rows.push(`${heading} Day ${dayIndex + 1} · ${day.date}${day.title ? ` — ${day.title}` : ""}`, "");
      if (day.weather) rows.push(`Weather: ${day.weather.summary} (${day.weather.status})`, "");
      for (const item of day.items) {
        rows.push(
          `- **${item.start || "—"}–${item.end || "—"} · ${item.name}** (${item.type})`,
          `  - ${item.notes || "No description."}`,
          item.address ? `  - Address: ${item.address}` : "",
          ...item.source_refs.map(
            (source) => `  - Source: ${source.source}${source.url ? ` — ${source.url}` : ""}`,
          ),
        );
      }
      if (day.note) rows.push("", `Day note: ${day.note}`);
      rows.push("");
    }
  };
  if (multi) {
    // The cities in order: the leg into each, its stay or stays, then its days.
    for (const { segment, days } of daysBySegment(plan)) {
      const leg = legInto(plan, segment.id);
      if (leg) rows.push(...legRows(plan, leg));
      const nights = nightsBetween(segment.dates.start, segment.dates.end);
      rows.push(
        `### ${segment.destination.name} · ${segment.dates.start} – ${segment.dates.end} · ${nights} ${nights === 1 ? "night" : "nights"}`,
        "",
      );
      const stays = staysIn(plan, segment.id);
      if (stays.length) rows.push(...stays.map(stayRow), "");
      addDays(days, "####");
    }
    const home = legHome(plan);
    if (home) rows.push(...legRows(plan, home));
  } else {
    addDays(plan.days, "###");
  }
  rows.push("## Costs", "", `Total: ${plan.cost.currency} ${Math.round(plan.cost.total).toLocaleString()}`, "");
  for (const costLine of plan.cost.lines) {
    rows.push(`- ${costLine.label}: ${plan.cost.currency} ${Math.round(costLine.amount).toLocaleString()}`);
  }
  for (const note of multi ? plan.cost.notes : []) rows.push(`- Note: ${note}`);
  rows.push(
    "",
    "## Sources",
    "",
    ...plan.data_quality.sources_used.map((source) => `- ${source}`),
    "",
    "Prices and availability are reference estimates unless marked live. No booking was made.",
  );
  return rows.join("\n");
}

export function planFileName(plan: TripPlan, extension: string): string {
  const name = segmentsOf(plan)
    .map((segment) => segment.destination.name)
    .join(" ")
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/g, "-");
  return `${name}-${plan.dates.start}.${extension}`;
}

export function downloadPlanMarkdown(plan: TripPlan, disclaimer = ""): void {
  const blob = new Blob([planMarkdown(plan, disclaimer)],{ type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = planFileName(plan, "md");
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
