import { useEffect, useId, useState } from "react";
import type { KeyBody, KeyTestResult, LlmKey, LlmProvider, LlmStatus } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { keyPrefixMismatch, providerLabel, statusLine } from "../lib/modelKeys";
import { Badge, Button, Checkbox, Divider, Input, Select, Text } from "./atoms";
import { Banner } from "./organisms";
import { Field } from "./molecules";

export { statusLine };

/** What a save answers with; `null` means it failed (the page shows why through `error`) and the form is left as typed. */
export type SaveOutcome = { warning?: string } | null;

export type ModelKeysProps = {
  title: string;
  /** One sentence under the title saying whose key this is. */
  body?: string;
  providers: LlmProvider[];
  keys: LlmKey[];
  status: LlmStatus | null;
  /** True while a request for this card is in flight. */
  busy?: boolean;
  /** Why the last load or change failed. */
  error?: string | null;
  /** `null` when the request itself failed. */
  onTest(body: KeyBody): Promise<KeyTestResult | null>;
  onSave(body: KeyBody): Promise<SaveOutcome>;
  onRetest(id: string): Promise<KeyTestResult | null>;
  onPatch(id: string, body: { model?: string; use_for_search?: boolean }): Promise<void>;
  onChoose(id: string | null): Promise<void>;
  onDelete(id: string): Promise<void>;
};

