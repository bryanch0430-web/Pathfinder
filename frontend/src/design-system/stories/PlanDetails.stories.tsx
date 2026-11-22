import type { Meta, StoryObj } from "@storybook/react";
import { BudgetPanel, ConstraintList, GapList, PlaceCard } from "../molecules";
import { samplePlan } from "../fixtures";

const meta: Meta = { title: "Molecules/Plan details" };
export default meta;
type Story = StoryObj;

export const Budget: Story = {
  render: () => (
    <div className="pf-card" style={{ padding: 24, maxWidth: 420 }}>
      <BudgetPanel cost={samplePlan.cost} onOptimize={() => undefined} />
    </div>
  ),
};

export const Constraints: Story = {
  render: () => (
    <div className="pf-card" style={{ padding: 24, maxWidth: 520 }}>
      <ConstraintList checks={samplePlan.constraints} />
    </div>
  ),
};

export const Gaps: Story = {
  render: () => (
    <div className="pf-card" style={{ padding: 24, maxWidth: 520 }}>
      <GapList gaps={samplePlan.data_quality.gaps} />
    </div>
  ),
};

export const Place: Story = {
  render: () => (
    <div style={{ padding: 24, maxWidth: 400 }}>
      <PlaceCard item={samplePlan.days[0].items[0]} weather={samplePlan.days[0].weather} />
    </div>
  ),
};
