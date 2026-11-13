import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { TransportLegCard, TripWorkspace } from "../organisms";
import { splitStayPlan, threeCityPlan } from "../multiCityFixtures";
import type { ChoiceKind } from "../../lib/api";
import type { PlanItem, TripPlan } from "../types";

const meta: Meta = { title: "Organisms/Multi-city trip" };
export default meta;
type Story = StoryObj;

const versions = [{ version: 2, change_summary: "Added Osaka", created_at: "2026-10-30T08:00:00Z" }];

function Workspace({ initial }: { initial: TripPlan }) {
  const [plan, setPlan] = useState<TripPlan>(initial);
  const [selected, setSelected] = useState<PlanItem | null>(null);
  const [changedLegs, setChangedLegs] = useState<ReadonlySet<string>>(new Set());
  const [changedStays, setChangedStays] = useState<ReadonlySet<string>>(new Set());
  const choose = async (kind: ChoiceKind, targetId: string | null, optionId: string) => {
    if (kind === "transport" || kind === "flight") {
      const legId = plan.tickets.find((ticket) => ticket.id === optionId)?.leg_id ?? "";
      setPlan({ ...plan, tickets: plan.tickets.map((t) => (t.leg_id === legId ? { ...t, selected: t.id === optionId } : t)) });
      setChangedLegs(new Set([legId]));
    }
    if (kind === "hotel") {
      const option = plan.hotel_options?.find((entry) => entry.id === optionId);
      if (!option) return;
      setPlan({ ...plan, hotels: plan.hotels.map((h) => (h.id === targetId ? { ...option, check_in: h.check_in, check_out: h.check_out } : h)) });
      setChangedStays(new Set([optionId]));
    }
  };
  return (
    <div style={{ padding: 24, maxWidth: 760 }}>
      <TripWorkspace
        plan={plan}
        versions={versions}
        activeVersion={2}
        onVersionChange={() => undefined}
        selectedItemId={selected?.id ?? null}
        onSelectItem={setSelected}
        onUseful={() => undefined}
        onNotUseful={() => undefined}
        feedbackStars={null}
        feedbackVerdict={null}
        onChooseOption={choose}
        changedLegIds={changedLegs}
        changedStayIds={changedStays}
        onAnswerQuestion={() => undefined}
      />
    </div>
  );
}

/** Three cities: a heading with dates and nights for each, its stay and the stay's options, and the leg into it. */
export const ThreeCities: Story = { render: () => <Workspace initial={threeCityPlan} /> };

/** A city with two stays: Kyoto's first two nights at one hotel, the last at another. */
export const SplitStay: Story = { render: () => <Workspace initial={splitStayPlan} /> };

/** One leg with a flight, an estimated train and an estimated drive: any mode can be chosen, estimates say so. */
export const LegWithFlightAndEstimatedTrain: Story = {
  render: () => {
    const [options, setOptions] = useState(threeCityPlan.tickets.filter((ticket) => ticket.leg_id === "leg_1"));
    const leg = threeCityPlan.legs?.find((entry) => entry.id === "leg_1");
    if (!leg) return <p>The fixture has no leg.</p>;
    const chosen = options.find((option) => option.selected) ?? options[0];
    return (
      <div style={{ padding: 24, maxWidth: 640 }}>
        <TransportLegCard
          leg={leg}
          options={options}
          chosenId={chosen.id}
          onChoose={async (optionId) => setOptions(options.map((option) => ({ ...option, selected: option.id === optionId })))}
        />
      </div>
    );
  },
};
