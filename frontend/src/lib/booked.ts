import type { HotelStay, TicketOption, TransportLeg, TripPlan } from "../design-system/types";
import { chosenFor } from "./segments";

type BookedPlan = Pick<TripPlan, "hotels" | "tickets" | "destination"> & Partial<Pick<TripPlan, "legs" | "origin" | "segments" | "dates">>;

/** The stays the traveller marked booked; only a locked stay can be. */
export function bookedStays(plan: BookedPlan): HotelStay[] {
  return (plan.hotels ?? []).filter((stay) => stay.locked && stay.booked);
}

/** The legs (airport transfers included) the traveller marked booked; only a locked leg can be. */
export function bookedLegs(plan: BookedPlan): TransportLeg[] {
  return (plan.legs ?? []).filter((leg) => leg.locked && leg.booked);
}

/** True for the chosen option of a leg the traveller marked booked. */
export function isBookedTicket(plan: BookedPlan, ticket: TicketOption): boolean {
  return bookedLegs(plan).some((leg) => chosenFor(plan as TripPlan, leg.id)?.id === ticket.id);
}

/** One line per booked stay and leg, for the exports: what it is, its dates and, for a leg, the option booked. */
export function bookedLines(plan: BookedPlan): string[] {
  const stays = bookedStays(plan).map((stay) => `${stay.name} · stay ${stay.check_in} → ${stay.check_out}`);
  const legs = bookedLegs(plan).map((leg) => {
    const chosen = chosenFor(plan as TripPlan, leg.id);
    return [`${leg.from_place} → ${leg.to_place}`, leg.depart_date, chosen?.name].filter(Boolean).join(" · ");
  });
  return [...stays, ...legs];
}
