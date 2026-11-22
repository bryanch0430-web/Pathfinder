import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { TripContextForm } from "../molecules";
import { sampleContext } from "../fixtures";
import { TRIP_LINES_MAX } from "../../lib/limits";
import type { TripContext } from "../types";

const meta: Meta = { title: "Molecules/Trip Context Form" };
export default meta;
type Story = StoryObj;

export const Playground: Story = {
  render: () => {
    const [values, setValues] = useState<Partial<TripContext>>(sampleContext);
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 560 }}>
        <TripContextForm
          values={values}
          onChange={(patch) => setValues((current) => ({ ...current, ...patch }))}
          onSubmit={() => undefined}
          onSave={() => undefined}
        />
      </div>
    );
  },
};

/** Fifty preferences and fifty constraints: both fields say the list is full instead of dropping the next line. */
export const AtLineLimit: Story = {
  render: () => {
    const [values, setValues] = useState<Partial<TripContext>>({
      ...sampleContext,
      preferences: Array.from({ length: TRIP_LINES_MAX }, (_, index) => `preference ${index + 1}`),
      hard_constraints: Array.from({ length: TRIP_LINES_MAX }, (_, index) => `constraint ${index + 1}`),
    });
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 560 }}>
        <TripContextForm
          values={values}
          onChange={(patch) => setValues((current) => ({ ...current, ...patch }))}
          onSubmit={() => undefined}
          onSave={() => undefined}
        />
      </div>
    );
  },
};

export const Compact: Story = {
  render: () => {
    const [values, setValues] = useState<Partial<TripContext>>(sampleContext);
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 420 }}>
        <TripContextForm
          compact
          values={values}
          onChange={(patch) => setValues((current) => ({ ...current, ...patch }))}
          onSubmit={() => undefined}
        />
      </div>
    );
  },
};

/** A trip of three cities in order, each with the nights the traveller gave it (the last one left open). */
export const ThreeCities: Story = {
  render: () => {
    const [values, setValues] = useState<Partial<TripContext>>({
      ...sampleContext,
      destination: "Tokyo",
      stops: [
        { city: "Tokyo", nights: 4 },
        { city: "Kyoto", nights: 3 },
        { city: "Osaka", nights: null },
      ],
    });
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 560 }}>
        <TripContextForm
          values={values}
          onChange={(patch) => setValues((current) => ({ ...current, ...patch }))}
          onSubmit={() => undefined}
          onSave={() => undefined}
        />
      </div>
    );
  },
};
