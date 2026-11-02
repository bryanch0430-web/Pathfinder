import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { DaySection, DayStrip } from "../molecules";
import { samplePlan } from "../fixtures";
import type { PlanItem } from "../types";

const meta: Meta = { title: "Molecules/Itinerary" };
export default meta;
type Story = StoryObj;

export const Days: Story = {
  render: () => {
    const [active, setActive] = useState(samplePlan.days[0].date);
    const [selected, setSelected] = useState<PlanItem | null>(null);
    const day = samplePlan.days.find((entry) => entry.date === active) ?? samplePlan.days[0];
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 720 }}>
        <DayStrip days={samplePlan.days} active={active} onSelect={setActive} />
        <div style={{ height: 12 }} />
        <DaySection
          day={day}
          index={samplePlan.days.indexOf(day)}
          selectedItemId={selected?.id}
          onSelectItem={setSelected}
        />
        <div className="pf-banner" data-tone="info" style={{ marginTop: 12 }}>
          Selected: {selected ? selected.name : "none"}
        </div>
      </div>
    );
  },
};

export const Unavailable: Story = {
  render: () => {
    const day = {
      ...samplePlan.days[1],
      items: samplePlan.days[1].items.map((item, index) =>
        index === 1 ? { ...item, data_status: "unavailable" as const } : item,
      ),
    };
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 720 }}>
        <DaySection day={day} index={1} />
      </div>
    );
  },
};
