import { useEffect, useState } from "react";
import { Button, Field, Select, Switch, Text, Textarea } from "../design-system";
import {
  getEscalation,
  getPrompts,
  resetPrompt,
  saveEscalation,
  savePrompt,
  type PromptRow,
} from "../lib/api";
import { useI18n } from "../lib/i18n";

/** Admin-only: when a chat is handed to a person, and the instructions the models read. */
export function AdminTune({ show }: { show: (group: "handoff" | "instructions", text: string) => boolean }) {
  const { t } = useI18n();
  const [enabled, setEnabled] = useState(true);
  const [message, setMessage] = useState("");
  const [heat, setHeat] = useState("");
  const [examples, setExamples] = useState("");
  const [prompts, setPrompts] = useState<PromptRow[]>([]);
  const [selected, setSelected] = useState("persona");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getEscalation()
      .then((policy) => {
        setEnabled(policy.enabled);
        setMessage(policy.message);
        setHeat(policy.frustration_at == null ? "" : String(policy.frustration_at));
        setExamples(policy.examples.map((item) => item.utterance).join("\n"));
      })
      .catch((cause) => setError(String(cause)));
    void getPrompts()
      .then((data) => {
        setPrompts(data.prompts);
        const first = data.prompts[0];
        if (first) {
          setSelected(first.name);
          setDraft(first.override);
        }
      })
      .catch((cause) => setError(String(cause)));
  }, []);

  const current = prompts.find((row) => row.name === selected);

  const choose = (name: string) => {
    setSelected(name);
    setDraft(prompts.find((row) => row.name === name)?.override ?? "");
  };

  const saveHandoff = async () => {
    setError(null);
    const trimmed = heat.trim();
    const frustration = trimmed === "" ? null : Number(trimmed);
    if (frustration !== null && (!Number.isInteger(frustration) || frustration < 0 || frustration > 100)) {
      setError(t("settings.escalationHeatHint"));
      return;
    }
    try {
      const saved = await saveEscalation({
        enabled,
        message,
        frustration_at: frustration,
        examples: examples.split("\n"),
      });
      setMessage(saved.message);
      setExamples(saved.examples.map((item) => item.utterance).join("\n"));
    } catch (cause) {
      setError(String(cause));
    }
  };

  const saveInstruction = async () => {
    setError(null);
    try {
      if (!draft.trim()) {
        await resetPrompt(selected);
      } else {
        await savePrompt(selected, draft);
      }
      setPrompts((rows) => rows.map((row) => (row.name === selected ? { ...row, override: draft.trim() } : row)));
    } catch (cause) {
      setError(String(cause));
    }
  };

  return (
    <>
      {error ? <p role="alert">{error}</p> : null}
      {show("handoff", `${t("settings.escalation")} ${t("settings.escalationBody")} ${t("settings.escalationHeat")} ${t("settings.escalationExamples")}`) ? (
      <section className="pf-card pf-eval__panel">
        <Text size="sm" weight="semibold">{t("settings.escalation")}</Text>
        <Text size="xs" tone="tertiary">{t("settings.escalationBody")}</Text>
        <Switch checked={enabled} onChange={setEnabled} label={t("settings.escalationOn")} />
        <div className="pf-form-grid">
          <Field label={t("settings.escalationMessage")} wide htmlFor="pf-handoff-message">
            <Textarea id="pf-handoff-message" rows={3} value={message} onChange={(event) => setMessage(event.target.value)} />
          </Field>
          <Field label={t("settings.escalationHeat")} hint={t("settings.escalationHeatHint")} wide htmlFor="pf-handoff-heat">
            <input id="pf-handoff-heat" className="pf-input" value={heat} onChange={(event) => setHeat(event.target.value)} inputMode="numeric" placeholder="60" />
            <span className="pf-heat-scale">
              <span className="pf-learn__heat" data-band="calm">0–24 {t("desk.band.calm")}</span>
              <span className="pf-learn__heat" data-band="uneasy">25–59 {t("desk.band.uneasy")}</span>
              <span className="pf-learn__heat" data-band="hot">60–100 {t("desk.band.hot")}</span>
            </span>
          </Field>
          <Field label={t("settings.escalationExamples")} wide htmlFor="pf-handoff-examples">
            <Textarea id="pf-handoff-examples" rows={4} value={examples} onChange={(event) => setExamples(event.target.value)} />
          </Field>
        </div>
        <Button variant="primary" onClick={() => void saveHandoff()}>{t("settings.escalationSave")}</Button>
      </section>
      ) : null}
      {show("instructions", `${t("settings.prompts")} ${t("settings.promptsBody")} persona language router ask planner`) ? (
      <section className="pf-card pf-eval__panel">
        <Text size="sm" weight="semibold">{t("settings.prompts")}</Text>
        <Text size="xs" tone="tertiary">{t("settings.promptsBody")}</Text>
        <div className="pf-form-grid">
          <Field label={t("settings.prompts")} htmlFor="pf-prompt-name">
            <Select id="pf-prompt-name" value={selected} onChange={(event) => choose(event.target.value)}>
              {prompts.map((row) => (
                <option key={row.name} value={row.name}>{row.name}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("settings.promptsReset")} hint={current?.description} wide htmlFor="pf-prompt-body">
            <Textarea id="pf-prompt-body" rows={8} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={current?.default ?? ""} />
          </Field>
        </div>
        <div className="pf-admin-tune__actions">
          <Button variant="primary" onClick={() => void saveInstruction()}>{t("settings.promptsSave")}</Button>
          <Button onClick={() => { setDraft(""); void resetPrompt(selected).then(() => setPrompts((rows) => rows.map((row) => row.name === selected ? { ...row, override: "" } : row))); }}>{t("settings.promptsReset")}</Button>
        </div>
      </section>
      ) : null}
    </>
  );
}
