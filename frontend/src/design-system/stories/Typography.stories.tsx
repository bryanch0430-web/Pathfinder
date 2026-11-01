import type { Meta, StoryObj } from "@storybook/react";
import { Avatar, Heading, Text } from "../atoms";
import { Icon, type IconName } from "../icons";

const meta: Meta = { title: "Atoms/Typography & icons" };
export default meta;
type Story = StoryObj;

const ICONS: IconName[] = [
  "compass", "map", "route", "calendar", "users", "wallet", "sparkles", "plane",
  "train", "hotel", "ticket", "meal", "sun", "cloud", "rain", "search", "send",
  "check", "alert", "clock", "refresh", "lock", "chevronDown", "chevronRight",
  "close", "plus", "minus", "play", "pause", "star", "arrowRight", "globe",
  "shield", "memory", "edit", "trash", "external", "layers", "zoomIn", "zoomOut",
  "info", "book", "download", "spark",
];

export const TypeAndIcons: Story = {
  render: () => (
    <div className="pf-card" style={{ padding: 24, maxWidth: 900 }}>
      <div className="pf-stack pf-stack--md">
        <Heading level={1}>Heading one</Heading>
        <Heading level={2}>Heading two</Heading>
        <Heading level={3}>Heading three</Heading>
        <Heading level={4}>Heading four</Heading>
        <Heading level={5}>Heading five</Heading>
        <Heading level={6}>Heading six</Heading>
        <Text size="lg">Body large: the itinerary is built from verified candidates.</Text>
        <Text size="md">Body medium: every item carries a source and a freshness status.</Text>
        <Text size="sm" tone="secondary">Body small: reference fare, availability not confirmed.</Text>
        <Text size="xs" tone="tertiary">Body extra small: updated 4 minutes ago.</Text>
        <div className="pf-row pf-row--wrap" style={{ gap: 14 }}>
          {ICONS.map((name) => (
            <span key={name} className="pf-stack" style={{ alignItems: "center", gap: 4, width: 64 }}>
              <Icon name={name} size={19} />
              <span className="pf-small pf-muted pf-mono" style={{ fontSize: 9 }}>{name}</span>
            </span>
          ))}
        </div>
        <div className="pf-row">
          <Avatar name="Helen Lai" />
          <Avatar name="Chan Yuk Ming" size="lg" />
        </div>
      </div>
    </div>
  ),
};
