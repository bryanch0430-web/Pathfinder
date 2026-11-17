import { isPendingChoice } from "../pendingChoice";
import type { PlanItem, TripPlan, WeatherDay } from "../types";
import type { TripDocument, TripDocumentItem } from "./document";

const slotOf = (start: string): TripDocumentItem["time_of_day"] => {
  const hour = Number(start.slice(0, 2));
  if (!Number.isFinite(hour)) return null;
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  return "evening";
};

const hoursBetween = (start: string, end: string) => {
  const read = (value: string) => {
    const match = value.match(/^(\d{1,2}):(\d{2})/);
    return match ? Number(match[1]) * 60 + Number(match[2]) : null;
  };
  const from = read(start);
  const to = read(end);
  if (from === null || to === null || to <= from) return null;
  return Math.round(((to - from) / 60) * 10) / 10;
};

const categoryOf = (item: PlanItem) => {
  if (item.type === "meal") return "food";
  if (item.type === "hotel") return "stay";
  if (item.type === "transit") return "transit";
  if (item.type === "ticket") return "experience";
  return "sight";
};

const skyOf = (weather: WeatherDay): "sun" | "partly" | "cloud" | "fog" | "rain" | "snow" | "storm" | null => {
  const text = `${weather.summary} ${weather.note ?? ""}`.toLowerCase();
  if (/storm|thunder/.test(text)) return "storm";
  if (/snow/.test(text)) return "snow";
  if (/rain|shower|drizzle/.test(text)) return "rain";
  if (/fog|mist/.test(text)) return "fog";
  if (/cloud|overcast/.test(text)) return "cloud";
  if (/partly|partial/.test(text)) return "partly";
  if (/sun|clear/.test(text)) return "sun";
  if ((weather.precipitation_probability ?? 0) >= 60) return "rain";
  return weather.summary ? "partly" : null;
};

const hopMode = (kind: string) => {
  if (kind === "outbound" || kind === "return") return "flight";
  if (kind === "to_stay" || kind === "from_stay") return "taxi";
  return "train";
};

/** The router's plan, in the shape the sketchbook pages are drawn from. */
export function planToDocument(plan: TripPlan): TripDocument {
  const currency = plan.cost?.currency || plan.budget?.currency || "USD";
  const days = plan.days.map((day, index) => {
    const items = day.items.map((item): TripDocumentItem => {
      const pending = isPendingChoice(item.notes);
      const hop = item.transfer;
      return {
        name: item.name,
        category: categoryOf(item),
        time_of_day: slotOf(item.start),
        area: item.address ? item.address.split(",")[0]?.trim() || null : null,
        est_cost: item.price?.amount ?? null,
        duration_hours: hoursBetween(item.start, item.end),
        when: item.start && item.end ? `${item.start}–${item.end}` : null,
        leg: hop ? `${hop.minutes} min ${hop.mode}` : null,
        tip: pending ? null : item.notes?.trim() || null,
        source_url: item.booking_url || item.source_refs[0]?.url || null,
        // The sketch draws the stop that is on the plan now. Alternatives stay in the plan.
        option: false,
      };
    });
    const costs = items.map((item) => item.est_cost).filter((value): value is number => value !== null);
    const hours = items.map((item) => item.duration_hours).filter((value): value is number => value !== null);
    const segment = plan.segments?.find((entry) => entry.id === day.segment_id);
    return {
      day: index + 1,
      date: day.date,
      base: segment?.destination.name || plan.destination.name,
      items,
      est_cost: costs.length ? costs.reduce((sum, value) => sum + value, 0) : null,
      hours: hours.length ? Math.round(hours.reduce((sum, value) => sum + value, 0) * 10) / 10 : null,
      weather: day.weather,
    };
  });
  return {
    destination: plan.destination.name || null,
    origin: plan.origin || null,
    duration_days: plan.days.length || null,
    start_date: plan.dates.start || null,
    end_date: plan.dates.end || null,
    travelers: { adults: plan.party_size || 1, children: 0 },
    travel_mode: plan.legs?.[0] ? hopMode(plan.legs[0].kind) : "transit",
    interests: [],
    avoid: [],
    must_see: plan.days.flatMap((day) => day.items.filter((item) => item.confirmed).map((item) => item.name)).slice(0, 8),
    days: days.map(({ weather: _weather, ...day }) => day),
    legs: (plan.legs ?? [])
      .filter((leg) => leg.kind === "outbound" || leg.kind === "between" || leg.kind === "return")
      .map((leg) => ({
        source: leg.from_place,
        target: leg.to_place,
        mode: hopMode(leg.kind),
        duration: null,
        distance: null,
        cost: null,
      })),
    budget: {
      currency,
      target: plan.budget?.amount ?? plan.cost?.budget ?? null,
      planned: plan.cost?.total ?? null,
      entered: 0,
      projected: plan.cost?.total ?? 0,
    },
    enrichment: {
      holidays: [],
      weather: days.flatMap((day) =>
        day.weather
          ? [{
              day: day.day,
              date: day.date || "",
              base: day.base,
              kind: "forecast" as const,
              label: day.weather.summary || "Weather",
              condition: skyOf(day.weather),
              temp_min: day.weather.low_c,
              temp_max: day.weather.high_c,
              temp_mean: day.weather.low_c !== null && day.weather.high_c !== null
                ? (day.weather.low_c + day.weather.high_c) / 2
                : null,
              rain_mm: null,
              badge: null,
            }]
          : [],
      ),
    },
  };
}
