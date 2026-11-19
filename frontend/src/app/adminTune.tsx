import { useEffect, useState } from "react";
import { Button, Field, Select, Switch, Text, Textarea } from "../design-system";
import {
  getEscalation,
  getPrompts,
  resetPrompt,
  saveEscalation,
  savePrompt,
  type MonitorPart,
  type PromptRow,
} from "../lib/api";
import { useI18n } from "../lib/i18n";

type ScoreDraft = { text: string; score: string };

type MonitorDraft = {
  enabled: boolean;
  engine: string;
  words: ScoreDraft[];
  examples: ScoreDraft[];
  score: string;
  message: string;
  after: string;
};

const EMPTY_MONITOR: MonitorDraft = {
  enabled: true,
  engine: "keywords",
  words: [],
  examples: [],
  score: "60",
  message: "",
  after: "3",
};

function rowsFrom(value: MonitorPart["words"] | string[] | undefined): ScoreDraft[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string") return item.trim() ? [{ text: item, score: "60" }] : [];
    const text = item.text || "";
    return text.trim() ? [{ text, score: item.score == null ? "60" : String(item.score) }] : [];
  });
}

function draftFrom(part: MonitorPart | undefined, fallback: MonitorDraft): MonitorDraft {
  if (!part) return fallback;
  return {
    enabled: part.enabled,
    engine: part.engine === "jev" ? "jev" : "keywords",
    words: rowsFrom(part.words),
    examples: rowsFrom(part.examples),
    score: part.score == null ? "60" : String(part.score),
    message: part.message || "",
    after: String(part.after || 3),
  };
}

function rowsToPart(rows: ScoreDraft[]) {
  return rows
    .map((row) => ({ text: row.text.trim(), score: Number(row.score) }))
    .filter((row) => row.text);
}

function draftToPart(draft: MonitorDraft): MonitorPart {
  const score = draft.score.trim();
  return {
    enabled: draft.enabled,
    engine: draft.engine === "jev" ? "jev" : "keywords",
    words: rowsToPart(draft.words),
    examples: rowsToPart(draft.examples),
    score: score === "" ? 60 : Number(score),
    message: draft.message,
    after: Number(draft.after),
  };
}

function ScoreTable({
  label,
  hint,
  rows,
  onChange,
  textLabel,
  scoreLabel,
  addLabel,
  removeLabel,
  scored = true,
}: {
  label: string;
  hint: string;
  rows: ScoreDraft[];
  onChange: (rows: ScoreDraft[]) => void;
  textLabel: string;
  scoreLabel: string;
  addLabel: string;
  removeLabel: string;
  scored?: boolean;
}) {
  const edit = (index: number, patch: Partial<ScoreDraft>) => {
    onChange(rows.map((row, place) => (place === index ? { ...row, ...patch } : row)));
  };
  return (
    <div className="pf-score">
      <Text size="xs" weight="semibold">{label}</Text>
      <Text size="xs" tone="tertiary">{hint}</Text>
      <table className="pf-score__table">
        <thead>
          <tr>
            <th>{textLabel}</th>
            {scored ? <th>{scoreLabel}</th> : null}
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td>
                <input className="pf-input" value={row.text} onChange={(event) => edit(index, { text: event.target.value })} />
              </td>
              {scored ? (
                <td>
                  <input className="pf-input" value={row.score} inputMode="numeric" onChange={(event) => edit(index, { score: event.target.value })} />
                </td>
              ) : null}
              <td>
                <button type="button" className="pf-score__remove" onClick={() => onChange(rows.filter((_, place) => place !== index))}>{removeLabel}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="pf-score__add" onClick={() => onChange([...rows, { text: "", score: "60" }])}>{addLabel}</button>
    </div>
  );
}

