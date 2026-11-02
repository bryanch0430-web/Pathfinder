import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { MapPanel } from "../organisms";
import { samplePlan } from "../fixtures";
import type { PlanItem } from "../types";

const meta: Meta = { title: "Organisms/Map panel" };
export default meta;
type Story = StoryObj;

export const Playground: Story = {
  render: () => {
    const [active, setActive] = useState(samplePlan.days[0].date);
    const [selected, setSelected] = useState<PlanItem | null>(samplePlan.days[0].items[0]);
    return (
      <div style={{ padding: 24, maxWidth: 520 }}>
        <MapPanel
          plan={samplePlan}
          activeDay={active}
          onActiveDay={setActive}
          selectedItemId={selected?.id ?? null}
          onSelectItem={setSelected}
          onOptimize={() => undefined}
        />
      </div>
    );
  },
};

export const EmptySelection: Story = {
  render: () => {
    const [active, setActive] = useState(samplePlan.days[1].date);
    return (
      <div style={{ padding: 24, maxWidth: 520 }}>
        <MapPanel plan={samplePlan} activeDay={active} onActiveDay={setActive} selectedItemId={null} onSelectItem={() => undefined} />
      </div>
    );
  },
};
