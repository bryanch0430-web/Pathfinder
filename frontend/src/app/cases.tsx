import { useEffect, useState } from "react";
import { Badge, Button, Text } from "../design-system/atoms";
import { SituationReading, WriterDiagnosis } from "../design-system/writerDiagnosis";
import { useI18n, type MessageKey } from "../lib/i18n";
import {
  acceptSuggestion,
  addOperatorCase,
  checkReviewCases,
  dismissSuggestion,
  getCaptureTurns,
  getReviewCases,
  restoreSuggestion,
  scanReviewCases,
  type CaptureTurn,
  type ReviewCase,
  type SkillSuggestion,
} from "../lib/api";
import { failureOf, describeFailure, type Failure } from "../lib/failure";

const KIND: Record<ReviewCase["kind"], MessageKey> = {
  misclassified: "cases.misclassified",
  failed: "cases.failed",
  unchanged: "cases.unchanged",
  not_useful: "cases.notUseful",
  added: "cases.added",
};

const SETTING_NAME: Record<string, string> = {
  router_confidence: "confidence gate",
  gate_plain_temperature: "plain temperature",
  gate_reason_temperature: "reasoning temperature",
};

/** The prompt name the writer may replace, and the built-in skill file it stands for. */
const SKILL_FILE: Record<string, string> = {
  modify: "edit-itinerary/SKILL.md",
  clarify: "clarify-trip/SKILL.md",
  planner: "compose-itinerary/SKILL.md",
  ask: "answer-question/SKILL.md",
  router: "route-message/SKILL.md",
  understand: "read-trip-request/SKILL.md",
  intake: "read-shared-material/SKILL.md",
  research: "judge-stay/SKILL.md",
  audit: "audit-candidates/SKILL.md",
  repair: "repair-plan/SKILL.md",
};

