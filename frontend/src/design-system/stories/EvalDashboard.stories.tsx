import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { EvalDashboard } from "../organisms";
import { sampleEvalRun } from "../fixtures";
import type { EvalDataset, EvalRunSummary } from "../types";

const meta: Meta = { title: "Organisms/Evaluation dashboard" };
export default meta;
type Story = StoryObj;

const datasets: EvalDataset[] = [
  { id: "hongkong", name: "Hongkong", version: "1.0", split: "dev", frozen: true, item_count: 4, created_at: "2026-10-30T08:00:00Z" },
  { id: "japan", name: "Japan", version: "1.0", split: "dev", frozen: true, item_count: 3, created_at: "2026-10-30T08:00:00Z" },
  { id: "routing", name: "Routing", version: "1.0", split: "dev", frozen: true, item_count: 20, created_at: "2026-10-30T08:00:00Z" },
  { id: "injection", name: "Injection", version: "1.0", split: "dev", frozen: true, item_count: 10, created_at: "2026-10-30T08:00:00Z" },
];

const summary = (tag: string, delta: number): EvalRunSummary => ({
  id: `run_${tag}`,
  experiment_tag: tag,
  dataset_id: "hongkong",
  dataset_version: "1.0",
  status: "completed",
  metrics: sampleEvalRun.metrics.map((metric) => ({ ...metric, value: Math.max(0, metric.value - delta) })),
  latency_ms: 2640 - delta * 10,
  token_usage: 0,
  created_at: `2026-10-${delta === 0 ? "30" : "29"}T08:00:00Z`,
  case_count: 4,
  passed: delta === 0 ? 4 : 3,
});

export const Playground: Story = {
  render: () => {
    const [running, setRunning] = useState(false);
    const [runs, setRuns] = useState<EvalRunSummary[]>([summary("hotel-agent-v2", 0), summary("baseline-v1", 15)]);
    return (
      <EvalDashboard
        datasets={datasets}
        runs={runs}
        latest={sampleEvalRun}
        running={running}
        error={null}
        onRun={(_dataset, tag) => {
          setRunning(true);
          window.setTimeout(() => {
            setRunning(false);
            setRuns((current) => [summary(tag, 0), ...current]);
          }, 800);
        }}
        onExport={() => undefined}
      />
    );
  },
};

export const Empty: Story = {
  render: () => (
    <EvalDashboard datasets={datasets} runs={[]} latest={null} onRun={() => undefined} running={false} error={null} />
  ),
};
