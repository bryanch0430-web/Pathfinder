import type { Meta, StoryObj } from "@storybook/react";
import { SettingsView } from "../organisms";
import type { LlmKey, LlmProvider, LlmStatus } from "../../lib/api";

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

const providers: LlmProvider[] = [
  { id: "anthropic", label: "Anthropic", default_model: "claude-sonnet-5-5", key_prefix: "sk-ant-", supports_search: false },
  { id: "openai", label: "OpenAI", default_model: "gpt-5", key_prefix: "sk-", supports_search: false },
  { id: "xai", label: "xAI", default_model: "grok-4.20-0309-non-reasoning", key_prefix: "xai-", supports_search: true },
];

const savedKeys: LlmKey[] = [
  {
    id: "k1", provider: "anthropic", label: "Anthropic", model: "claude-sonnet-5-5", key_tail: "a1b2", active: true,
    use_for_search: false, last_test_ok: true, last_tested_at: "2026-11-08T09:30:00Z", last_error: "", unreadable: false,
  },
  {
    id: "k2", provider: "xai", label: "xAI", model: "grok-4.20-0309-non-reasoning", key_tail: "9z8y", active: false,
    use_for_search: true, last_test_ok: false, last_tested_at: "2026-11-07T18:00:00Z", last_error: "The provider refused the key.", unreadable: false,
  },
];

const noLocal = { configured: false, reachable: false, model: "" };
const byokStatus: LlmStatus = { source: "byok", provider: "anthropic", model: "claude-sonnet-5-5", local: noLocal };
const serverStatus: LlmStatus = { source: "server", provider: "xai", model: "grok-4.20-0309-non-reasoning", local: noLocal };

const handlers = {
  onTest: async () => ({ ok: true, kind: "ok" as const, message: "", latency_ms: 420, models: ["claude-sonnet-5-5", "claude-haiku-4-5"] }),
  onSave: async () => ({}),
  onRetest: async () => ({ ok: true, kind: "ok" as const, message: "", latency_ms: 380, models: [] }),
  onPatch: async () => undefined,
  onChoose: async () => undefined,
  onDelete: async () => undefined,
};

/** A traveller with two saved keys, one in use, and the add form below them. */
export const ModelKeys: Story = {
  render: () => <SettingsView config={config} modelKeys={{ providers, keys: savedKeys, status: byokStatus, ...handlers }} />,
};

/** No key of one's own and no model on the server: the rule-based path answers. */
export const ModelKeysNone: Story = {
  render: () => (
    <SettingsView
      config={config}
      modelKeys={{ providers, keys: [], status: { source: "none", provider: "", model: "", local: noLocal }, ...handlers }}
    />
  ),
};

/** An operator with the admin token also sees the Server key card. */
export const ModelKeysWithServerCard: Story = {
  render: () => (
    <SettingsView
      config={config}
      modelKeys={{ providers, keys: [], status: serverStatus, ...handlers }}
      serverKeys={{ providers, keys: savedKeys.slice(1), status: serverStatus, ...handlers }}
    />
  ),
};

/** A load or change failed: the reason shows in the card. */
export const ModelKeysFailed: Story = {
  render: () => (
    <SettingsView
      config={config}
      modelKeys={{ providers, keys: savedKeys, status: byokStatus, error: "The key change could not be made.", ...handlers }}
    />
  ),
};