export function CasesPage() {
  const { t } = useI18n();
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [suggestions, setSuggestions] = useState<SkillSuggestion[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [restored, setRestored] = useState<Record<string, boolean>>({});
  const [turns, setTurns] = useState<CaptureTurn[]>([]);
  const [message, setMessage] = useState("");
  const [expected, setExpected] = useState("");
  const [note, setNote] = useState("");
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState<"scan" | string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [score, setScore] = useState<{ passed: number; exam: number } | null>(null);
  const [error, setError] = useState<Failure | null>(null);

  const load = () => {
    getReviewCases()
      .then((data) => {
        setCases(data.cases);
        setSuggestions(data.suggestions);
        setDrafts(Object.fromEntries(data.suggestions.map((item) => [item.id, item.kind === "setting" ? item.setting_value || item.body : item.body])));
      })
      .catch((cause) => setError(failureOf(cause, "errors.feedback")));
  };

  useEffect(() => {
    load();
    getCaptureTurns().then((data) => setTurns(data.turns)).catch(() => setTurns([]));
  }, []);

  const scan = async () => {
    setBusy("scan");
    setError(null);
    setNotice(null);
    try {
      const data = await scanReviewCases();
      setNotice(data.suggestions.length ? null : t("cases.noSuggestion"));
      load();
    } catch (cause) {
      setError(failureOf(cause, "errors.feedback"));
    } finally {
      setBusy(null);
    }
  };

  const check = async () => {
    setBusy("check");
    setError(null);
    setNotice(null);
    try {
      const data = await checkReviewCases();
      setCases(data.cases);
      setScore({ passed: data.passed, exam: data.exam });
    } catch (cause) {
      setError(failureOf(cause, "errors.feedback"));
    } finally {
      setBusy(null);
    }
  };

  const accept = async (item: SkillSuggestion) => {
    setBusy(item.id);
    setError(null);
    setNotice(null);
    try {
      await acceptSuggestion(item.id, drafts[item.id] ?? item.body);
      setRestored((current) => ({ ...current, [item.skill]: false }));
      load();
    } catch (cause) {
      setError(failureOf(cause, "errors.feedback"));
    } finally {
      setBusy(null);
    }
  };

  const dismiss = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      await dismissSuggestion(id);
      load();
    } catch (cause) {
      setError(failureOf(cause, "errors.feedback"));
    } finally {
      setBusy(null);
    }
  };

  const restore = async (item: SkillSuggestion) => {
    setBusy(item.id);
    setError(null);
    try {
      await restoreSuggestion(item.id);
      setRestored((current) => ({ ...current, [item.skill]: true }));
      setNotice(t("cases.restored"));
      load();
    } catch (cause) {
      setError(failureOf(cause, "errors.feedback"));
    } finally {
      setBusy(null);
    }
  };

  const saveCase = async () => {
    const turn = turns.find((item) => item.id === picked);
    const text = (turn?.input || message).trim();
    if (!text) return;
    setBusy("add");
    setError(null);
    try {
      const data = await addOperatorCase({
        message: text,
        expected,
        note: note || turn?.reply || "",
        session_id: turn?.session_id || "",
        session_state: turn?.session_state || "",
      });
      setCases(data.cases);
      setMessage("");
      setExpected("");
      setNote("");
      setPicked("");
    } catch (cause) {
      setError(failureOf(cause, "errors.feedback"));
    } finally {
      setBusy(null);
    }
  };

  const evalCount = cases.filter((item) => item.source === "evaluation").length;
  const chatCount = cases.length - evalCount;

  return (
    <main className="pf-eval">
      <header className="pf-eval__head">
        <div className="pf-stack" style={{ gap: 6 }}>
          <Text size="lg" weight="semibold">{t("cases.title")}</Text>
          <Text size="sm" tone="secondary">{t("cases.body")}</Text>
        </div>
        <div className="pf-row">
          <Button variant="secondary" disabled={busy !== null || cases.length === 0} onClick={() => void check()}>
            {busy === "check" ? t("cases.checking") : t("cases.check")}
          </Button>
          <Button variant="primary" disabled={busy !== null || cases.length === 0} onClick={() => void scan()}>
            {busy === "scan" ? t("cases.scanning") : t("cases.scan")}
          </Button>
        </div>
      </header>
      {error ? <p className="pf-banner" role="alert">{describeFailure(error, t)}</p> : null}
      {error ? <WriterDiagnosis text={describeFailure(error, t)} /> : null}
      {notice ? <Text size="sm">{notice}</Text> : null}
      {score ? <Text size="sm" weight="semibold">{t("cases.score", { passed: score.passed, exam: score.exam })}</Text> : null}
      <section className="pf-cases__note">
        <Text size="sm" weight="semibold">{t("cases.compareTitle")}</Text>
        <Text size="sm" tone="secondary">{t("cases.compare")}</Text>
      </section>
      <section className="pf-stack" style={{ gap: 8 }}>
        <Text size="md" weight="semibold">{t("cases.suggestions")}</Text>
        <Text size="xs" tone="tertiary">{t("cases.changeKind")}</Text>
        {suggestions.length === 0 ? <Text size="sm" tone="tertiary">{t("cases.noSuggestion")}</Text> : null}
        {suggestions.map((item) => {
          const setting = item.kind === "setting";
          const file = SKILL_FILE[item.skill] ?? item.skill;
          const builtIn = item.status === "restored" || Boolean(restored[item.skill]);
          const settingName = SETTING_NAME[item.skill] ?? item.skill;
          return (
            <article className="pf-card pf-cases__card" key={item.id}>
              <div className="pf-cases__row">
                <div className="pf-stack" style={{ gap: 4 }}>
                  <div className="pf-row" style={{ flexWrap: "wrap" }}>
                    <Badge tone="brand">
                      {setting
                        ? t("cases.setting", { name: settingName, value: item.setting_value || "" })
                        : t("cases.replaces", { name: item.skill })}
                    </Badge>
                    {item.status === "accepted" && !builtIn ? <Badge tone="success" dot>{t("cases.accepted")}</Badge> : null}
                    {builtIn ? <Badge tone="neutral" dot>{t("cases.restored")}</Badge> : null}
                  </div>
                  <Text size="sm">{item.summary}</Text>
                  {setting ? null : <Text size="xs" tone="tertiary">{t("cases.file", { file })}</Text>}
                </div>
                <div className="pf-cases__actions">
                  {item.status === "draft" ? (
                    <>
                      <Button variant="primary" disabled={busy !== null} onClick={() => void accept(item)}>{t("cases.accept")}</Button>
                      <Button variant="ghost" disabled={busy !== null} onClick={() => void dismiss(item.id)}>{t("cases.dismiss")}</Button>
                    </>
                  ) : null}
                  {item.status === "accepted" && !builtIn ? (
                    <Button variant="ghost" disabled={busy !== null} onClick={() => void restore(item)}>{t("cases.restore")}</Button>
                  ) : null}
                  {builtIn ? (
                    <>
                      {item.status === "restored" ? (
                        <Button variant="ghost" disabled={busy !== null} onClick={() => void dismiss(item.id)}>{t("cases.dismiss")}</Button>
                      ) : null}
                      <Button variant="primary" disabled={busy !== null || cases.length === 0} onClick={() => void scan()}>{t("cases.again")}</Button>
                    </>
                  ) : null}
                </div>
              </div>
              <details className="pf-cases__fold">
                <summary>{t("cases.showChange")}</summary>
                <div className="pf-stack" style={{ gap: 8, marginTop: 8 }}>
                  <div className="pf-cases__why">
                    <Text size="xs" weight="semibold">{t("cases.why")}</Text>
                    <Text size="sm" tone="secondary">{item.rationale}</Text>
                  </div>
                  {item.status === "draft" ? (
                    setting ? (
                      <input
                        className="pf-input"
                        inputMode="decimal"
                        aria-label={t("cases.replacement")}
                        value={drafts[item.id] ?? item.setting_value ?? ""}
                        onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: event.target.value }))}
                      />
                    ) : (
                      <textarea
                        className="pf-input pf-cases__body"
                        rows={8}
                        spellCheck={false}
                        aria-label={t("cases.replacement")}
                        value={drafts[item.id] ?? item.body}
                        onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: event.target.value }))}
                      />
                    )
                  ) : null}
                  {item.status === "draft" ? <Text size="xs" tone="tertiary">{t("cases.note")}</Text> : null}
                </div>
              </details>
            </article>
          );
        })}
      </section>
      <section className="pf-stack" style={{ gap: 10 }}>
          <div className="pf-row">
            <Text size="md" weight="semibold">{t("cases.title")}</Text>
            <Badge tone="neutral">{evalCount} {t("cases.eval")}</Badge>
            <Badge tone="neutral">{chatCount} {t("cases.session")}</Badge>
          </div>
          {cases.length === 0 ? <Text size="sm" tone="tertiary">{t("cases.empty")}</Text> : null}
          <article className="pf-card pf-cases__card">
            <Text size="sm" weight="semibold">{t("cases.add")}</Text>
            <label className="pf-stack" style={{ gap: 4 }}>
              <Text size="xs">{t("cases.fromChat")}</Text>
              <select
                className="pf-input"
                value={picked}
                onChange={(event) => {
                  const id = event.target.value;
                  setPicked(id);
                  const turn = turns.find((item) => item.id === id);
                  if (turn) {
                    setMessage(turn.input);
                    setNote(turn.reply);
                  }
                }}
              >
                <option value="">{t("cases.fromChat")}</option>
                {turns.map((turn) => (
                  <option key={turn.id} value={turn.id}>
                    {(turn.session_state === "live" ? t("cases.live") : t("cases.completed")) + " · " + turn.input.slice(0, 80)}
                  </option>
                ))}
              </select>
            </label>
            <label className="pf-stack" style={{ gap: 4 }}>
              <Text size="xs">{t("cases.message")}</Text>
              <textarea className="pf-input" rows={3} value={message} onChange={(event) => setMessage(event.target.value)} />
            </label>
            <label className="pf-stack" style={{ gap: 4 }}>
              <Text size="xs">{t("cases.should")}</Text>
              <textarea className="pf-input" rows={2} value={expected} onChange={(event) => setExpected(event.target.value)} />
            </label>
            <Button variant="secondary" disabled={busy !== null || !(picked || message.trim())} onClick={() => void saveCase()}>{t("cases.saveCase")}</Button>
          </article>
          {cases.map((item) => (
            <article className="pf-card" key={`${item.source}-${item.case_id}`} style={{ padding: 12 }}>
              <div className="pf-row" style={{ flexWrap: "wrap" }}>
                <Badge tone={item.source === "evaluation" ? "warning" : "neutral"} dot>
                  {item.source === "session" ? t("cases.session") : item.source === "operator" ? t("cases.demo") : t("cases.eval")}
                </Badge>
                {item.session_state === "live" ? <Badge>{t("cases.live")}</Badge> : null}
                {item.session_state === "completed" ? <Badge>{t("cases.completed")}</Badge> : null}
                <Badge tone="danger">{t(KIND[item.kind])}</Badge>
                {item.check_status === "pass" ? <Badge tone="success" dot>{t("cases.nowPass")}</Badge> : null}
                {item.check_status === "fail" ? <Badge tone="danger" dot>{t("cases.still")}</Badge> : null}
                {item.check_status === "chat" ? <Badge tone="neutral">{t("cases.chatCheck")}</Badge> : null}
                {item.dataset && item.dataset !== "session" ? <Badge>{item.dataset}</Badge> : null}
              </div>
              <Text size="sm">{item.input || item.case_id}</Text>
              {item.earlier ? (
                <Text size="xs" tone="secondary">{t("cases.earlier")}: {item.earlier}</Text>
              ) : null}
              {item.check_summary ? <Text size="xs">{item.check_summary}</Text> : null}
              {item.output_summary ? <Text size="xs" tone="secondary">{item.output_summary}</Text> : null}
              <SituationReading input={item.input} output={`${item.output_summary} ${item.check_summary}`} />
            </article>
          ))}
        </section>
    </main>
  );
}
