import type { TripPlan } from "../design-system/types";

export function planMarkdown(plan: TripPlan, disclaimer = ""): string {
  const rows: string[] = [
    `# ${plan.destination.name} — ${plan.days.length}-day trip`,
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
  plan.days.forEach((day, dayIndex) => {
    rows.push(`### Day ${dayIndex + 1} · ${day.date}${day.title ? ` — ${day.title}` : ""}`, "");
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
  });
  rows.push("## Costs", "", `Total: ${plan.cost.currency} ${Math.round(plan.cost.total).toLocaleString()}`, "");
  for (const costLine of plan.cost.lines) {
    rows.push(`- ${costLine.label}: ${plan.cost.currency} ${Math.round(costLine.amount).toLocaleString()}`);
  }
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
  return `${plan.destination.name.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}-${plan.dates.start}.${extension}`;
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
