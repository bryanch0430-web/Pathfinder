import type { Meta, StoryObj } from "@storybook/react";
import { Heading, Text } from "../atoms";

const meta: Meta = {
  title: "Foundations/Design Tokens",
  parameters: { layout: "fullscreen" },
};
export default meta;

const COLORS = [
  ["brand-primary", "var(--p30-tone-brand-primary)"],
  ["brand-secondary", "var(--p30-tone-brand-secondary)"],
  ["brand-muted", "var(--p30-tone-brand-muted)"],
  ["bgLayout", "var(--p30-tone-neutral-bgLayout)"],
  ["bgContainer", "var(--p30-tone-neutral-bgContainer)"],
  ["gray100", "var(--p30-tone-neutral-gray100)"],
  ["gray200", "var(--p30-tone-neutral-gray200)"],
  ["gray500", "var(--p30-tone-neutral-gray500)"],
  ["gray800", "var(--p30-tone-neutral-gray800)"],
  ["gray900", "var(--p30-tone-neutral-gray900)"],
  ["success", "var(--p30-tone-feedback-success)"],
  ["warning", "var(--p30-tone-feedback-warning)"],
  ["danger", "var(--p30-tone-feedback-danger)"],
  ["info", "var(--p30-tone-feedback-info)"],
  ["busy", "var(--p30-tone-feedback-busy)"],
];

function TokenPage() {
  return (
    <div style={{ padding: 32, maxWidth: 1100 }}>
      <div className="pf-stack pf-stack--lg">
        <div className="pf-stack">
          <Text size="xs" tone="tertiary" className="pf-mono">FOUNDATIONS</Text>
          <Heading level={1}>Storybook Pathfinder</Heading>
          <Text size="lg" tone="secondary">
            The design language for an agentic trip planner: warm paper, white cards, a red decision accent,
            neumorphic depth, and a single type scale. Tokens are the contract; components consume only tokens.
          </Text>
        </div>

        <section className="pf-stack">
          <Heading level={3}>Colour</Heading>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 12 }}>
            {COLORS.map(([name, value]) => (
              <div key={name} className="pf-card" style={{ overflow: "hidden" }}>
                <div style={{ height: 64, background: value, borderBottom: "1px solid var(--pf-line)" }} />
                <div style={{ padding: 10 }}>
                  <Text size="xs" weight="semibold">{name}</Text>
                  <Text size="xs" tone="tertiary" className="pf-mono">{value}</Text>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="pf-stack">
          <Heading level={3}>Type scale</Heading>
          <div className="pf-card" style={{ padding: 24 }}>
            <Heading level={1}>Heading 1 · 38</Heading>
            <Heading level={2}>Heading 2 · 30</Heading>
            <Heading level={3}>Heading 3 · 24</Heading>
            <Heading level={4}>Heading 4 · 20</Heading>
            <Heading level={5}>Heading 5 · 16</Heading>
            <Heading level={6}>Heading 6 · 14</Heading>
            <div style={{ height: 12 }} />
            <Text size="xl">Body XL · 20 — plan the day, keep the sources.</Text>
            <Text size="lg">Body LG · 16 — the itinerary is built from verified candidates.</Text>
            <Text size="md">Body MD · 14 — the default reading size of the console.</Text>
            <Text size="sm">Body SM · 12 — metadata, captions and status lines.</Text>
            <Text size="xs" tone="tertiary">Body XS · 12 — timestamps and source labels.</Text>
          </div>
        </section>

        <section className="pf-stack">
          <Heading level={3}>Spacing</Heading>
          <div className="pf-card" style={{ padding: 24 }}>
            {[
              ["xxs", 2],
              ["xs", 4],
              ["sm", 8],
              ["md", 16],
              ["lg", 24],
              ["xl", 32],
              ["xxl", 48],
              ["xxxl", 64],
            ].map(([name, value]) => (
              <div key={name} className="pf-row" style={{ gap: 12, marginBottom: 6 }}>
                <span className="pf-mono pf-small" style={{ width: 46 }}>{name}</span>
                <span style={{ width: Number(value), height: 10, background: "var(--p30-tone-brand-primary)", borderRadius: 2 }} />
                <span className="pf-small pf-muted">{value}px</span>
              </div>
            ))}
          </div>
        </section>

        <section className="pf-stack">
          <Heading level={3}>Depth</Heading>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 16 }}>
            {["xs", "sm", "raised", "md", "lg", "xl", "inset", "pressed"].map((name) => (
              <div
                key={name}
                className="pf-card"
                style={{ height: 92, display: "grid", placeItems: "center", boxShadow: `var(--p30-depth-${name})` }}
              >
                <Text size="xs" className="pf-mono">shadow-{name}</Text>
              </div>
            ))}
          </div>
        </section>

        <section className="pf-stack">
          <Heading level={3}>Radius & motion</Heading>
          <div className="pf-row pf-row--wrap">
            {["xs", "sm", "md", "lg", "xl", "pill"].map((name) => (
              <div key={name} className="pf-card" style={{ width: 108, height: 72, display: "grid", placeItems: "center", borderRadius: `var(--p30-round-${name})` }}>
                <Text size="xs" className="pf-mono">{name}</Text>
              </div>
            ))}
          </div>
          <Text size="sm" tone="secondary">
            Motion: 100 ms / 200 ms / 300 ms with easeOut for surfaces, easeOutBack for switches.
          </Text>
        </section>
      </div>
    </div>
  );
}

export const Showcase: StoryObj = { render: () => <TokenPage /> };