function whenText(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function testLine(result: KeyTestResult, t: ReturnType<typeof useI18n>["t"]): string {
  return result.ok ? t("keys.testOk", { ms: Math.round(result.latency_ms) }) : t("keys.testFailed", { message: result.message });
}

export function ModelKeysCard({
  title,
  body,
  providers,
  keys,
  status,
  busy = false,
  error = null,
  onTest,
  onSave,
  onRetest,
  onPatch,
  onChoose,
  onDelete,
}: ModelKeysProps) {
  const { t } = useI18n();
  const formId = useId();
  const first = providers[0];
  const [providerId, setProviderId] = useState(first?.id ?? "");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [model, setModel] = useState(first?.default_model ?? "");
  const [models, setModels] = useState<string[]>([]);
  const [useForSearch, setUseForSearch] = useState(true);
  const [formNote, setFormNote] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [working, setWorking] = useState<"test" | "save" | null>(null);

  const provider = providers.find((entry) => entry.id === providerId) ?? first;
  // The providers arrive after the first render: pick the first one then.
  useEffect(() => {
    if (!providers.some((entry) => entry.id === providerId) && providers[0]) {
      setProviderId(providers[0].id);
      setModel(providers[0].default_model);
    }
  }, [providers, providerId]);

  const changeProvider = (id: string) => {
    setProviderId(id);
    // A key typed for one vendor is never sent to another one by accident.
    setApiKey("");
    setShowKey(false);
    setModel(providers.find((entry) => entry.id === id)?.default_model ?? "");
    setModels([]);
    setFormNote(null);
  };

  const formBody = (): KeyBody => ({
    provider: providerId,
    api_key: apiKey,
    model: model.trim(),
    use_for_search: provider?.supports_search ? useForSearch : undefined,
  });

  const test = async () => {
    setWorking("test");
    setFormNote(null);
    const result = await onTest(formBody());
    setWorking(null);
    if (!result) return;
    if (result.models.length) setModels(result.models);
    setFormNote({ tone: result.ok ? "info" : "danger", text: testLine(result, t) });
  };

  const save = async () => {
    setWorking("save");
    setFormNote(null);
    const outcome = await onSave(formBody());
    setWorking(null);
    if (!outcome) return;
    // The key leaves the page's memory as soon as it is saved; it was never written to storage.
    setApiKey("");
    setShowKey(false);
    setModels([]);
    setFormNote({
      tone: "info",
      text: outcome.warning ? t("keys.savedWithWarning", { message: outcome.warning }) : t("keys.saved"),
    });
  };

  const noKey = !keys.some((key) => key.active);
  const prefix = provider?.key_prefix ?? "";
  const prefixWarning = provider && keyPrefixMismatch(apiKey, prefix)
    ? t("keys.prefixMismatch", { provider: provider.label, prefix })
    : null;
  const canSubmit = Boolean(providerId && apiKey.trim() && model.trim());

  return (
    <div className="pf-card pf-eval__panel" aria-busy={busy || undefined}>
      <Text size="sm" weight="semibold">{title}</Text>
      {body ? <Text size="xs" tone="tertiary">{body}</Text> : null}
      {error ? <Banner>{error}</Banner> : null}
      {status ? (
        <Badge tone={status.source === "none" || status.problem ? "warning" : "success"} dot>
          {statusLine(status, t, providers)}
        </Badge>
      ) : null}

      <div className="pf-stack pf-stack--sm" role="radiogroup" aria-label={title}>
        {keys.length ? (
          <label className="pf-row" style={{ gap: 8 }}>
            <input
              type="radio"
              name={`${formId}-choice`}
              checked={noKey}
              disabled={busy}
              onChange={() => void onChoose(null)}
            />
            <span className="pf-small">{t("keys.useDefault")}</span>
          </label>
        ) : (
          <Text size="xs" tone="tertiary">{t("keys.empty")}</Text>
        )}
        {keys.map((key) => (
          <KeyRow
            key={key.id}
            entry={key}
            groupName={`${formId}-choice`}
            providers={providers}
            busy={busy}
            onRetest={onRetest}
            onPatch={onPatch}
            onChoose={onChoose}
            onDelete={onDelete}
          />
        ))}
      </div>

      <Divider />
      <Text size="sm" weight="semibold">{t("keys.add")}</Text>
      <div className="pf-form-grid">
        <Field label={t("keys.provider")} htmlFor={`${formId}-provider`}>
          <Select id={`${formId}-provider`} value={providerId} onChange={(event) => changeProvider(event.target.value)}>
            {providers.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.label}</option>
            ))}
          </Select>
        </Field>
        <Field label={t("keys.model")} htmlFor={`${formId}-model`}>
          <Input
            id={`${formId}-model`}
            value={model}
            list={`${formId}-models`}
            autoComplete="off"
            spellCheck={false}
            maxLength={120}
            onChange={(event) => setModel(event.target.value)}
          />
          <datalist id={`${formId}-models`}>
            {models.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </Field>
        <Field label={t("keys.apiKey")} htmlFor={`${formId}-key`} wide>
          <div className="pf-row" style={{ gap: 6 }}>
            <Input
              id={`${formId}-key`}
              type={showKey ? "text" : "password"}
              value={apiKey}
              placeholder={provider?.key_prefix ? `${provider.key_prefix}…` : undefined}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={512}
              onChange={(event) => setApiKey(event.target.value)}
            />
            <Button
              size="sm"
              variant="ghost"
              aria-pressed={showKey}
              onClick={() => setShowKey((shown) => !shown)}
            >
              {showKey ? t("keys.hide") : t("keys.show")}
            </Button>
          </div>
        </Field>
      </div>
      {provider?.supports_search ? (
        <Checkbox
          checked={useForSearch}
          onChange={(event) => setUseForSearch(event.target.checked)}
          label={t("keys.search")}
        />
      ) : null}
      {prefixWarning ? <Banner tone="info">{prefixWarning}</Banner> : null}
      {formNote ? <Banner tone={formNote.tone}>{formNote.text}</Banner> : null}
      <div className="pf-row" style={{ gap: 6 }}>
        <Button size="sm" variant="secondary" loading={working === "test"} disabled={!canSubmit || busy || working !== null} onClick={() => void test()}>
          {working === "test" ? t("keys.testing") : t("keys.test")}
        </Button>
        <Button size="sm" variant="primary" loading={working === "save"} disabled={!canSubmit || busy || working !== null} onClick={() => void save()}>
          {working === "save" ? t("keys.saving") : t("keys.save")}
        </Button>
      </div>
    </div>
  );
}

