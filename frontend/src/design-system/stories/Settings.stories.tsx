import type { Meta, StoryObj } from "@storybook/react";
import { SettingsView } from "../organisms";

const meta: Meta = { title: "Organisms/Settings" };
export default meta;
type Story = StoryObj;

const config = {
  thresholds: { router_confidence: 0.8, memory_similarity: 0.72, preplan_budget_seconds: 90, max_repairs: 2 },
  freshness_hours: { weather: 6, tickets: 24, hotels: 168, attractions: 720 },
  writer: { available: true, model: "grok-4.20-0309-non-reasoning" },
  providers: {
    geocoder: ["open-meteo-geocoder", "nominatim"],
    weather: ["open-meteo"],
    places: ["google-places", "wikivoyage", "wikipedia-geosearch"],
    hotels: ["serpapi-hotels", "wikivoyage-hotels"],
    flights: ["serpapi-flights"],
    notes: ["youtube", "bilibili"],
    routes: ["google-routes"],
  },
  locales: ["en", "zh-Hans", "zh-Hant"],
};

export const Playground: Story = {
  render: () => <SettingsView config={config} injections={[{ category: "system_prompt_extraction", severity: "high", count: 3 }]} />,
};
