import { useEffect, useState } from "react";
import { addSerpKey, getSerpKeys, setSerpKeyStatus, type SerpKey, type SerpKeyStatus } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { MessageKey } from "../lib/locales/en";
import { Button, Input, Select, Text } from "./atoms";
import { Field } from "./molecules";

const STATUSES: SerpKeyStatus[] = ["active", "ready", "exhausted", "paused"];
const STATUS_LABEL: Record<SerpKeyStatus, MessageKey> = {
  active: "settings.serp.status.active",
  ready: "settings.serp.status.ready",
  exhausted: "settings.serp.status.exhausted",
  paused: "settings.serp.status.paused",
};

/** The SerpAPI key list. One key is in use. The next ready key takes over when that account runs out. */
export function SerpApiRotation({ canEdit }: { canEdit: boolean }) {
  const { t } = useI18n();
  const [keys, setKeys] = useState<SerpKey[]>([]);
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [status, setStatus] = useState<SerpKeyStatus>("ready");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => getSerpKeys().then((data) => setKeys(data.keys));

  useEffect(() => {
    if (!canEdit) return;
    void reload().catch((cause) => setNote(cause instanceof Error ? cause.message : String(cause)));
  }, [canEdit]);

  if (!canEdit) return null;

  const run = async (work: () => Promise<{ keys: SerpKey[] }>) => {
    setBusy(true);
    setNote(null);
    try {
      const data = await work();
      setKeys(data.keys);
      setNote(t("settings.serp.saved"));
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pf-card pf-eval__panel">
      <Text size="sm" weight="semibold">{t("settings.serp.title")}</Text>
      <Text size="xs" tone="tertiary">{t("settings.serp.body")}</Text>
      {keys.length ? (
        <div className="pf-stack pf-stack--sm">
          {keys.map((row) => (
            <div className="pf-row pf-row--between" key={row.id} style={{ gap: 8 }}>
              <span className="pf-small">
                {row.label || t("settings.serp.title")}
                <span className="pf-mono pf-muted"> · {t("keys.tail", { tail: row.key_tail })}</span>
              </span>
              <Select
                aria-label={t("settings.serp.title")}
                value={row.status}
                disabled={busy}
                onChange={(event) => void run(() => setSerpKeyStatus(row.id, event.target.value as SerpKeyStatus))}
              >
                {STATUSES.map((item) => (
                  <option key={item} value={item}>{t(STATUS_LABEL[item])}</option>
                ))}
              </Select>
            </div>
          ))}
        </div>
      ) : (
        <Text size="xs" tone="tertiary">{t("settings.serp.empty")}</Text>
      )}
      <div className="pf-form-grid">
        <Field label={t("settings.serp.label")} htmlFor="pf-serp-label">
          <Input id="pf-serp-label" value={label} disabled={busy} maxLength={80} onChange={(event) => setLabel(event.target.value)} />
        </Field>
        <Field wide label={t("keys.apiKey")} htmlFor="pf-serp-key">
          <Input
            id="pf-serp-key"
            type="password"
            value={apiKey}
            disabled={busy}
            autoComplete="new-password"
            spellCheck={false}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </Field>
        <Field label={t("settings.serp.title")} htmlFor="pf-serp-status">
          <Select id="pf-serp-status" value={status} disabled={busy} onChange={(event) => setStatus(event.target.value as SerpKeyStatus)}>
            {STATUSES.map((item) => (
              <option key={item} value={item}>{t(STATUS_LABEL[item])}</option>
            ))}
          </Select>
        </Field>
      </div>
      {note ? <Text size="xs" tone="tertiary">{note}</Text> : null}
      <Button
        size="sm"
        variant="primary"
        disabled={busy || apiKey.trim().length < 8}
        onClick={() => void run(async () => {
          const data = await addSerpKey({ api_key: apiKey.trim(), label: label.trim(), status });
          setApiKey("");
          setLabel("");
          return data;
        })}
      >
        {t("settings.serp.add")}
      </Button>
    </div>
  );
}
