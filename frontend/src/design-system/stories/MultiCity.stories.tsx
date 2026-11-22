import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ChoiceSlot, TicketCard, TripWorkspace } from "../organisms";
import { airportTransferPlan, lockedThreeCityPlan, splitStayPlan, threeCityPlan } from "../multiCityFixtures";
import { samplePlan } from "../fixtures";
import { slotState, transportKinds } from "../../lib/choices";
import type { ChoiceKind } from "../../lib/api";
import type { PlanItem, TicketOption, TripPlan } from "../types";

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
  // Lock in chooses the option and locks the slot; Change unlocks it: nothing is a new version.
  const lockChoice = async (kind: "hotel" | "transport", targetId: string, optionId: string) => {
    await choose(kind, targetId, optionId);
    setPlan((current) =>
      kind === "hotel"
        ? { ...current, hotels: current.hotels.map((h) => (h.id === targetId || h.id === optionId ? { ...h, locked: true } : h)) }
        : { ...current, legs: (current.legs ?? []).map((leg) => (leg.id === targetId ? { ...leg, locked: true } : leg)) },
    );
  };
  const reopenChoice = async (kind: "hotel" | "transport", targetId: string) => {
    setPlan((current) =>
      kind === "hotel"
        ? { ...current, hotels: current.hotels.map((h) => (h.id === targetId ? { ...h, locked: false } : h)) }
        : { ...current, legs: (current.legs ?? []).map((leg) => (leg.id === targetId ? { ...leg, locked: false } : leg)) },
    );
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
        onLockChoice={lockChoice}
        onReopenChoice={reopenChoice}
        changedLegIds={changedLegs}
        changedStayIds={changedStays}
        onAnswerQuestion={() => undefined}
      />
    </div>
  );
}

/** Three cities: every stay and leg is a slot with all its options shown, the suggested one marked, each with Lock in. */
export const ThreeCities: Story = { render: () => <Workspace initial={threeCityPlan} /> };

/** The same trip with everything locked in: each slot shows only its chosen option and a Change button. */
export const EveryChoiceLocked: Story = { render: () => <Workspace initial={lockedThreeCityPlan} /> };

/** A city with two stays: Kyoto's first two nights at one hotel, the last at another. */
export const SplitStay: Story = { render: () => <Workspace initial={splitStayPlan} /> };

/** A city flown into and out of: the airport transfer into it is still to choose, the one out is locked in. */
export const AirportTransfers: Story = { render: () => <Workspace initial={airportTransferPlan} /> };

/** A one-city trip has the same slots: its stay, and the flight as its outbound leg. */
export const SingleCity: Story = { render: () => <Workspace initial={samplePlan} /> };

function LegTabs({ options: initial, locked = false }: { options: TicketOption[]; locked?: boolean }) {
  const [options, setOptions] = useState(initial);
  const [isLocked, setLocked] = useState(locked);
  const chosen = options.find((option) => option.selected) ?? options[0] ?? null;
  return (
    <div style={{ padding: 24, maxWidth: 640 }}>
      <ChoiceSlot
        title="Tokyo → Kyoto · 13 Apr"
        icon="route"
        state={slotState(isLocked, options.length)}
        chosen={chosen}
        options={options}
        tabs={transportKinds(options).map((group) => ({ label: group.kind, options: group.options }))}
        emptyText="No transport options were found for this leg."
        renderOption={(option) => <TicketCard ticket={option} selected={false} choosable={false} />}
        onLock={(optionId) => {
          setOptions(options.map((option) => ({ ...option, selected: option.id === optionId })));
          setLocked(true);
        }}
        onReopen={() => setLocked(false)}
      />
    </div>
  );
}

/** One leg with a flight, an estimated train and an estimated drive, one tab for each kind of transport. */
export const LegWithFlightTrainAndBusTabs: Story = {
  render: () => {
    const bus: TicketOption = {
      ...threeCityPlan.tickets.filter((ticket) => ticket.leg_id === "leg_1")[0],
      id: "leg1_bus",
      mode: "bus",
      name: "Bus or shuttle (estimate)",
      selected: false,
      price: null,
      estimated: true,
    };
    return <LegTabs options={[...threeCityPlan.tickets.filter((ticket) => ticket.leg_id === "leg_1"), bus]} />;
  },
};

/** A leg no source found any option for says so, with a directions link when both places are known. */
export const LegWithNoOptions: Story = { render: () => <LegTabs options={[]} /> };
