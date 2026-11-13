import assert from "node:assert/strict";
import test from "node:test";

import { changedIdsOf, NO_IDS, STAY_KINDS } from "./planChanges.ts";

const diff = [
  { kind: "changed", item_id: "item_1" },
  { kind: "hotel", id: "hotel_b", segment_id: "seg_b", from: "A", to: "B" },
  { kind: "stay", id: "hotel_c", name: "C" },
  { kind: "transport", leg_id: "leg_ab", from: "train", to: "bus" },
  { kind: "hotel", from: "A", to: "B" },
];

test("the stays a diff names are the ids of its hotel, stay and price entries", () => {
  assert.deepEqual([...changedIdsOf(diff, STAY_KINDS, "id")], ["hotel_b", "hotel_c"]);
});

test("the legs a diff names are the leg ids of its transport entries", () => {
  assert.deepEqual([...changedIdsOf(diff, ["transport"], "leg_id")], ["leg_ab"]);
});

test("a diff that names none gives the shared empty set", () => {
  assert.equal(changedIdsOf([], STAY_KINDS, "id"), NO_IDS);
  assert.equal(changedIdsOf([{ kind: "created" }], ["transport"], "leg_id"), NO_IDS);
});
