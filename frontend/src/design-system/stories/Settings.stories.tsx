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

/** A save failed: the reason shows above the form, which stays as typed so Save can be pressed again. */
export const SaveFailed: Story = {
  render: () => (
    <SettingsView
      config={config}
      profile={{ client_token: "guest", preferences: ["slow pace", "vegetarian"], hard_constraints: [], sessions: 3, saved_trips: 1, destinations: ["Kyoto"], updated_at: null }}
      error="Preferences could not be saved."
      onSavePreferences={() => undefined}
      onClearMemory={() => undefined}
    />
  ),
};

/** 20 preferences is the most: the field is read-only and says so. */
export const PreferenceLimit: Story = {
  render: () => (
    <SettingsView
      config={config}
      profile={{
        client_token: "guest",
        preferences: Array.from({ length: 20 }, (_, index) => `preference ${index + 1}`),
        hard_constraints: [],
        sessions: 3,
        saved_trips: 1,
        destinations: ["Kyoto"],
        updated_at: null,
      }}
      onSavePreferences={() => undefined}
    />
  ),
};
