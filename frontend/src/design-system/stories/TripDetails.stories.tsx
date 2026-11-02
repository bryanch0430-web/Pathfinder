import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { TripPanel } from "../organisms";
import { sampleContext, samplePlan } from "../fixtures";
import type { TripContext } from "../types";

const meta: Meta = { title: "Organisms/Trip details" };
export default meta;
type Story = StoryObj;

function Harness({ initialOpen = false }: { initialOpen?: boolean }) {
  const [context, setContext] = useState<Partial<TripContext>>(sampleContext);
  return (
    <div style={{ width: 380 }}>
      <TripPanel
        context={context}
        plan={samplePlan}
        onChange={(patch) => setContext((current) => ({ ...current, ...patch }))}
        onSave={() => undefined}
        onGenerate={() => undefined}
        initialOpen={initialOpen}
      />
    </div>
  );
}

export const Collapsed: Story = {
  render: () => <Harness />,
};

export const OpenDrawer: Story = {
  render: () => <Harness initialOpen />,
};
