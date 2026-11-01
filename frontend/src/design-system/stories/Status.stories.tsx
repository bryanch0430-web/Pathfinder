import type { Meta, StoryObj } from "@storybook/react";
import { Badge, Heading, Avatar, Spinner } from "../atoms";
import { AgentChip, IntentBadge, StatusPill } from "../elements";

const meta: Meta = { title: "Elements/Status & intent" };
export default meta;
type Story = StoryObj;

export const Playground: Story = {
  render: () => (
    <div className="pf-card" style={{ padding: 24 }}>
      <div className="pf-stack pf-stack--md">
        <Heading level={4}>Data status</Heading>
        <div className="pf-row pf-row--wrap">
          <StatusPill status="ok" />
          <StatusPill status="stale" />
          <StatusPill status="unavailable" />
        </div>

        <Heading level={4}>Route intent</Heading>
        <div className="pf-row pf-row--wrap">
          <IntentBadge intent="plan" />
          <IntentBadge intent="modify" />
          <IntentBadge intent="ask" />
          <IntentBadge intent="unclear" />
        </div>

        <Heading level={4}>Agents</Heading>
        <div className="pf-row pf-row--wrap">
          <AgentChip agent="attraction" status="running" />
          <AgentChip agent="hotel" status="ok" detail="4 candidates" />
          <AgentChip agent="weather" status="unavailable" />
          <AgentChip agent="ticket" status="partial" />
        </div>

        <Heading level={4}>Badges</Heading>
        <div className="pf-row pf-row--wrap">
          {(["neutral", "brand", "success", "warning", "danger", "info", "busy", "ink"] as const).map((tone) => (
            <Badge key={tone} tone={tone} dot>{tone}</Badge>
          ))}
        </div>

        <Heading level={4}>Avatars & spinner</Heading>
        <div className="pf-row">
          <Avatar name="Helen Lai" />
          <Avatar name="Chan Yuk Ming" size="lg" />
          <Spinner />
          <Spinner size="lg" />
        </div>
      </div>
    </div>
  ),
};
