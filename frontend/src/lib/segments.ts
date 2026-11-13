/**
 * The cities, stays and legs of a plan, looked up by name or by date. Pure functions that mirror the backend's rules
 * (`TripPlan.segment_of`, `stay_on`, `stays_in`, `options_for`, `chosen_tickets`), so a plan stored before cities
 * existed renders as a trip of one city.
 */
import type { DayPlan, HotelStay, TicketOption, TransportLeg, TripPlan, TripSegment } from "../design-system/types";

type PlanLike = Pick<TripPlan, "destination" | "dates"> &
  Partial<Pick<TripPlan, "segments" | "legs" | "tickets" | "hotels" | "days" | "origin">>;

/** The plan's cities; one synthetic city (the destination over the trip's dates) when the plan has none. */
export function segmentsOf(plan: PlanLike): TripSegment[] {
  if (plan.segments && plan.segments.length > 0) return plan.segments;
  return [{ id: "seg_1", destination: plan.destination, dates: plan.dates }];
}

/** The city a date belongs to: the last one that starts on or before it, else the first. A one-city trip has one. */
export function segmentOfDay(plan: PlanLike, date: string): TripSegment {
  const segments = segmentsOf(plan);
  if (segments.length === 1) return segments[0];
  let chosen = segments[0];
  for (const segment of segments) if (segment.dates.start <= date) chosen = segment;
  return chosen;
}

export interface SegmentDays {
  segment: TripSegment;
  days: DayPlan[];
}

/** The days of each city in travel order. A day without a `segment_id` goes to the city its date belongs to. */
export function daysBySegment(plan: PlanLike): SegmentDays[] {
  const segments = segmentsOf(plan);
  const groups: SegmentDays[] = segments.map((segment) => ({ segment, days: [] }));
  for (const day of plan.days ?? []) {
    const own = day.segment_id ? groups.find((group) => group.segment.id === day.segment_id) : undefined;
    const group = own ?? groups.find((candidate) => candidate.segment.id === segmentOfDay(plan, day.date).id);
    (group ?? groups[0]).days.push(day);
  }
  return groups;
}

/** The stay the traveller sleeps in on a night, or wakes up in on the last morning; null when there is none. */
export function stayOn(plan: PlanLike, date: string): HotelStay | null {
  const stays = plan.hotels ?? [];
  const night = stays.find((stay) => stay.check_in <= date && date < stay.check_out);
  if (night) return night;
  const morning = stays.find((stay) => stay.check_out === date);
  if (morning) return morning;
  return stays.length === 1 && segmentsOf(plan).length === 1 ? stays[0] : null;
}

/** The chosen stays of a city in date order. A stay without a `segment_id` belongs to the city of its check-in. */
export function staysIn(plan: PlanLike, segmentId: string): HotelStay[] {
  return (plan.hotels ?? [])
    .filter((stay) => (stay.segment_id ?? segmentOfDay(plan, stay.check_in).id) === segmentId)
    .sort((a, b) => (a.check_in < b.check_in ? -1 : a.check_in > b.check_in ? 1 : 0));
}

/** The hops of the trip; the one outbound leg a plan from before legs implies when it has tickets. */
export function legsOf(plan: PlanLike): TransportLeg[] {
  if (plan.legs && plan.legs.length > 0) return plan.legs;
  if ((plan.tickets ?? []).length === 0) return [];
  return [
    {
      id: "leg_out",
      from_place: plan.origin ?? "",
      to_place: plan.destination.name,
      depart_date: plan.dates.start,
      kind: "outbound",
    },
  ];
}

/** The options of a leg in the order they are kept; an option that names no known leg is on the first leg. */
export function optionsFor(plan: PlanLike, legId: string): TicketOption[] {
  const legs = legsOf(plan);
  const known = new Set(legs.map((leg) => leg.id));
  return (plan.tickets ?? []).filter((ticket) => {
    const own = ticket.leg_id && known.has(ticket.leg_id) ? ticket.leg_id : legs[0]?.id;
    return own === legId;
  });
}

/** The option chosen for a leg: the one marked selected, else the first; undefined when the leg has none. */
export function chosenFor(plan: PlanLike, legId: string): TicketOption | undefined {
  const options = optionsFor(plan, legId);
  return options.find((option) => option.selected) ?? options[0];
}

/** The leg that brings the traveller into a city: the way out for the first one, else the hop that arrives there. */
export function legInto(plan: PlanLike, segmentId: string): TransportLeg | undefined {
  const segments = segmentsOf(plan);
  const index = segments.findIndex((segment) => segment.id === segmentId);
  if (index < 0) return undefined;
  const legs = legsOf(plan);
  if (index === 0) return legs.find((leg) => leg.kind === "outbound");
  const name = segments[index].destination.name.toLowerCase();
  const arrivals = legs.filter((leg) => leg.kind === "between");
  return (
    arrivals.find((leg) => leg.to_place.toLowerCase() === name && leg.depart_date === segments[index].dates.start) ??
    arrivals[index - 1]
  );
}

/** The leg that takes the traveller home from the last city, when the trip returns. */
export function legHome(plan: PlanLike): TransportLeg | undefined {
  return legsOf(plan).find((leg) => leg.kind === "return");
}

/** "Tokyo → Kyoto"; the single name for one city. */
export function routeLabel(plan: PlanLike): string {
  return segmentsOf(plan)
    .map((segment) => segment.destination.name)
    .join(" → ");
}

/** Whole nights between two dates ("YYYY-MM-DD"), never below zero. */
export function nightsBetween(start: string, end: string): number {
  const days = Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000);
  return Number.isFinite(days) ? Math.max(days, 0) : 0;
}
