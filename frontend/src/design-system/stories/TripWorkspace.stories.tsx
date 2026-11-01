import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { TripWorkspace } from "../organisms";
import { samplePlan } from "../fixtures";
import type { PlanItem, RunTrace } from "../types";

const meta: Meta = { title: "Organisms/Trip workspace" };
export default meta;
type Story = StoryObj;

const versions = [
  { version: 2, change_summary: "Swapped the hotel", created_at: "2026-10-30T08:00:00Z" },
  { version: 1, change_summary: "Initial plan", created_at: "2026-10-30T07:30:00Z" },
];

const trace: RunTrace = {
  request_id: "req_8b2b12031486",
  spans: [
    { name: "understand", duration_ms: 18, status: "ok" },
    { name: "route", duration_ms: 240, status: "ok" },
    { name: "agent.attraction", duration_ms: 1280, status: "ok" },
    { name: "agent.hotel", duration_ms: 1120, status: "ok" },
    { name: "agent.weather", duration_ms: 86, status: "ok" },
    { name: "agent.ticket", duration_ms: 960, status: "ok" },
    { name: "llm.planner", duration_ms: 2140, status: "ok" },
  ],
  generations: [{ purpose: "planner", model: "grok-4.20-0309-non-reasoning", prompt_tokens: 4200, completion_tokens: 320, latency_ms: 2140 }],
  tool_calls: 7,
};

export const Playground: Story = {
  render: () => {
    const [selected, setSelected] = useState<PlanItem | null>(null);
    return (
      <div style={{ padding: 24, maxWidth: 760 }}>
        <TripWorkspace
          plan={samplePlan}
          versions={versions}
          activeVersion={2}
          onVersionChange={() => undefined}
          selectedItemId={selected?.id ?? null}
          onSelectItem={setSelected}
          onUseful={() => undefined}
          onNotUseful={() => undefined}
          feedbackStars={null}
          feedbackVerdict={null}
          trace={trace}
        />
      </div>
    );
  },
};
