import assert from "node:assert/strict";
import test from "node:test";

import { keyPrefixMismatch, reasonKey, statusLine } from "./modelKeys.ts";

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

test("a chosen key that cannot be used shows its problem, never that it is in use", () => {
  const unreadable = { source: "byok", provider: "anthropic", model: "m", problem: "unreadable", local };
  assert.equal(statusLine(unreadable, t, providers), 'keys.status.problem.unreadable{"provider":"Anthropic"}');
  const server = { source: "server", provider: "openai", model: "m", problem: "unreadable", local };
  assert.equal(statusLine(server, t, providers), 'keys.status.problem.unreadable{"provider":"openai"}');
  const unknown = { source: "byok", provider: "gone", model: "m", problem: "unknown_provider", local };
  assert.equal(statusLine(unknown, t, providers), 'keys.status.problem.unknown_provider{"provider":"gone"}');
  const strange = { source: "byok", provider: "anthropic", model: "m", problem: "something_new", local };
  assert.equal(statusLine(strange, t, providers), 'keys.status.problem.other{"provider":"Anthropic"}');
  const fine = { source: "byok", provider: "anthropic", model: "m", problem: "", local };
  assert.equal(statusLine(fine, t, providers), 'keys.status.byok{"provider":"Anthropic","model":"m"}');
});

test("every reason the server sends has its own words", () => {
  assert.equal(reasonKey("auth"), "keys.reason.auth");
  assert.equal(reasonKey("rate_limit"), "keys.reason.rate_limit");
  assert.equal(reasonKey("network"), "keys.reason.network");
  assert.equal(reasonKey("timeout"), "keys.reason.network");
  assert.equal(reasonKey("server_error"), "keys.reason.server_error");
  assert.equal(reasonKey("invalid_request"), "keys.reason.invalid_request");
  assert.equal(reasonKey("no_data"), "keys.reason.no_data");
  assert.equal(reasonKey("unreadable"), "keys.reason.unreadable");
  assert.equal(reasonKey("credit"), "keys.reason.credit");
  assert.equal(reasonKey("model"), "keys.reason.model");
  assert.equal(reasonKey("something_new"), "keys.reason.other");
});

test("a key without the provider's usual prefix is flagged, an empty prefix never is", () => {
  assert.equal(keyPrefixMismatch("sk-ant-abc", "sk-ant-"), false);
  assert.equal(keyPrefixMismatch("  sk-ant-abc ", "sk-ant-"), false);
  assert.equal(keyPrefixMismatch("xai-abc", "sk-ant-"), true);
  assert.equal(keyPrefixMismatch("anything", ""), false);
  assert.equal(keyPrefixMismatch("", "sk-ant-"), false);
});