function KeyRow({
  entry,
  groupName,
  providers,
  busy,
  onRetest,
  onPatch,
  onChoose,
  onDelete,
}: {
  entry: LlmKey;
  groupName: string;
  providers: LlmProvider[];
  busy: boolean;
  onRetest: ModelKeysProps["onRetest"];
  onPatch: ModelKeysProps["onPatch"];
  onChoose: ModelKeysProps["onChoose"];
  onDelete: ModelKeysProps["onDelete"];
}) {
  const { t } = useI18n();
  const [model, setModel] = useState(entry.model);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<KeyTestResult | null>(null);
  useEffect(() => setModel(entry.model), [entry.model]);

  const label = providerLabel(entry.provider, providers);
  const supportsSearch = providers.find((provider) => provider.id === entry.provider)?.supports_search ?? false;
  const commitModel = () => {
    const next = model.trim();
    if (next && next !== entry.model) void onPatch(entry.id, { model: next });
    else setModel(entry.model);
  };
  const retest = async () => {
    setTesting(true);
    setResult(await onRetest(entry.id));
    setTesting(false);
  };
  const remove = () => {
    if (window.confirm(t("keys.removeConfirm", { provider: label, tail: entry.key_tail }))) void onDelete(entry.id);
  };

  const lastLine = result
    ? testLine(result, t)
    : entry.last_test_ok === null || !entry.last_tested_at
      ? t("keys.neverTested")
      : entry.last_test_ok
        ? t("keys.lastOk", { when: whenText(entry.last_tested_at) })
        : t("keys.lastFailed", { when: whenText(entry.last_tested_at), message: entry.last_error });

  return (
    <div className="pf-stack pf-stack--sm">
      <div className="pf-row pf-row--between">
        <label className="pf-row" style={{ gap: 8 }}>
          <input
            type="radio"
            name={groupName}
            checked={entry.active}
            disabled={busy || entry.unreadable}
            aria-label={t("keys.choose", { provider: label, tail: entry.key_tail })}
            onChange={() => void onChoose(entry.id)}
          />
          <span className="pf-small">{label}</span>
          <span className="pf-mono pf-small pf-muted">{t("keys.tail", { tail: entry.key_tail })}</span>
          {entry.active ? <Badge tone="success">{t("keys.inUse")}</Badge> : null}
        </label>
        <Button size="sm" variant="ghost" disabled={busy} onClick={remove}>
          {t("keys.remove")}
        </Button>
      </div>
      {entry.unreadable ? (
        <Text size="xs" tone="tertiary">{t("keys.unreadable")}</Text>
      ) : (
        <>
          <div className="pf-row" style={{ gap: 6 }}>
            <Input
              value={model}
              aria-label={t("keys.modelFor", { provider: label, tail: entry.key_tail })}
              list={`${groupName}-${entry.id}-models`}
              autoComplete="off"
              spellCheck={false}
              maxLength={120}
              onChange={(event) => setModel(event.target.value)}
              onBlur={commitModel}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
              }}
            />
            <datalist id={`${groupName}-${entry.id}-models`}>
              {result?.models.map((name) => <option key={name} value={name} />)}
            </datalist>
            <Button size="sm" variant="secondary" loading={testing} disabled={busy} onClick={() => void retest()}>
              {t("keys.test")}
            </Button>
          </div>
          <Text size="xs" tone="tertiary">
            {lastLine}
          </Text>
          {supportsSearch ? (
            <Checkbox
              checked={entry.use_for_search}
              disabled={busy}
              onChange={(event) => void onPatch(entry.id, { use_for_search: event.target.checked })}
              label={t("keys.search")}
            />
          ) : null}
        </>
      )}
    </div>
  );
}
