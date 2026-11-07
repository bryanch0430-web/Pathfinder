/** Pure helpers for the "Model & API keys" card. Kept free of React so `node --test` can load them. */
import type { LlmProvider, LlmStatus } from "./api";
import type { MessageKey, Translate } from "./i18n";

/** The status line in plain words: which model answers, and whose key pays for it. */
export function statusLine(status: LlmStatus, t: Translate, providers: Pick<LlmProvider, "id" | "label">[] = []): string {
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

const REASON_KEYS: Record<string, MessageKey> = {
  auth: "keys.reason.auth",
  model: "keys.reason.model",
  rate_limit: "keys.reason.rate_limit",
  network: "keys.reason.network",
  credit: "keys.reason.credit",
  other: "keys.reason.other",
};

/** The message key that words a failure kind such as "auth"; an unknown kind reads as "other". */
export function reasonKey(kind: string): MessageKey {
  return REASON_KEYS[kind] ?? "keys.reason.other";
}
