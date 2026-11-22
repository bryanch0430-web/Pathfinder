import type { TravellerProfile, TripContext } from "../design-system/types";

export const DEFAULT_PROFILE: TravellerProfile = {
  pace: "balanced",
  with_children: false,
  purpose: "leisure",
  step_free: false,
};

const PACES = new Set<TravellerProfile["pace"]>(["relaxed", "balanced", "packed"]);

/** The traveller profile of a trip form: its own, else one made from the old `pace` word. */
export function profileOf(context: Pick<TripContext, "pace"> & { profile?: TravellerProfile }): TravellerProfile {
  if (context.profile) return { ...DEFAULT_PROFILE, ...context.profile };
  const pace = PACES.has(context.pace as TravellerProfile["pace"])
    ? (context.pace as TravellerProfile["pace"])
    : "balanced";
  return { ...DEFAULT_PROFILE, pace };
}
