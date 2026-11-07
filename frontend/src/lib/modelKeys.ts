/** Pure helpers for the "Model & API keys" card. Kept free of React so `node --test` can load them. */
import type { LlmProvider, LlmStatus } from "./api";
import type { MessageKey, Translate } from "./i18n";

const PROBLEM_KEYS: Record<string, MessageKey> = {
  unreadable: "keys.status.problem.unreadable",
  unknown_provider: "keys.status.problem.unknown_provider",
};

/** The status line in plain words: which model answers, and whose key pays for it. */
export function statusLine(status: LlmStatus, t: Translate, providers: Pick<LlmProvider, "id" | "label">[] = []): string {
  if (status.problem) {
    // The chosen key cannot be used: never say it is in use.
    const provider = providerLabel(status.provider, providers);
    return t(PROBLEM_KEYS[status.problem] ?? "keys.status.problem.other", { provider });
  }
  switch (status.source) {
    case "byok":
      return t("keys.status.byok", { provider: providerLabel(status.provider, providers), model: status.model });
    case "server":
      return t("keys.status.server");
    case "env":
      return t("keys.status.env");
    case "local":
      return t("keys.status.local", { model: status.model });
    default:
      return t("keys.status.none");
  }
}

/** The provider's display name, or its id when the catalogue does not list it. */
export function providerLabel(id: string, providers: Pick<LlmProvider, "id" | "label">[]): string {
  return providers.find((provider) => provider.id === id)?.label ?? id;
}

/** True when a typed key does not start with the provider's usual prefix (a key pasted for another vendor). */
export function keyPrefixMismatch(apiKey: string, prefix: string): boolean {
  const typed = apiKey.trim();
  return Boolean(prefix && typed && !typed.startsWith(prefix));
}

/**
 * Words for a failure kind. Covers the key test's kinds and every `llm_key` reason the server sends: its
 * failure kinds (auth, rate_limit, timeout, network, invalid_request, no_data, server_error, parse, blocked)
 * and "unreadable" for a saved key that no longer decrypts.
 */
const REASON_KEYS: Record<string, MessageKey> = {
  auth: "keys.reason.auth",
  model: "keys.reason.model",
  rate_limit: "keys.reason.rate_limit",
  network: "keys.reason.network",
  timeout: "keys.reason.network",
  credit: "keys.reason.credit",
  server_error: "keys.reason.server_error",
  invalid_request: "keys.reason.invalid_request",
  no_data: "keys.reason.no_data",
  unreadable: "keys.reason.unreadable",
  other: "keys.reason.other",
};

/** The message key that words a failure kind such as "auth"; an unknown kind reads as "other". */
export function reasonKey(kind: string): MessageKey {
  return REASON_KEYS[kind] ?? "keys.reason.other";
}
