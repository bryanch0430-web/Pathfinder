import { useEffect, useRef, useState } from "react";
import { Button, EmptyState, Text } from "../design-system";
import { useI18n } from "../lib/i18n";
import {
  addTrainingExample,
  deleteTrainingExample,
  getDesk,
  getDeskChat,
  getTrainingExamples,
  getTrainingTurns,
  getDeskPlan,
  holdDeskChat,
  releaseDeskChat,
  runDeskWorkflow,
  saveDeskNote,
  saveDeskPlan,
  sayOnDesk,
  type DeskChat,
  type DeskMessage,
  type DeskPlan,
  type DeskStop,
  type TrainingExample,
  type TrainingTurn,
} from "../lib/api";

const PATHS = ["plan", "modify", "ask", "unclear"] as const;

function isPath(value: string): value is (typeof PATHS)[number] {
  return (PATHS as readonly string[]).includes(value);
}

function heatBand(score: number): "calm" | "uneasy" | "hot" {
  if (score >= 60) return "hot";
  if (score >= 25) return "uneasy";
  return "calm";
}

/** Retell-style finetune examples: what someone said, and the path the router should take next time. */
export function TrainingPage() {
  const { t } = useI18n();
  const [examples, setExamples] = useState<TrainingExample[]>([]);
  const [turns, setTurns] = useState<TrainingTurn[]>([]);
  const [utterance, setUtterance] = useState("");
  const [intent, setIntent] = useState<(typeof PATHS)[number]>("ask");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    void getTrainingExamples().then((data) => setExamples(data.examples)).catch((cause) => setError(String(cause)));
    void getTrainingTurns().then((data) => setTurns(data.turns)).catch(() => setTurns([]));
  };
  useEffect(load, []);

  const save = async () => {
    if (!utterance.trim()) return;
    setError(null);
    try {
      await addTrainingExample(utterance.trim(), intent, note.trim());
      setUtterance("");
      setNote("");
      load();
    } catch (cause) {
      setError(String(cause));
    }
  };

  const pathName = (path: string) => (isPath(path) ? t(`training.path.${path}`) : path);

  return (
    <main className="pf-page">
      <header className="pf-page__head">
        <h1 className="pf-heading" data-level="2">{t("training.title")}</h1>
        <Text tone="secondary">{t("training.body")}</Text>
      </header>
      {error ? <p role="alert">{error}</p> : null}
      <div className="pf-learn">
        <section className="pf-card pf-learn__card">
          <Text size="sm" weight="semibold">{t("training.recent")}</Text>
          <Text size="xs" tone="tertiary">{t("training.recentBody")}</Text>
          {turns.length === 0 ? (
            <EmptyState icon="memory" title={t("training.empty")} body={t("training.emptyBody")} />
          ) : (
            <ul className="pf-learn__list">
              {turns.map((turn) => (
                <li key={turn.id}>
                  <button
                    type="button"
                    className="pf-learn__turn"
                    onClick={() => {
                      setUtterance(turn.input);
                      if (isPath(turn.path)) setIntent(turn.path);
                    }}
                  >
                    <span className="pf-learn__pair">
                      <span className="pf-learn__io">
                        <span className="pf-learn__k">{t("training.input")}</span>
                        <span>{turn.input}</span>
                      </span>
                      <span className="pf-learn__io">
                        <span className="pf-learn__k">{t("training.pathTaken")}</span>
                        <span>{turn.path ? pathName(turn.path) : t("training.noReply")}</span>
                      </span>
                      <span className="pf-learn__io">
                        <span className="pf-learn__k">{t("training.output")}</span>
                        <span>{turn.output || t("training.noReply")}</span>
                      </span>
                      {turn.why ? <span className="pf-learn__why">{turn.why}</span> : null}
                    </span>
                    <span className="pf-learn__heat" data-band={heatBand(turn.frustration)}>{turn.frustration}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="pf-card pf-learn__card">
          <Text size="sm" weight="semibold">{t("training.add")}</Text>
          <label className="pf-learn__field">
            <span>{t("training.input")}</span>
            <textarea value={utterance} onChange={(event) => setUtterance(event.target.value)} rows={4} />
          </label>
          <label className="pf-learn__field">
            <span>{t("training.path")}</span>
            <select value={intent} onChange={(event) => setIntent(event.target.value as (typeof PATHS)[number])}>
              {PATHS.map((path) => <option key={path} value={path}>{t(`training.path.${path}`)}</option>)}
            </select>
          </label>
          <label className="pf-learn__field">
            <span>{t("training.note")}</span>
            <input value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <Button variant="primary" onClick={() => void save()}>{t("training.save")}</Button>
          {examples.length === 0 ? (
            <Text size="xs" tone="tertiary">{t("training.examplesEmpty")}</Text>
          ) : (
            <ul className="pf-learn__list">
              {examples.map((example) => (
                <li key={example.id} className="pf-learn__example">
                  <div>
                    <strong>{pathName(example.intent)}</strong>
                    <p>{example.utterance}</p>
                    {example.note ? <Text size="xs" tone="tertiary">{example.note}</Text> : null}
                  </div>
                  <button type="button" className="pf-learn__remove" onClick={() => void deleteTrainingExample(example.id).then(load)}>
                    {t("training.remove")}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}

function waitingOn(chat: DeskChat): boolean {
  return chat.last_role === "user" && !chat.held;
}

function tripWhen(chat: DeskChat): string {
  if (!chat.start) return "";
  return chat.end && chat.end !== chat.start ? `${chat.start} – ${chat.end}` : chat.start;
}

/** A contact desk, separate from the planner: a queue, the conversation, and the trip beside it. */
export function DeskPage({ onBack }: { onBack: () => void }) {
  const { t } = useI18n();
  const [chats, setChats] = useState<DeskChat[]>([]);
  const [query, setQuery] = useState("");
  const [ready, setReady] = useState(() => {
    try {
      return localStorage.getItem("pathfinder.desk-ready") !== "away";
    } catch {
      return true;
    }
  });
  const [active, setActive] = useState<string | null>(null);
  const [held, setHeld] = useState(false);
  const [messages, setMessages] = useState<DeskMessage[]>([]);
  const [caseNote, setCaseNote] = useState("");
  const [draft, setDraft] = useState("");
  const [instruction, setInstruction] = useState("");
  const [running, setRunning] = useState(false);
  const [plan, setPlan] = useState<DeskPlan | null>(null);
  const [stops, setStops] = useState<DeskStop[]>([]);
  const [error, setError] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const loadList = () => {
    void getDesk().then((data) => setChats(data.chats)).catch((cause) => setError(String(cause)));
  };
  const loadChat = (id: string, withNote = false) => {
    void getDeskChat(id).then((data) => {
      setMessages(data.messages);
      setHeld(data.held);
      if (withNote) setCaseNote(data.note ?? "");
    }).catch((cause) => setError(String(cause)));
  };
  useEffect(() => {
    loadList();
    const timer = window.setInterval(loadList, 5000);
    return () => window.clearInterval(timer);
  }, []);
  const loadPlan = (id: string) => {
    void getDeskPlan(id).then((data) => {
      setPlan(data.plan);
      setStops(data.plan?.days.flatMap((day) => day.items.map((item) => ({ ...item }))) ?? []);
    }).catch((cause) => setError(String(cause)));
  };
  useEffect(() => {
    if (!active) return;
    loadChat(active, true);
    loadPlan(active);
    const timer = window.setInterval(() => loadChat(active), 4000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    const node = threadRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, active]);

  const current = chats.find((chat) => chat.session_id === active);
  const needle = query.trim().toLowerCase();
  const matches = (chat: DeskChat) =>
    !needle
    || chat.session_id.toLowerCase().includes(needle)
    || (chat.destination || "").toLowerCase().includes(needle)
    || (chat.preview || "").toLowerCase().includes(needle);
  const waiting = chats.filter((chat) => !chat.held && matches(chat));
  const mine = chats.filter((chat) => chat.held && matches(chat));
  const counts = {
    open: chats.length,
    hot: chats.filter((chat) => chat.band !== "calm").length,
    held: chats.filter((chat) => chat.held).length,
    waiting: chats.filter(waitingOn).length,
  };
  const setPresence = (next: boolean) => {
    setReady(next);
    try {
      localStorage.setItem("pathfinder.desk-ready", next ? "ready" : "away");
    } catch {
      // The choice still applies for this visit.
    }
  };

  const kindText = (kind: DeskChat["frustration_kind"]) => {
    if (kind === "session") return t("desk.kind.session");
    if (kind === "none") return t("desk.kind.none");
    return t("desk.kind.line");
  };
  const labelText = (label: string | undefined) => {
    if (label === "positive") return t("desk.label.positive");
    if (label === "negative") return t("desk.label.negative");
    if (label === "neutral") return t("desk.label.neutral");
    return t("desk.label.not_measured");
  };
  const noteText = (note: string) => {
    if (note === "unmeasured") return t("desk.note.unmeasured");
    if (note === "negative") return t("desk.note.negative");
    if (note === "positive") return t("desk.note.positive");
    if (note === "negation") return t("desk.note.negation");
    if (note === "intensifier") return t("desk.note.intensifier");
    if (note === "repeat") return t("desk.note.repeat");
    if (note === "loud") return t("desk.note.loud");
    return t("desk.note.plain");
  };

  const runWorkflow = (text: string) => {
    if (!active || !held || !text.trim() || running) return;
    setRunning(true);
    setError(null);
    void runDeskWorkflow(active, text.trim())
      .then(() => {
        setInstruction("");
        loadChat(active);
        loadPlan(active);
        loadList();
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setRunning(false));
  };
  const saveStops = () => {
    if (!active || !plan || running) return;
    const changed = stops.filter((stop) => {
      const original = plan.days.flatMap((day) => day.items).find((item) => item.id === stop.id);
      if (!original) return false;
      return stop.name !== original.name || stop.start !== original.start || stop.end !== original.end;
    });
    if (!changed.length) return;
    setRunning(true);
    setError(null);
    void saveDeskPlan(
      active,
      plan.version,
      changed.map((stop) => ({ id: stop.id, name: stop.name, start: stop.start, end: stop.end, remove: false })),
    )
      .then(() => { loadPlan(active); loadChat(active); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setRunning(false));
  };
  const removeStop = (id: string) => {
    if (!active || !plan || running) return;
    setRunning(true);
    setError(null);
    void saveDeskPlan(active, plan.version, [{ id, name: "", start: "", end: "", remove: true }])
      .then(() => { loadPlan(active); loadChat(active); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setRunning(false));
  };

  const take = () => {
    if (!active) return;
    if (!held && !ready) return;
    const run = held ? releaseDeskChat : holdDeskChat;
    void run(active).then(() => { loadChat(active); loadList(); });
  };

  const row = (chat: DeskChat) => (
    <li key={chat.session_id}>
      <button type="button" className="pf-desk__chat" data-active={chat.session_id === active} onClick={() => setActive(chat.session_id)}>
        <span className="pf-learn__heat" data-band={chat.frustration_kind === "none" ? "calm" : chat.band}>
          {chat.frustration_kind === "none" ? "–" : chat.frustration}
        </span>
        <span>
          <strong className="pf-center__sid">{chat.session_id}</strong>
          <p>{chat.destination || t("desk.untitled")}</p>
          <p>{chat.preview || t("desk.empty")}</p>
        </span>
      </button>
    </li>
  );

  return (
    <main className="pf-center">
      <header className="pf-center__bar">
        <div>
          <h1 className="pf-heading" data-level="2">{t("desk.title")}</h1>
          <Text size="xs" tone="tertiary">{t("desk.body")}</Text>
        </div>
        <div className="pf-center__stats" aria-label={t("desk.queue")}>
          <span><strong>{counts.open}</strong> {t("desk.stat.open")}</span>
          <span><strong>{counts.waiting}</strong> {t("desk.stat.waiting")}</span>
          <span data-hot={counts.hot > 0 || undefined}><strong>{counts.hot}</strong> {t("desk.stat.hot")}</span>
          <span><strong>{counts.held}</strong> {t("desk.stat.held")}</span>
        </div>
        <div className="pf-center__presence" role="group" aria-label={t("desk.presence")}>
          <button type="button" data-active={ready} onClick={() => setPresence(true)}>{t("desk.ready")}</button>
          <button type="button" data-active={!ready} onClick={() => setPresence(false)}>{t("desk.away")}</button>
        </div>
        <button type="button" className="pf-center__back" onClick={onBack}>{t("desk.back")}</button>
      </header>
      {error ? <p className="pf-center__alert" role="alert">{error}</p> : null}
      <div className="pf-center__body">
        <section className="pf-center__queue">
          <input
            className="pf-center__search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("desk.search")}
            aria-label={t("desk.search")}
          />
          <Text size="xs" tone="tertiary">{t("desk.oneEach")}</Text>
          {chats.length === 0 ? (
            <EmptyState icon="users" title={t("desk.noChats")} body={t("desk.noChatsBody")} />
          ) : (
            <>
              <h2 className="pf-center__group">{t("desk.mineQueue")} <span>{mine.length}</span></h2>
              {mine.length === 0 ? <Text size="xs" tone="tertiary">{t("desk.queueEmpty")}</Text> : <ul className="pf-desk__list">{mine.map(row)}</ul>}
              <h2 className="pf-center__group">{t("desk.queue")} <span>{waiting.length}</span></h2>
              {waiting.length === 0 ? <Text size="xs" tone="tertiary">{t("desk.queueEmpty")}</Text> : <ul className="pf-desk__list">{waiting.map(row)}</ul>}
            </>
          )}
        </section>
        <section className="pf-center__thread">
          {active && current ? (
            <>
              <div className="pf-desk__bar">
                <div>
                  <Text size="sm" weight="semibold">{current.session_id}</Text>
                  <Text size="xs" tone="tertiary">{current.destination || t("desk.untitled")}</Text>
                </div>
                <Button variant={held ? "secondary" : "primary"} disabled={!held && !ready} onClick={take}>{held ? t("desk.release") : t("desk.hold")}</Button>
              </div>
              <div className="pf-desk__messages" ref={threadRef}>
                {messages.length === 0 ? <Text tone="tertiary">{t("desk.empty")}</Text> : null}
                {messages.map((message) => (
                  <p key={message.id} data-role={message.role} data-live={message.live_agent || undefined}>
                    <span className="pf-desk__who">
                      {message.operator || message.live_agent ? t("desk.person") : message.role === "user" ? t("desk.traveller") : t("desk.planner")}
                      {message.role === "user" && message.sentiment_label && message.sentiment_label !== "not_measured" && message.frustration >= 25 ? ` · ${message.frustration}` : ""}
                    </span>
                    {message.content}
                  </p>
                ))}
              </div>
              {held ? (
                <form
                  className="pf-desk__say"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!draft.trim() || !active) return;
                    const text = draft.trim();
                    setDraft("");
                    void sayOnDesk(active, text).then(() => loadChat(active));
                  }}
                >
                  <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={t("desk.placeholder")} />
                  <Button type="submit" variant="primary">{t("desk.send")}</Button>
                </form>
              ) : (
                <p className="pf-center__lock">{ready ? t("desk.replyLocked") : t("desk.notReady")}</p>
              )}
            </>
          ) : (
            <EmptyState icon="users" title={t("desk.pick")} body={t("desk.pickBody")} />
          )}
        </section>
        <aside className="pf-center__contact">
          <Text size="sm" weight="semibold">{t("desk.contact")}</Text>
          {current ? (
            <dl>
              <div>
                <dt>{t("desk.session")}</dt>
                <dd className="pf-center__sid">{current.session_id}</dd>
              </div>
              <div>
                <dt>{current.signed_in ? t("desk.account") : t("desk.guest")}</dt>
                <dd>{current.signed_in ? (current.name || current.email || t("desk.account")) : t("desk.guest")}</dd>
              </div>
              {current.signed_in && current.email ? (
                <div>
                  <dt>{t("auth.email")}</dt>
                  <dd>{current.email}</dd>
                </div>
              ) : null}
              <div>
                <dt>{t("desk.preferences")}</dt>
                <dd>{(current.preferences ?? []).length ? (current.preferences ?? []).join(", ") : t("desk.noPreferences")}</dd>
              </div>
              <div>
                <dt>{t("desk.trip")}</dt>
                <dd>{current.destination || t("desk.none")}</dd>
              </div>
              <div>
                <dt>{t("desk.from")}</dt>
                <dd>{current.origin || t("desk.none")}</dd>
              </div>
              <div>
                <dt>{t("desk.dates")}</dt>
                <dd>{tripWhen(current) || t("desk.none")}</dd>
              </div>
              <div>
                <dt>{t("workspace.party")}</dt>
                <dd>{current.party ? t(current.party === 1 ? "trip.travellersOne" : "trip.travellers", { count: current.party }) : t("desk.none")}</dd>
              </div>
              <div>
                <dt>{t("desk.budget")}</dt>
                <dd>{current.budget != null ? `${current.currency} ${current.budget}` : t("desk.none")}</dd>
              </div>
              <div>
                <dt>{t("desk.stat.hot")}</dt>
                <dd>
                  {current.frustration_kind === "none" ? t("desk.kind.none") : (
                    <>
                      <span className="pf-learn__heat" data-band={current.band}>{current.frustration}</span>
                      {kindText(current.frustration_kind)}
                    </>
                  )}
                </dd>
              </div>
              <div>
                <dt>{labelText(current.sentiment_label)}</dt>
                <dd>{current.sentiment_score == null ? t("desk.kind.none") : current.sentiment_score}</dd>
              </div>
              {(current.sentiment_notes ?? []).map((note) => (
                <div key={note}>
                  <dd>{noteText(note)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <Text size="sm" tone="tertiary">{t("desk.pick")}</Text>
          )}
          {current && held ? (
            <div className="pf-desk__tools">
              <Text size="sm" weight="semibold">{t("desk.run")}</Text>
              <Text size="xs" tone="tertiary">{t("desk.runHint")}</Text>
              <textarea
                rows={3}
                value={instruction}
                disabled={running}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder={t("desk.placeholder")}
              />
              <div className="pf-row" style={{ gap: 6 }}>
                <Button size="sm" variant="primary" disabled={running || !instruction.trim()} onClick={() => runWorkflow(instruction)}>
                  {running ? t("desk.running") : t("desk.run")}
                </Button>
                <Button size="sm" disabled={running || !current.destination} onClick={() => runWorkflow("Plan this trip.")}>
                  {t("desk.planNow")}
                </Button>
              </div>
              <Text size="sm" weight="semibold">{t("desk.manual")}</Text>
              <Text size="xs" tone="tertiary">{t("desk.manualHint")}</Text>
              {plan && stops.length ? stops.map((stop) => (
                <div className="pf-desk__stop" key={stop.id}>
                  <input
                    value={stop.name}
                    disabled={running}
                    aria-label={stop.name}
                    onChange={(event) => setStops((currentStops) => currentStops.map((item) => item.id === stop.id ? { ...item, name: event.target.value } : item))}
                  />
                  <input
                    value={stop.start}
                    disabled={running}
                    aria-label={stop.start}
                    onChange={(event) => setStops((currentStops) => currentStops.map((item) => item.id === stop.id ? { ...item, start: event.target.value } : item))}
                  />
                  <input
                    value={stop.end}
                    disabled={running}
                    aria-label={stop.end}
                    onChange={(event) => setStops((currentStops) => currentStops.map((item) => item.id === stop.id ? { ...item, end: event.target.value } : item))}
                  />
                  <Button size="sm" disabled={running} onClick={() => removeStop(stop.id)}>{t("desk.remove")}</Button>
                </div>
              )) : <Text size="xs" tone="tertiary">{t("desk.noPlan")}</Text>}
              {plan && stops.length ? (
                <Button size="sm" variant="primary" disabled={running} onClick={saveStops}>{t("desk.savePlan")}</Button>
              ) : null}
            </div>
          ) : null}
          {current ? (
            <label className="pf-center__note">
              <span>{t("desk.caseNote")}</span>
              <textarea
                rows={4}
                value={caseNote}
                onChange={(event) => setCaseNote(event.target.value)}
                onBlur={() => { if (active) void saveDeskNote(active, caseNote); }}
                placeholder={t("desk.caseHint")}
              />
            </label>
          ) : null}
          <div className="pf-center__method">
            <Text size="sm" weight="semibold">{t("desk.methodTitle")}</Text>
            <p>{t("desk.method")}</p>
          </div>
        </aside>
      </div>
    </main>
  );
}
