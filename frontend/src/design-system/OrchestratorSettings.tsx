import { useEffect, useId, useState } from "react";
import { testLlmKey, type LlmProvider } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { keyPrefixMismatch, providerLabel } from "../lib/modelKeys";
import { Button, Divider, Input, Select, Text } from "./atoms";
import { Field } from "./molecules";

type Side = { model: string; url: string; key_set: boolean; key_saved: boolean; key_tail: string };

const PLAIN_MODEL = "gemini-3.5-flash-lite";
const REASON_MODEL = "gemini-3.8-flash";
const REASONING = ["none", "low", "medium", "high"] as const;
const REASONING_LABEL = {
  none: "settings.orch.reasoning.none",
  low: "settings.orch.reasoning.low",
  medium: "settings.orch.reasoning.medium",
  high: "settings.orch.reasoning.high",
} as const;

export type GateChoice = "jev" | "plain" | "reason";
export type ReasoningEffort = (typeof REASONING)[number];

export type GateArm = {
  provider: string;
  model: string;
  base_url: string;
  temperature: number;
  max_tokens: number;
  reasoning: string;
  key_set: boolean;
  key_saved: boolean;
  key_tail: string;
  key_source: "arm" | "poe" | "none";
};

export type GateArmBody = {
  provider: string;
  model: string;
  base_url: string;
  api_key?: string;
  clear_key?: boolean;
  temperature: number;
  max_tokens: number;
  reasoning: string;
};

export type OrchestratorConfig = {
  gate?: GateChoice;
  poe?: { key_set: boolean; key_saved: boolean; key_tail: string };
  models?: { plain?: string; reason?: string };
  arms?: { plain?: GateArm; reason?: GateArm };
  orchestrator?: { jev: Side; fast: Side };
};

type SideBody = { model: string; url: string; api_key?: string; clear_key?: boolean };

function fallbackArm(kind: "plain" | "reason", config: OrchestratorConfig): GateArm {
  const saved = config.arms?.[kind];
  if (saved) return saved;
  const poe = config.poe;
  return {
    provider: "poe",
    model: config.models?.[kind] || (kind === "plain" ? PLAIN_MODEL : REASON_MODEL),
    base_url: "",
    temperature: kind === "plain" ? 0 : 0.2,
    max_tokens: 1024,
    reasoning: kind === "plain" ? "none" : "low",
    key_set: Boolean(poe?.key_set),
    key_saved: false,
    key_tail: poe?.key_tail ?? "",
    key_source: poe?.key_set ? "poe" : "none",
  };
}

/**
 * Jev, or a fast model and a fast reasoning model.
 * Each cloud arm has a provider, a model, a key, and the call settings the request can send.
 */
