import type { PlanDiffEntry } from "../design-system/types";

/** No ids: one shared set, so a state that resets to it does not re-render. */
export const NO_IDS: ReadonlySet<string> = new Set();

/** The diff entries that are about a stay. */
export const STAY_KINDS: readonly string[] = ["hotel", "stay", "hotel_price"];

/** The ids a plan diff names in `key` for entries of the given kinds (the stays or the legs that changed). */
export function changedIdsOf(
  diff: readonly PlanDiffEntry[],
  kinds: readonly string[],
  key: "id" | "leg_id",
): ReadonlySet<string> {
  const ids = diff
    .filter((entry) => kinds.includes(entry.kind))
    .map((entry) => entry[key])
    .filter((id): id is string => Boolean(id));
  return ids.length ? new Set(ids) : NO_IDS;
}
