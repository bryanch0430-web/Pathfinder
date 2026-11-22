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

/**
 * Operator settings that actually exist in v5.
 *
 * Handoff has two independent signals: the analyst's frustration estimate and a
 * stuck counter. An explicit request for a person always wins. The old keyword
 * lists, example matching, Jev pre-gate and scored monitor rows are gone because
 * no code reads them in this build.
 */
export function AdminTune({ show }: { show: (group: "handoff" | "instructions", text: string) => boolean }) {
  const { t } = useI18n();
  const [liveAgent, setLiveAgent] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [frustration, setFrustration] = useState("0.75");
  const [stuckOn, setStuckOn] = useState(true);
  const [stuckAfter, setStuckAfter] = useState("3");
  const [message, setMessage] = useState("");
  const [phrases, setPhrases] = useState("");
  const [prompts, setPrompts] = useState<PromptRow[]>([]);
  const [selected, setSelected] = useState("persona");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getEscalation()
      .then((policy) => {
        setLiveAgent(policy.live_agent !== false);
        setEnabled(Boolean(policy.enabled));
        setFrustration(String(policy.frustration_at ?? 0.75));
        setStuckOn(Boolean(policy.stuck_on));
        setStuckAfter(String(policy.stuck_after ?? 3));
        setMessage(policy.message || "");
        setPhrases((policy.human_phrases || []).join("\n"));
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
    const threshold = Number(frustration);
    const after = Number(stuckAfter);
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
      setError("The frustration threshold is a number from 0 to 1.");
      return;
    }
    if (!Number.isInteger(after) || after < 2 || after > 8) {
      setError("The stuck count is a whole number from 2 to 8.");
      return;
    }
    try {
      const saved = await saveEscalation({
        live_agent: liveAgent,
        enabled,
        message,
        frustration_at: threshold,
        stuck_on: stuckOn,
        stuck_after: after,
        human_phrases: phrases.split("\n").map((line) => line.trim()).filter(Boolean),
      });
      setEnabled(Boolean(saved.enabled));
      setFrustration(String(saved.frustration_at ?? threshold));
      setStuckOn(Boolean(saved.stuck_on));
      setStuckAfter(String(saved.stuck_after ?? after));
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
      {show("handoff", "handoff live agent stuck frustration threshold") ? (
        <>
          <Text size="xs" tone="tertiary">
            Live-agent handoff can be turned off completely. While it is on, an explicit request
            always reaches the queue; automatic handoff and the stuck counter are separate options.
          </Text>
          <section className="pf-card pf-eval__panel pf-handoff">
            <Text size="sm" weight="semibold">Live agent</Text>
            <Text size="xs" tone="tertiary">
              When this is off, no chat goes to the desk and an explicit request is answered by
              Pathfinder. Turn it on to allow the queue.
            </Text>
            <Switch checked={liveAgent} onChange={setLiveAgent} label="Allow live-agent handoff" />
            <Button variant="primary" onClick={() => void saveHandoff()}>Save live agent</Button>
          </section>
          {liveAgent ? (
          <>
          <section className="pf-card pf-eval__panel pf-handoff">
            <Text size="sm" weight="semibold">Automatic handoff</Text>
            <Text size="xs" tone="tertiary">
              When this is on, a high frustration score or a stuck streak can hand the chat to the
              desk. When it is off, only an explicit request brings a person in.
            </Text>
            <Switch checked={enabled} onChange={setEnabled} label="Allow automatic handoff" />
            {enabled ? (
              <Field label="Frustration threshold (0–1)" hint="0.75 means clearly angry; 0.9 is reserved for the worst cases." wide>
                <input
                  className="pf-input"
                  value={frustration}
                  inputMode="decimal"
                  onChange={(event) => setFrustration(event.target.value)}
                />
              </Field>
            ) : null}
            <Field label="Handoff message" wide>
              <Textarea rows={3} value={message} onChange={(event) => setMessage(event.target.value)} />
            </Field>
            <Button variant="primary" onClick={() => void saveHandoff()}>Save this monitor</Button>
          </section>
          <section className="pf-card pf-eval__panel pf-handoff">
            <Text size="sm" weight="semibold">Explicit request phrases</Text>
            <Text size="xs" tone="tertiary">
              When the traveller's message contains one of these phrases, the chat goes to the
              queue for a person to accept, even while automatic handoff is off. One phrase per line.
            </Text>
            <Field label="Hand off when the traveller says" wide>
              <Textarea
                rows={5}
                value={phrases}
                onChange={(event) => setPhrases(event.target.value)}
                placeholder="live agent, real person, talk to a person"
              />
            </Field>
            <Button variant="primary" onClick={() => void saveHandoff()}>Save phrases</Button>
          </section>
          <section className="pf-card pf-eval__panel pf-handoff">
            <Text size="sm" weight="semibold">Stuck chats</Text>
            <Text size="xs" tone="tertiary">
              Only runs while automatic handoff is on. Counts edits that leave the itinerary
              unchanged; a successful plan or edit resets the count.
            </Text>
            <Switch
              checked={stuckOn}
              onChange={setStuckOn}
              disabled={!enabled}
              label="Flag a chat that is stuck"
            />
            {stuckOn ? (
              <Field label="Failed turns before a chat is stuck" hint="A whole number from 2 to 8." wide>
                <input
                  className="pf-input"
                  value={stuckAfter}
                  inputMode="numeric"
                  onChange={(event) => setStuckAfter(event.target.value)}
                />
              </Field>
            ) : null}
            <Button variant="primary" onClick={() => void saveHandoff()}>Save this monitor</Button>
          </section>
          </>
          ) : null}
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
