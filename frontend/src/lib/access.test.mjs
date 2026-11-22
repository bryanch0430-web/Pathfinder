import assert from "node:assert/strict";
import test from "node:test";

import { localAdminFrom, offersSignIn, showsAdmin, withoutSignIn } from "./access.ts";

test("only a literal true in /api/ready turns local admin mode on", () => {
  assert.equal(localAdminFrom({ status: "ok", local_admin: true }), true);
  for (const body of [{ local_admin: false }, { local_admin: "true" }, { local_admin: 1 }, {}, null, undefined, "ok"]) {
    assert.equal(localAdminFrom(body), false, JSON.stringify(body));
  }
});

test("the admin areas show with a stored token, or in local admin mode without one", () => {
  assert.equal(showsAdmin(false, ""), false);
  assert.equal(showsAdmin(false, null), false);
  assert.equal(showsAdmin(false, "operator-token"), true);
  assert.equal(showsAdmin(true, ""), true);
  assert.equal(showsAdmin(true, "operator-token"), true);
});

test("sign-in is offered only outside local admin mode", () => {
  assert.equal(offersSignIn(false), true);
  assert.equal(offersSignIn(true), false);
});

test("in local admin mode no message asks the traveller to sign in", () => {
  assert.equal(withoutSignIn("errors.signInToOpen", true), "errors.notOpenHere");
  assert.equal(withoutSignIn("errors.signInEnded", true), "errors.signInEndedGuest");
  assert.equal(withoutSignIn("errors.network", true), "errors.network");
  // Outside the mode every message stays as it is.
  assert.equal(withoutSignIn("errors.signInToOpen", false), "errors.signInToOpen");
  assert.equal(withoutSignIn("errors.signInEnded", false), "errors.signInEnded");
});
