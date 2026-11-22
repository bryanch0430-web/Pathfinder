import { useEffect, useId, useState } from "react";
import { testLlmKey, type LlmProvider } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { keyPrefixMismatch, providerLabel } from "../lib/modelKeys";
import { Button, Input, Select, Text } from "./atoms";
import { Field } from "./molecules";

const REASONING = ["none", "low", "medium", "high"] as const;
const REASONING_LABEL = {
  none: "settings.orch.reasoning.none",
  low: "settings.orch.reasoning.low",
  medium: "settings.orch.reasoning.medium",
  high: "settings.orch.reasoning.high",
} as const;

export type ReviewerConfig = {
  provider: string;
  model: string;
  base_url: string;
  temperature: number;
  max_tokens: number;
  reasoning: string;
  rounds: number;
  key_set: boolean;
  key_saved: boolean;
  key_tail: string;
  key_source: "arm" | "poe" | "deepseek" | "none";
};

export type ReviewerBody = {
  provider: string;
  model: string;
  base_url: string;
  api_key?: string;
  clear_key?: boolean;
  temperature: number;
  max_tokens: number;
  reasoning: string;
  rounds: number;
};

/**
 * The reviewer: how many rule checks run, and an optional model of its own.
 * A blank model keeps the review on the rules.
 */
