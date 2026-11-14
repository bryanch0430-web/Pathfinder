/**
 * Choice slots: a stay or a leg the traveller picks one option for, then locks in. Pure functions that mirror the
 * backend's `TripPlan.open_choices`, so the page and the server agree on what is still to decide.
 */
import type { TicketOption, TripPlan } from "../design-system/types";
import { legsOf, optionsFor, optionsOfStay, transferLegsOf } from "./segments";

export type SlotState = "locked" | "open" | "single" | "empty";

/** `locked` when the traveller locked it; else by how many options it has: none (`empty`), one (`single`), two or more (`open`). */
export function slotState(locked: boolean, optionCount: number): SlotState {
  if (locked) return "locked";
  if (optionCount <= 0) return "empty";
  return optionCount === 1 ? "single" : "open";
}

export type TransportKind = "flight" | "train" | "bus" | "drive" | "taxi";

const KIND_OF: Record<string, TransportKind> = {
  flight: "flight",
  train: "train",
  bus: "bus",
  shuttle: "bus",
  ground: "bus",
  ticket: "bus",
  ferry: "bus",
  drive: "drive",
  taxi: "taxi",
};
const KIND_ORDER: TransportKind[] = ["flight", "train", "bus", "drive", "taxi"];

/** The options of a leg grouped by kind of transport, in the order flight, train, bus / shuttle, drive, taxi; a kind
 * with no options is left out. */
export function transportKinds(options: TicketOption[]): { kind: TransportKind; options: TicketOption[] }[] {
  const groups = new Map<TransportKind, TicketOption[]>();
  for (const option of options) {
    const kind = KIND_OF[option.mode] ?? "bus";
    groups.set(kind, [...(groups.get(kind) ?? []), option]);
  }
  return KIND_ORDER.filter((kind) => groups.has(kind)).map((kind) => ({ kind, options: groups.get(kind) ?? [] }));
}

/** Every stay and leg (the hops and the airport transfers) with two or more options: the choices there are to make. */
function slots(plan: TripPlan): { locked: boolean }[] {
  const stays = (plan.hotels ?? [])
    .filter((stay) => optionsOfStay(plan, stay).length >= 2)
    .map((stay) => ({ locked: Boolean(stay.locked) }));
  const legs = [...legsOf(plan), ...transferLegsOf(plan)]
    .filter((leg) => optionsFor(plan, leg.id).length >= 2)
    .map((leg) => ({ locked: Boolean(leg.locked) }));
  return [...stays, ...legs];
}

/** How many choices are still open (not locked, two or more options). */
export function openCount(plan: TripPlan): number {
  return slots(plan).filter((slot) => !slot.locked).length;
}

/** How many choices there are at all (two or more options, locked or not). */
export function slotCount(plan: TripPlan): number {
  return slots(plan).length;
}