/** Admin-only: when a chat is handed to a person, and the instructions the models read. */
export function AdminTune({ show }: { show: (group: "handoff" | "instructions", text: string) => boolean }) {
  const { t } = useI18n();
  const [hand, setHand] = useState<MonitorDraft>({ ...EMPTY_MONITOR });
  const [sentiment, setSentiment] = useState<MonitorDraft>({ ...EMPTY_MONITOR });
  const [stuckPart, setStuckPart] = useState<MonitorDraft>({ ...EMPTY_MONITOR, enabled: false });
  const [jevKey, setJevKey] = useState("");
  const [keyMode, setKeyMode] = useState<"own" | "env">("env");
  const [keySource, setKeySource] = useState<"saved" | "gate" | "none">("none");
  const [prompts, setPrompts] = useState<PromptRow[]>([]);
  const [selected, setSelected] = useState("persona");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getEscalation()
      .then((policy) => {
        const parts = policy.parts;
        setHand(draftFrom(parts?.hand, {
          ...EMPTY_MONITOR,
          engine: "keywords",
          enabled: policy.enabled,
          message: policy.message,
          words: (policy.words ?? []).map((text) => ({ text, score: "60" })),
          examples: policy.examples.map((item) => ({ text: item.utterance, score: "60" })),
          score: policy.frustration_at == null ? "60" : String(policy.frustration_at),
        }));
        setSentiment(draftFrom(parts?.sentiment, { ...EMPTY_MONITOR, engine: policy.use_jev ? "jev" : "keywords" }));
        setStuckPart(draftFrom(parts?.stuck, { ...EMPTY_MONITOR, enabled: Boolean(policy.stuck_on), after: String(policy.stuck_after ?? 3) }));
        setJevKey("");
        setKeySource(policy.key_source === "saved" || policy.key_source === "gate" ? policy.key_source : "none");
        setKeyMode(policy.key_source === "saved" ? "own" : "env");
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
    const monitors = { hand: { ...draftToPart(hand), engine: "keywords" }, sentiment: draftToPart(sentiment), stuck: draftToPart(stuckPart) };
    const rows = [...monitors.hand.words, ...monitors.hand.examples, ...monitors.sentiment.words, ...monitors.sentiment.examples, ...monitors.stuck.words, ...monitors.stuck.examples];
    if (rows.some((row) => !Number.isInteger(row.score) || row.score < 0 || row.score > 100)) {
      setError(t("settings.part.barHint"));
      return;
    }
    for (const part of [monitors.hand, monitors.sentiment, monitors.stuck]) {
      if (part.score !== null && (!Number.isInteger(part.score) || part.score < 0 || part.score > 100)) {
        setError(t("settings.part.barHint"));
        return;
      }
    }
    if (!Number.isInteger(monitors.stuck.after) || monitors.stuck.after < 2 || monitors.stuck.after > 8) {
      setError(t("settings.escalationStuckHint"));
      return;
    }
    const usesJev = monitors.sentiment.engine === "jev";
    try {
      const saved = await saveEscalation({
        enabled: monitors.hand.enabled,
        message: monitors.hand.message,
        frustration_at: monitors.hand.score,
        examples: monitors.hand.examples.map((row) => row.text),
        match_mode: "keywords",
        use_jev: usesJev,
        stuck_on: monitors.stuck.enabled,
        stuck_after: monitors.stuck.after,
        words: monitors.hand.words.map((row) => row.text),
        api_key: usesJev && keyMode === "own" ? jevKey.trim() : "",
        clear_key: usesJev && keyMode === "env",
        monitors,
      });
      const parts = saved.parts;
      setHand(draftFrom(parts?.hand, hand));
      setSentiment(draftFrom(parts?.sentiment, sentiment));
      setStuckPart(draftFrom(parts?.stuck, stuckPart));
      setJevKey("");
      setKeySource(saved.key_source === "saved" || saved.key_source === "gate" ? saved.key_source : "none");
      setKeyMode(saved.key_source === "saved" ? "own" : "env");
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
      {show("handoff", `${t("settings.escalation")} ${t("settings.part.handTitle")} ${t("settings.part.sentimentTitle")} ${t("settings.part.stuckTitle")}`) ? (
      <>
        <Text size="xs" tone="tertiary">{t("settings.escalationBody")}</Text>
        <Switch
          checked={sentiment.engine === "jev"}
          onChange={(on) => setSentiment({ ...sentiment, engine: on ? "jev" : "keywords" })}
          label={t("settings.part.useJev")}
        />
        <Text size="xs" tone="tertiary">{t("settings.part.useJevHint")}</Text>
        {sentiment.engine === "jev" ? (
          <>
            <div className="pf-handoff__keys" role="radiogroup" aria-label={t("settings.escalationJevKey")}>
              <label className="pf-handoff__choice">
                <input type="radio" name="pf-handoff-jev-key" checked={keyMode === "env"} onChange={() => setKeyMode("env")} />
                <span>{t("settings.handoff.keyEnv")}</span>
              </label>
              <label className="pf-handoff__choice">
                <input type="radio" name="pf-handoff-jev-key" checked={keyMode === "own"} onChange={() => setKeyMode("own")} />
                <span>{t("settings.handoff.keyOwn")}</span>
              </label>
            </div>
            <Text size="xs" tone="tertiary">{t("settings.handoff.keyEnvNote")}</Text>
            {keyMode === "own" ? (
              <Field label={t("settings.escalationJevKey")} hint={t("settings.escalationJevKeyHint")} wide htmlFor="pf-handoff-key">
                <input id="pf-handoff-key" className="pf-input" type="password" value={jevKey} autoComplete="new-password" onChange={(event) => setJevKey(event.target.value)} />
              </Field>
            ) : null}
            <Text size="xs" tone="tertiary">
              {keySource === "saved" ? t("settings.escalationJevSaved") : keySource === "gate" ? t("settings.escalationJevGate") : t("settings.escalationJevNone")}
            </Text>
          </>
        ) : null}
        {([
          ["hand", t("settings.part.handTitle"), t("settings.part.handBody"), hand, setHand, true, false],
          ["sentiment", t("settings.part.sentimentTitle"), t("settings.part.sentimentBody"), sentiment, setSentiment, false, false],
          ["stuck", t("settings.part.stuckTitle"), t("settings.part.stuckBody"), stuckPart, setStuckPart, false, true],
        ] as const).map(([key, title, body, draft, setDraft, withMessage, withAfter]) => (
          <section key={key} className="pf-card pf-eval__panel pf-handoff">
            <Text size="sm" weight="semibold">{title}</Text>
            <Text size="xs" tone="tertiary">{body}</Text>
            <Switch
              checked={draft.enabled}
              onChange={(enabled) => setDraft({ ...draft, enabled })}
              label={key === "sentiment" ? t("settings.part.sentimentOn") : key === "stuck" ? t("settings.escalationStuck") : t("settings.escalationOn")}
            />
            {draft.enabled ? (
              <>
                {withMessage ? (
                  <Field label={t("settings.escalationMessage")} wide htmlFor={`pf-${key}-message`}>
                    <Textarea id={`pf-${key}-message`} rows={3} value={draft.message} onChange={(event) => setDraft({ ...draft, message: event.target.value })} />
                  </Field>
                ) : null}
                {key === "sentiment" ? (
                  <Field label={t("settings.part.bar")} hint={t("settings.part.barHint")} wide htmlFor={`pf-${key}-score`}>
                    <input id={`pf-${key}-score`} className="pf-input" value={draft.score} inputMode="numeric" onChange={(event) => setDraft({ ...draft, score: event.target.value })} />
                  </Field>
                ) : null}
                {withAfter ? (
                  <Field label={t("settings.escalationStuckAfter")} hint={t("settings.escalationStuckHint")} wide htmlFor={`pf-${key}-after`}>
                    <input id={`pf-${key}-after`} className="pf-input" value={draft.after} inputMode="numeric" onChange={(event) => setDraft({ ...draft, after: event.target.value })} />
                  </Field>
                ) : null}
                {key === "stuck" ? null : (
                <>
                <ScoreTable
                  label={key === "hand" ? t("settings.part.handList") : t("settings.part.keywords")}
                  hint={key === "hand" ? t("settings.part.handKeywordsHint") : t("settings.part.keywordsHint")}
                  rows={draft.words}
                  onChange={(words) => setDraft({ ...draft, words })}
                  textLabel={t("settings.part.colText")}
                  scoreLabel={t("settings.part.colScore")}
                  addLabel={t("settings.part.add")}
                  removeLabel={t("settings.part.remove")}
                  scored={key !== "hand"}
                />
                <ScoreTable
                  label={t("settings.part.examples")}
                  hint={key === "hand" ? t("settings.part.handExamplesHint") : t("settings.part.examplesHint")}
                  rows={draft.examples}
                  onChange={(examples) => setDraft({ ...draft, examples })}
                  textLabel={t("settings.part.colText")}
                  scoreLabel={t("settings.part.colScore")}
                  addLabel={t("settings.part.add")}
                  removeLabel={t("settings.part.remove")}
                  scored={key !== "hand"}
                />
                </>
                )}
              </>
            ) : null}
            <Button variant="primary" onClick={() => void saveHandoff()}>{t("settings.part.save")}</Button>
          </section>
        ))}
      </>
      ) : null}
      {show("instructions", `${t("settings.prompts")} ${t("settings.promptsBody")} persona language router ask planner`) ? (
      <section className="pf-card pf-eval__panel">
        <Text size="sm" weight="semibold">{t("settings.prompts")}</Text>
        <Text size="xs" tone="tertiary">{t("settings.promptsBody")}</Text>
        <div className="pf-form-grid">
          <Field label={t("settings.prompts")} htmlFor="pf-prompt-name">
            <Select id="pf-prompt-name" value={selected} onChange={(event) => choose(event.target.value)}>
              {prompts.map((row) => (
                <option key={row.name} value={row.name}>
                  {row.file ? `${row.name} — ${row.file.replace(/^backend\/app\/skills\//, "")}` : row.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("settings.promptDocument")} hint={current?.file ? t("settings.promptFile") : t("settings.promptMemory")} wide>
            <input
              className="pf-input pf-mono"
              readOnly
              aria-label={t("settings.promptDocument")}
              value={current?.file || ""}
              placeholder={t("settings.promptMemory")}
            />
          </Field>
          {selected === "review" ? <Text size="xs" tone="secondary">{t("settings.reviewFailed")}</Text> : null}
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