export function ReviewerSettings({
  reviewer,
  providers,
  canEdit,
  onSave,
}: {
  reviewer: ReviewerConfig;
  providers: LlmProvider[];
  canEdit: boolean;
  onSave?: (body: ReviewerBody) => Promise<void>;
}) {
  const { t } = useI18n();
  const formId = useId();
  const [providerId, setProviderId] = useState(reviewer.provider || "poe");
  const [model, setModel] = useState(reviewer.model);
  const [baseUrl, setBaseUrl] = useState(reviewer.base_url);
  const [apiKey, setApiKey] = useState("");
  const [rounds, setRounds] = useState(String(reviewer.rounds));
  const [temperature, setTemperature] = useState(String(reviewer.temperature));
  const [maxTokens, setMaxTokens] = useState(String(reviewer.max_tokens));
  const [reasoning, setReasoning] = useState(reviewer.reasoning || "none");
  const [models, setModels] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [working, setWorking] = useState<"test" | "save" | null>(null);

  useEffect(() => {
    setProviderId(reviewer.provider || "poe");
    setModel(reviewer.model);
    setBaseUrl(reviewer.base_url);
    setRounds(String(reviewer.rounds));
    setTemperature(String(reviewer.temperature));
    setMaxTokens(String(reviewer.max_tokens));
    setReasoning(reviewer.reasoning || "none");
  }, [reviewer]);

  const catalogue = providers.length
    ? providers
    : [{ id: providerId, label: providerId, default_model: "", key_prefix: "", supports_search: false, needs_base_url: providerId === "openai-compatible" }];
  const provider = catalogue.find((entry) => entry.id === providerId) ?? catalogue[0];
  const needsBaseUrl = Boolean(provider?.needs_base_url);
  const prefixWarning = provider && keyPrefixMismatch(apiKey, provider.key_prefix)
    ? t("keys.prefixMismatch", { provider: provider.label, prefix: provider.key_prefix })
    : null;

  const changeProvider = (id: string) => {
    setProviderId(id);
    setApiKey("");
    setBaseUrl("");
    setModels([]);
    setNote(null);
  };

  const parsed = () => {
    const heat = Number(temperature);
    const cap = Number(maxTokens);
    const times = Number(rounds);
    if (!Number.isFinite(heat) || heat < 0 || heat > 2) return null;
    if (!Number.isInteger(cap) || cap < 16 || cap > 16384) return null;
    if (!Number.isInteger(times) || times < 0 || times > 10) return null;
    return { temperature: heat, max_tokens: cap, rounds: times };
  };

  const body = (clearKey: boolean): ReviewerBody | null => {
    const numbers = parsed();
    if (!numbers) return null;
    return {
      provider: providerId,
      model: model.trim(),
      base_url: needsBaseUrl ? baseUrl.trim() : "",
      api_key: clearKey ? "" : apiKey.trim(),
      clear_key: clearKey,
      temperature: numbers.temperature,
      max_tokens: numbers.max_tokens,
      reasoning,
      rounds: numbers.rounds,
    };
  };

  const used = () => ({
    provider: providerLabel(reviewer.provider || providerId, catalogue),
    model: reviewer.model || model || "—",
  });

  const status = () => {
    const who = used();
    if (reviewer.key_source === "arm" && reviewer.key_tail) return t("settings.reviewer.keySaved", { tail: reviewer.key_tail, ...who });
    if (reviewer.key_source === "poe" && reviewer.key_tail) return t("settings.reviewer.keyPoe", { tail: reviewer.key_tail, ...who });
    if (reviewer.key_source === "deepseek" && reviewer.key_tail) return t("settings.reviewer.keyDeepseek", { tail: reviewer.key_tail, ...who });
    if (reviewer.key_set) return `${t("settings.orch.keyEnv")} ${who.provider} · ${who.model}.`;
    return t("settings.orch.keyNone");
  };

  const save = async (clearKey: boolean) => {
    const next = body(clearKey);
    if (!next || !onSave) {
      setNote(t("settings.orch.hyperInvalid"));
      return;
    }
    setWorking("save");
    setNote(null);
    try {
      await onSave(next);
      setApiKey("");
      setNote(t("settings.savedUses", {
        name: t("settings.group.reviewer"),
        provider: providerLabel(providerId, catalogue),
        model: model.trim() || "—",
      }));
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(null);
    }
  };

  const test = async () => {
    if (!parsed() || !model.trim()) {
      setNote(t("settings.orch.hyperInvalid"));
      return;
    }
    setWorking("test");
    setNote(null);
    try {
      const result = await testLlmKey({
        provider: providerId,
        api_key: apiKey.trim(),
        model: model.trim(),
        base_url: needsBaseUrl ? baseUrl.trim() : undefined,
      });
      if (result.models.length) setModels(result.models);
      setNote(result.ok ? t("keys.testOk", { ms: Math.round(result.latency_ms) }) : t("keys.testFailed", { message: result.message }));
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(null);
    }
  };

  const locked = working !== null;

  return (
    <div className="pf-card pf-eval__panel">
      <Text size="sm" weight="semibold">{t("settings.group.reviewer")}</Text>
      <Text size="xs" tone="tertiary">{t("settings.reviewer.body")}</Text>
      <Text size="xs" tone="tertiary">{status()}</Text>
      <div className="pf-form-grid">
        <Field wide htmlFor={`${formId}-rounds`} label={t("settings.reviewer.rounds")} hint={t("settings.reviewer.roundsHint")}>
          <Input id={`${formId}-rounds`} value={rounds} disabled={!canEdit || locked} inputMode="numeric" onChange={(event) => setRounds(event.target.value)} />
        </Field>
        <Field label={t("keys.provider")} htmlFor={`${formId}-provider`}>
          <Select id={`${formId}-provider`} value={providerId} disabled={!canEdit || locked} onChange={(event) => changeProvider(event.target.value)}>
            {catalogue.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.label}</option>
            ))}
          </Select>
        </Field>
        {needsBaseUrl ? (
          <Field wide label={t("keys.baseUrl")} htmlFor={`${formId}-base`} hint={t("keys.baseUrlHint")}>
            <Input id={`${formId}-base`} value={baseUrl} disabled={!canEdit || locked} autoComplete="off" spellCheck={false} maxLength={300} onChange={(event) => setBaseUrl(event.target.value)} />
          </Field>
        ) : null}
        <Field wide label={t("keys.model")} htmlFor={`${formId}-model`} hint={t("settings.reviewer.modelHint")}>
          <Input id={`${formId}-model`} value={model} disabled={!canEdit || locked} list={`${formId}-models`} autoComplete="off" spellCheck={false} maxLength={120} onChange={(event) => setModel(event.target.value)} />
          <datalist id={`${formId}-models`}>
            {models.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </Field>
        {canEdit ? (
          <Field wide label={t("keys.apiKey")} htmlFor={`${formId}-key`} hint={prefixWarning || t("settings.orch.armKeyHint")}>
            <Input id={`${formId}-key`} name={`${formId}-key`} type="password" value={apiKey} disabled={locked} autoComplete="new-password" spellCheck={false} onChange={(event) => setApiKey(event.target.value)} />
          </Field>
        ) : null}
        <Field label={t("settings.orch.temperature")} htmlFor={`${formId}-heat`} hint={t("settings.reviewer.temperatureHint")}>
          <Input id={`${formId}-heat`} value={temperature} disabled={!canEdit || locked} inputMode="decimal" onChange={(event) => setTemperature(event.target.value)} />
        </Field>
        <Field label={t("settings.orch.maxTokens")} htmlFor={`${formId}-cap`} hint={t("settings.reviewer.tokensHint")}>
          <Input id={`${formId}-cap`} value={maxTokens} disabled={!canEdit || locked} inputMode="numeric" onChange={(event) => setMaxTokens(event.target.value)} />
        </Field>
        <Field label={t("settings.orch.reasoning")} htmlFor={`${formId}-reason`} hint={t("settings.orch.reasoningHint")}>
          <Select id={`${formId}-reason`} value={reasoning} disabled={!canEdit || locked} onChange={(event) => setReasoning(event.target.value)}>
            {REASONING.map((effort) => (
              <option key={effort} value={effort}>{t(REASONING_LABEL[effort])}</option>
            ))}
          </Select>
        </Field>
      </div>
      {canEdit ? (
        <div className="pf-row" style={{ gap: 6 }}>
          <Button size="sm" variant="primary" disabled={locked} onClick={() => void save(false)}>{working === "save" ? t("settings.gateSaving") : t("settings.reviewer.save")}</Button>
          <Button size="sm" disabled={locked || !model.trim() || !apiKey.trim()} onClick={() => void test()}>{t("keys.test")}</Button>
          <Button size="sm" disabled={locked || !reviewer.key_saved} onClick={() => void save(true)}>{t("settings.orch.clearKey")}</Button>
        </div>
      ) : null}
      {note ? <Text size="xs" tone="tertiary">{note}</Text> : null}
    </div>
  );
}