export function OrchestratorSettings({
  config,
  providers,
  canEdit,
  onSaveGate,
  onSaveSide,
}: {
  config: OrchestratorConfig;
  providers: LlmProvider[];
  canEdit: boolean;
  onSaveGate?: (gate: GateChoice) => Promise<void>;
  onSaveSide?: (body: { jev?: SideBody; plain?: GateArmBody; reason?: GateArmBody }) => Promise<void>;
}) {
  const { t } = useI18n();
  const jev = config.orchestrator?.jev;
  const [choice, setChoice] = useState<GateChoice>(config.gate === "plain" || config.gate === "reason" ? config.gate : "jev");
  const [jevModel, setJevModel] = useState(jev?.model ?? "");
  const [jevUrl, setJevUrl] = useState(jev?.url ?? "");
  const [jevKey, setJevKey] = useState("");
  const [busy, setBusy] = useState<"choice" | "jev" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setChoice(config.gate === "plain" || config.gate === "reason" ? config.gate : "jev");
    setJevModel(jev?.model ?? "");
    setJevUrl(jev?.url ?? "");
  }, [config.gate, jev?.model, jev?.url]);

  const run = async (kind: "choice" | "jev", work: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  };

  const choose = (next: GateChoice) => {
    if (!canEdit || next === choice || !onSaveGate) return;
    const previous = choice;
    setChoice(next);
    void run("choice", async () => {
      try {
        await onSaveGate(next);
      } catch (cause) {
        setChoice(previous);
        throw cause;
      }
    });
  };

  const status = (side: Side | undefined) => {
    if (!side) return "";
    if (side.key_saved && side.key_tail) return t("settings.orch.keySaved", { tail: side.key_tail });
    if (side.key_set) return t("settings.orch.keyEnv");
    return t("settings.orch.keyNone");
  };

  const locked = busy !== null;

  return (
    <div className="pf-card pf-eval__panel">
      <Text size="sm" weight="semibold">{t("settings.orch")}</Text>
      <Text size="xs" tone="tertiary">{t("settings.orchBody")}</Text>
      <div className="pf-stack pf-stack--sm" role="radiogroup" aria-label={t("settings.orch")}>
        <label className="pf-stack" style={{ gap: 2 }}>
          <span className="pf-row" style={{ gap: 8 }}>
            <input type="radio" name="pf-orchestrator" checked={choice === "jev"} disabled={!canEdit || locked} onChange={() => choose("jev")} />
            <span className="pf-small">{t("settings.gate.jev")}</span>
          </span>
          <Text size="xs" tone="tertiary">{t("settings.gate.jevBody")}</Text>
        </label>
        <label className="pf-stack" style={{ gap: 2 }}>
          <span className="pf-row" style={{ gap: 8 }}>
            <input type="radio" name="pf-orchestrator" checked={choice === "plain"} disabled={!canEdit || locked} onChange={() => choose("plain")} />
            <span className="pf-small">{t("settings.gate.plain")}</span>
          </span>
          <Text size="xs" tone="tertiary">{t("settings.gate.plainBody")}</Text>
        </label>
        <label className="pf-stack" style={{ gap: 2 }}>
          <span className="pf-row" style={{ gap: 8 }}>
            <input type="radio" name="pf-orchestrator" checked={choice === "reason"} disabled={!canEdit || locked} onChange={() => choose("reason")} />
            <span className="pf-small">{t("settings.gate.reason")}</span>
          </span>
          <Text size="xs" tone="tertiary">{t("settings.gate.reasonBody")}</Text>
        </label>
      </div>
      <Divider />
      <Text size="sm" weight="semibold">{t("settings.gate.jev")}</Text>
      <Text size="xs" tone="tertiary">{status(jev)}</Text>
      <div className="pf-form-grid">
        <Field wide htmlFor="pf-jev-model" label={t("keys.model")}>
          <Input id="pf-jev-model" value={jevModel} disabled={!canEdit || locked} onChange={(event) => setJevModel(event.target.value)} />
        </Field>
        <Field wide htmlFor="pf-jev-url" label={t("settings.orch.address")} hint={t("settings.orch.jevUrlHint")}>
          <Input id="pf-jev-url" value={jevUrl} disabled={!canEdit || locked} onChange={(event) => setJevUrl(event.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        {canEdit ? (
          <Field wide htmlFor="pf-jev-key" label={t("keys.apiKey")} hint={t("settings.orch.keyHint")}>
            <Input id="pf-jev-key" name="pf-jev-key" type="password" value={jevKey} disabled={locked} onChange={(event) => setJevKey(event.target.value)} autoComplete="new-password" spellCheck={false} />
          </Field>
        ) : null}
      </div>
      {canEdit ? (
        <div className="pf-row" style={{ gap: 6 }}>
          <Button size="sm" variant="primary" disabled={locked} onClick={() => void run("jev", async () => { await onSaveSide?.({ jev: { model: jevModel.trim(), url: jevUrl.trim(), api_key: jevKey.trim() } }); setJevKey(""); })}>
            {busy === "jev" ? t("settings.gateSaving") : t("settings.orch.saveJev")}
          </Button>
          <Button size="sm" disabled={locked || !jev?.key_saved} onClick={() => void run("jev", async () => { await onSaveSide?.({ jev: { model: jevModel.trim(), url: jevUrl.trim(), clear_key: true } }); setJevKey(""); })}>
            {t("settings.orch.clearKey")}
          </Button>
        </div>
      ) : null}
      <Divider />
      <GateArmForm
        kind="plain"
        title={t("settings.gate.plain")}
        hint={t("settings.gate.plainHint")}
        arm={fallbackArm("plain", config)}
        providers={providers}
        canEdit={canEdit && !locked}
        onSave={async (body) => { await onSaveSide?.({ plain: body }); }}
      />
      <Divider />
      <GateArmForm
        kind="reason"
        title={t("settings.gate.reason")}
        hint={t("settings.gate.reasonHint")}
        arm={fallbackArm("reason", config)}
        providers={providers}
        canEdit={canEdit && !locked}
        onSave={async (body) => { await onSaveSide?.({ reason: body }); }}
      />
      {busy === "choice" ? <Text size="xs" tone="tertiary">{t("settings.gateSaving")}</Text> : null}
      {error ? <Text size="xs" tone="tertiary">{error}</Text> : null}
    </div>
  );
}

function GateArmForm({
  kind,
  title,
  hint,
  arm,
  providers,
  canEdit,
  onSave,
}: {
  kind: "plain" | "reason";
  title: string;
  hint: string;
  arm: GateArm;
  providers: LlmProvider[];
  canEdit: boolean;
  onSave: (body: GateArmBody) => Promise<void>;
}) {
  const { t } = useI18n();
  const formId = useId();
  const [providerId, setProviderId] = useState(arm.provider || "poe");
  const [model, setModel] = useState(arm.model);
  const [baseUrl, setBaseUrl] = useState(arm.base_url);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [temperature, setTemperature] = useState(String(arm.temperature));
  const [maxTokens, setMaxTokens] = useState(String(arm.max_tokens));
  const [reasoning, setReasoning] = useState(arm.reasoning || (kind === "plain" ? "none" : "low"));
  const [models, setModels] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [working, setWorking] = useState<"test" | "save" | null>(null);

  useEffect(() => {
    setProviderId(arm.provider || "poe");
    setModel(arm.model);
    setBaseUrl(arm.base_url);
    setTemperature(String(arm.temperature));
    setMaxTokens(String(arm.max_tokens));
    setReasoning(arm.reasoning || (kind === "plain" ? "none" : "low"));
  }, [arm, kind]);

  const catalogue = providers.length
    ? providers
    : [{ id: providerId, label: providerId, default_model: model, key_prefix: "", supports_search: false }];
  const provider = catalogue.find((entry) => entry.id === providerId) ?? catalogue[0];
  const needsBaseUrl = Boolean(provider?.needs_base_url);
  const prefixWarning = provider && keyPrefixMismatch(apiKey, provider.key_prefix)
    ? t("keys.prefixMismatch", { provider: provider.label, prefix: provider.key_prefix })
    : null;

  const changeProvider = (id: string) => {
    setProviderId(id);
    setApiKey("");
    setShowKey(false);
    setBaseUrl("");
    setModel(providers.find((entry) => entry.id === id)?.default_model ?? "");
    setModels([]);
    setNote(null);
  };

  const parsed = () => {
    if (!temperature.trim() || !maxTokens.trim()) return null;
    const heat = Number(temperature);
    const cap = Number(maxTokens);
    if (!Number.isFinite(heat) || heat < 0 || heat > 2) return null;
    if (!Number.isInteger(cap) || cap < 16 || cap > 16384) return null;
    return { temperature: heat, max_tokens: cap };
  };

  const body = (clearKey: boolean): GateArmBody | null => {
    const numbers = parsed();
    if (!numbers || !model.trim()) return null;
    return {
      provider: providerId,
      model: model.trim(),
      base_url: needsBaseUrl ? baseUrl.trim() : "",
      api_key: clearKey ? "" : apiKey.trim(),
      clear_key: clearKey,
      temperature: numbers.temperature,
      max_tokens: numbers.max_tokens,
      reasoning,
    };
  };

  const status = () => {
    if (arm.key_source === "arm" && arm.key_tail) return t("settings.orch.keySaved", { tail: arm.key_tail });
    if (arm.key_source === "poe" && arm.key_tail) return t("settings.orch.keyPoe", { tail: arm.key_tail });
    if (arm.key_set) return t("settings.orch.keyEnv");
    return t("settings.orch.keyNone");
  };

  const test = async () => {
    const numbers = parsed();
    if (!numbers) {
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

  const save = async (clearKey: boolean) => {
    const next = body(clearKey);
    if (!next) {
      setNote(t("settings.orch.hyperInvalid"));
      return;
    }
    setWorking("save");
    setNote(null);
    try {
      await onSave(next);
      setApiKey("");
      setShowKey(false);
      setNote(t("settings.savedUses", {
        name: title,
        provider: provider?.label || providerLabel(providerId, catalogue),
        model: model.trim(),
      }));
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(null);
    }
  };

  const canTest = Boolean(canEdit && apiKey.trim() && model.trim() && (!needsBaseUrl || baseUrl.trim()) && working === null);
  const canSave = Boolean(canEdit && model.trim() && (!needsBaseUrl || baseUrl.trim()) && working === null);

  return (
    <div className="pf-stack pf-stack--sm">
      <Text size="sm" weight="semibold">{title}</Text>
      <Text size="xs" tone="tertiary">{hint}</Text>
      <Text size="xs" tone="tertiary">{status()}</Text>
      <div className="pf-form-grid">
        <Field label={t("keys.provider")} htmlFor={`${formId}-provider`}>
          <Select id={`${formId}-provider`} value={providerId} disabled={!canEdit || working !== null} onChange={(event) => changeProvider(event.target.value)}>
            {catalogue.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.label}</option>
            ))}
          </Select>
        </Field>
        {needsBaseUrl ? (
          <Field wide label={t("keys.baseUrl")} htmlFor={`${formId}-base-url`} hint={t("keys.baseUrlHint")}>
            <Input id={`${formId}-base-url`} value={baseUrl} disabled={!canEdit || working !== null} autoComplete="off" spellCheck={false} maxLength={300} onChange={(event) => setBaseUrl(event.target.value)} />
          </Field>
        ) : null}
        <Field label={t("keys.model")} htmlFor={`${formId}-model`}>
          <Input id={`${formId}-model`} value={model} disabled={!canEdit || working !== null} list={`${formId}-models`} autoComplete="off" spellCheck={false} maxLength={120} onChange={(event) => setModel(event.target.value)} />
          <datalist id={`${formId}-models`}>
            {models.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </Field>
        {canEdit ? (
          <Field wide label={t("keys.apiKey")} htmlFor={`${formId}-key`} hint={t("settings.orch.armKeyHint")}>
            <div className="pf-row" style={{ gap: 6 }}>
              <Input
                id={`${formId}-key`}
                name={`${formId}-key`}
                type={showKey ? "text" : "password"}
                value={apiKey}
                disabled={working !== null}
                placeholder={provider?.key_prefix ? `${provider.key_prefix}…` : undefined}
                autoComplete="new-password"
                spellCheck={false}
                maxLength={512}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <Button size="sm" variant="ghost" aria-pressed={showKey} disabled={working !== null} onClick={() => setShowKey((shown) => !shown)}>
                {showKey ? t("keys.hide") : t("keys.show")}
              </Button>
            </div>
          </Field>
        ) : null}
        <Field label={t("settings.orch.temperature")} htmlFor={`${formId}-temperature`} hint={t("settings.orch.temperatureHint")}>
          <Input id={`${formId}-temperature`} value={temperature} disabled={!canEdit || working !== null} inputMode="decimal" onChange={(event) => setTemperature(event.target.value)} />
        </Field>
        <Field label={t("settings.orch.reasoning")} htmlFor={`${formId}-reasoning`} hint={t("settings.orch.reasoningHint")}>
          <Select id={`${formId}-reasoning`} value={reasoning} disabled={!canEdit || working !== null} onChange={(event) => setReasoning(event.target.value)}>
            {REASONING.map((level) => (
              <option key={level} value={level}>{t(REASONING_LABEL[level])}</option>
            ))}
          </Select>
        </Field>
        <Field label={t("settings.orch.maxTokens")} htmlFor={`${formId}-tokens`} hint={t("settings.orch.maxTokensHint")}>
          <Input id={`${formId}-tokens`} value={maxTokens} disabled={!canEdit || working !== null} inputMode="numeric" onChange={(event) => setMaxTokens(event.target.value)} />
        </Field>
      </div>
      {prefixWarning ? <Text size="xs" tone="tertiary">{prefixWarning}</Text> : null}
      {note ? <Text size="xs" tone="tertiary">{note}</Text> : null}
      {canEdit ? (
        <div className="pf-row" style={{ gap: 6 }}>
          <Button size="sm" variant="secondary" loading={working === "test"} disabled={!canTest} onClick={() => void test()}>
            {working === "test" ? t("keys.testing") : t("keys.test")}
          </Button>
          <Button size="sm" variant="primary" loading={working === "save"} disabled={!canSave} onClick={() => void save(false)}>
            {working === "save" ? t("keys.saving") : t("keys.save")}
          </Button>
          <Button size="sm" disabled={!canSave || !arm.key_saved} onClick={() => void save(true)}>
            {t("settings.orch.clearKey")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
