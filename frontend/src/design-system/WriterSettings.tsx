import { useEffect, useId, useState } from "react";
import { testLlmKey, type KeyBody, type LlmKey, type LlmProvider, type LlmStatus } from "../lib/api";
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

export type WriterCall = {
  temperature: number;
  max_tokens: number;
  reasoning: string;
};

/**
 * The writer, in the same shape as the reviewer: one key field, or the environment key.
 */
export function WriterSettings({
  providerId: initialProvider,
  model: initialModel,
  baseUrl: initialBaseUrl,
  temperature,
  maxTokens,
  reasoning: initialReasoning,
  activeKey,
  usage,
  providers,
  canEdit,
  onSaveCall,
  onSaveKey,
  onUseEnvironment,
}: {
  providerId: string;
  model: string;
  baseUrl: string;
  temperature: number;
  maxTokens: number;
  reasoning: string;
  activeKey: LlmKey | null;
  usage: LlmStatus | null;
  providers: LlmProvider[];
  canEdit: boolean;
  onSaveCall: (body: WriterCall) => Promise<void>;
  onSaveKey: (body: KeyBody) => Promise<boolean>;
  onUseEnvironment: () => Promise<void>;
}) {
  const { t } = useI18n();
  const formId = useId();
  const [providerId, setProviderId] = useState(initialProvider || "poe");
  const [model, setModel] = useState(initialModel);
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [apiKey, setApiKey] = useState("");
  const [heat, setHeat] = useState(String(temperature));
  const [capText, setCapText] = useState(String(maxTokens));
  const [reasoning, setReasoning] = useState(initialReasoning || "none");
  const [models, setModels] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [working, setWorking] = useState<"test" | "save" | null>(null);

  useEffect(() => {
    setProviderId(initialProvider || "poe");
    setModel(initialModel);
    setBaseUrl(initialBaseUrl);
    setHeat(String(temperature));
    setCapText(String(maxTokens));
    setReasoning(initialReasoning || "none");
  }, [initialProvider, initialModel, initialBaseUrl, temperature, maxTokens, initialReasoning]);

  const catalogue = providers.length
    ? providers
    : [{ id: providerId, label: providerId, default_model: model, key_prefix: "", supports_search: false, needs_base_url: providerId === "openai-compatible" }];
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

  const parsed = (): WriterCall | null => {
    const nextHeat = Number(heat);
    const cap = Number(capText);
    if (!Number.isFinite(nextHeat) || nextHeat < 0 || nextHeat > 2) return null;
    if (!Number.isInteger(cap) || cap < 16 || cap > 16384) return null;
    return { temperature: nextHeat, max_tokens: cap, reasoning };
  };

  const named = (providerId: string, modelName: string) => ({
    provider: providerLabel(providerId, catalogue),
    model: modelName || "—",
  });

  const status = () => {
    if (activeKey?.key_tail) {
      return t("settings.reviewer.keySaved", { tail: activeKey.key_tail, ...named(activeKey.provider, activeKey.model) });
    }
    // The environment key's provider is the one the dropdown shows (taken from the base URL), not the server's name for it.
    return t("settings.writer.keyEnv", named(initialProvider || usage?.provider || "", usage?.model || ""));
  };

  const savedNote = (providerId: string, modelName: string) => t("settings.savedUses", {
    name: t("settings.group.keys"),
    ...named(providerId, modelName),
  });

  const save = async (clearKey: boolean) => {
    setWorking("save");
    setNote(null);
    try {
      if (clearKey) {
        await onUseEnvironment();
        setApiKey("");
        setNote(t("settings.savedEnv", { name: t("settings.group.keys") }));
        return;
      }
      const numbers = parsed();
      if (!numbers || !model.trim()) {
        setNote(t("settings.orch.hyperInvalid"));
        return;
      }
      if (apiKey.trim()) {
        const kept = await onSaveKey({
          provider: providerId,
          api_key: apiKey.trim(),
          model: model.trim(),
          base_url: needsBaseUrl ? baseUrl.trim() : undefined,
        });
        if (!kept) {
          setNote(t("keys.testFailed", { message: t("keys.reason.other") }));
          return;
        }
        setApiKey("");
        await onSaveCall(numbers);
        setNote(savedNote(providerId, model.trim()));
        return;
      }
      await onSaveCall(numbers);
      const current = activeKey ? named(activeKey.provider, activeKey.model) : named(usage?.provider || "", usage?.model || "");
      setNote(t("settings.savedUses", { name: t("settings.group.keys"), ...current }));
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(null);
    }
  };

  const test = async () => {
    if (!parsed() || !model.trim() || !apiKey.trim()) {
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
      <Text size="sm" weight="semibold">{t("settings.group.keys")}</Text>
      <Text size="xs" tone="tertiary">{t("settings.writer.callBody")}</Text>
      <Text size="xs" tone="tertiary">{status()}</Text>
      <div className="pf-form-grid">
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
        <Field wide label={t("keys.model")} htmlFor={`${formId}-model`}>
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
        <Field label={t("settings.orch.temperature")} htmlFor={`${formId}-heat`} hint={t("settings.writer.temperatureHint")}>
          <Input id={`${formId}-heat`} value={heat} disabled={!canEdit || locked} inputMode="decimal" onChange={(event) => setHeat(event.target.value)} />
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
          <Button size="sm" variant="primary" disabled={locked} onClick={() => void save(false)}>{working === "save" ? t("settings.gateSaving") : t("settings.writer.save")}</Button>
          <Button size="sm" disabled={locked || !model.trim() || !apiKey.trim()} onClick={() => void test()}>{t("keys.test")}</Button>
          <Button size="sm" disabled={locked || !activeKey} onClick={() => void save(true)}>{t("settings.orch.clearKey")}</Button>
        </div>
      ) : null}
      {note ? <Text size="xs" tone="tertiary">{note}</Text> : null}
    </div>
  );
}
