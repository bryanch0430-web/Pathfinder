import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "../atoms";

const meta = {
  title: "Atoms/Button",
  component: Button,
  args: { children: "Generate itinerary", variant: "primary", size: "md" },
} satisfies Meta<typeof Button>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const VariantMatrix: Story = {
  render: () => (
    <div className="pf-stack pf-stack--md" style={{ padding: 24 }}>
      {(["primary", "secondary", "ghost", "ink", "danger", "soft"] as const).map((variant) => (
        <div className="pf-row pf-row--wrap" key={variant}>
          <span className="pf-mono pf-small" style={{ width: 90 }}>{variant}</span>
          <Button variant={variant}>Plan a trip</Button>
          <Button variant={variant} iconLeft="sparkles">With icon</Button>
          <Button variant={variant} iconRight="arrowRight">Continue</Button>
          <Button variant={variant} loading>Loading</Button>
          <Button variant={variant} disabled>Disabled</Button>
        </div>
      ))}
    </div>
  ),
};

export const SizeMatrix: Story = {
  render: () => (
    <div className="pf-row pf-row--wrap" style={{ padding: 24 }}>
      <Button size="sm" variant="primary">Small</Button>
      <Button size="md" variant="primary">Medium</Button>
      <Button size="lg" variant="primary">Large</Button>
      <Button size="md" variant="secondary" iconRight="arrowRight">Full width</Button>
    </div>
  ),
};
