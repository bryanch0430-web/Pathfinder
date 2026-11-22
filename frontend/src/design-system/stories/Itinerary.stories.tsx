import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { DaySection, DayStrip, ItemAlternatives, ItineraryItem } from "../molecules";
import { optionsPlan, samplePlan } from "../fixtures";
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

const mealWithOptions = optionsPlan.days.flatMap((day) => day.items).find((entry) => entry.id === "item_m3") as PlanItem;

/** A lunch stop with its slot label, source facts, the walk from the previous stop and two backups. The second "Use this" fails. */
export const MealWithAlternatives: Story = {
  render: () => (
    <div className="pf-card" style={{ padding: 24, maxWidth: 720 }}>
      <ItineraryItem item={mealWithOptions} number={3} />
      <ItemAlternatives
        item={mealWithOptions}
        defaultOpen
        onChoose={(id) =>
          new Promise((resolve, reject) =>
            window.setTimeout(() => (id === "alt_m3b" ? reject(new Error("This stop is locked or confirmed; unlock it before choosing another.")) : resolve()), 700),
          )
        }
      />
    </div>
  ),
};
