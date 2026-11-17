/** The slice of a trip the sketchbook layout reads. Mirrors the LangGraph sketch document. */
export interface TripDocumentItem {
  name: string;
  category: string;
  time_of_day: "morning" | "afternoon" | "evening" | null;
  area: string | null;
  est_cost: number | null;
  duration_hours: number | null;
  /** Clock range for the stop, such as 08:00–08:45. */
  when: string | null;
  /** The hop into this stop, such as "7 min walk". */
  leg: string | null;
  tip: string | null;
  source_url: string | null;
  option: boolean;
}

export interface TripDocument {
  destination: string | null;
  origin: string | null;
  duration_days: number | null;
  start_date: string | null;
  end_date: string | null;
  travelers: { adults: number; children: number };
  travel_mode: string;
  interests: string[];
  avoid: string[];
  must_see: string[];
  days: {
    day: number;
    date: string | null;
    base: string;
    items: TripDocumentItem[];
    est_cost: number | null;
    hours: number | null;
  }[];
  legs: { source: string; target: string; mode: string | null; duration: string | null; distance: string | null; cost: string | null }[];
  budget: {
    currency: string;
    target: number | null;
    planned: number | null;
    entered: number;
    projected: number;
    is_locked?: boolean;
    locked_total?: number | null;
  };
  enrichment: {
    weather: {
      day: number;
      date: string;
      base: string;
      kind: "forecast" | "typical";
      label: string;
      condition: "sun" | "partly" | "cloud" | "fog" | "rain" | "snow" | "storm" | null;
      temp_min: number | null;
      temp_max: number | null;
      temp_mean: number | null;
      rain_mm: number | null;
      badge: { text: string } | null;
    }[];
    holidays: { day: number; name: string }[];
  } | null;
}
