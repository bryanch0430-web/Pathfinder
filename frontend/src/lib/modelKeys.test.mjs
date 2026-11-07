import assert from "node:assert/strict";
import test from "node:test";

import { statusLine } from "./modelKeys.ts";

// A stand-in translator: the key and its parameters, so the test sees which message and which values were chosen.
const t = (key, params = {}) => `${key}${Object.keys(params).length ? JSON.stringify(params) : ""}`;
const local = { configured: false, reachable: false, model: "" };
const providers = [{ id: "anthropic", label: "Anthropic" }];

test("a key of the traveller's own names its provider and model", () => {
  const status = { source: "byok", provider: "anthropic", model: "claude-sonnet-5-5", local };
  assert.equal(statusLine(status, t, providers), 'keys.status.byok{"provider":"Anthropic","model":"claude-sonnet-5-5"}');
});

test("an unknown provider id falls back to the id itself", () => {
  const status = { source: "byok", provider: "mystery", model: "m1", local };
  assert.equal(statusLine(status, t, providers), 'keys.status.byok{"provider":"mystery","model":"m1"}');
});

test("the server's key, the environment's key, the local model and no model each have their own words", () => {
  assert.equal(statusLine({ source: "server", provider: "xai", model: "g", local }, t, providers), "keys.status.server");
  assert.equal(statusLine({ source: "env", provider: "xai", model: "g", local }, t, providers), "keys.status.env");
  assert.equal(
    statusLine({ source: "local", provider: "", model: "llama3.1", local }, t, providers),
    'keys.status.local{"model":"llama3.1"}',
  );
  assert.equal(statusLine({ source: "none", provider: "", model: "", local }, t, providers), "keys.status.none");
});
