import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  clearMessages,
  createSession,
  discardSession,
  getPlan,
  getSession,
  getTranslation,
  patchContext,
  postFeedback,
  streamChat,
  type ContextPatch,
  type SessionState,
} from "../lib/api";
import type {
  AgentState,
  ChatMessage,
  ClarifyPayload,
  PlanDiffEntry,
  PlanItem,
  PlanLocale,
  PlanTranslation,
  RoutePayload,
  RunTrace,
  TripContext,
  TripPlan,
} from "../design-system/types";
import { describeFailure, failureOf, unreachableOf, type Failure } from "./failure";
import { useI18n } from "./i18n";
import { isPlanLocale, localizePlan, planLanguageNotice, type PlanLanguageNotice } from "./planLocale";
import { clearStoredValue } from "./storage";

const SESSION_KEY = "pathfinder.session";
const TOKEN_KEY = "pathfinder.client";
const AUTH_KEY = "pathfinder.auth";
const GUEST_KEY = "pathfinder.guest";

/** The profile token the API resolves with the bearer: a guest UUID, or `user:<id>` once signed in. */
export function clientToken(): string {
  let token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    token = crypto.randomUUID();
    localStorage.setItem(TOKEN_KEY, token);
  }
  return token;
}

const emptyContext = (sessionId = ""): TripContext => ({
  session_id: sessionId,
  destination: "",
  origin: "",
  start_date: null,
  end_date: null,
  days: null,
  party_size: 2,
  budget: null,
  currency: "HKD",
  hotel_style: "",
  pace: "balanced",
  preferences: [],
  hard_constraints: [],
  language: "en",
});

/**
 * What opening a trip came to. `missing`: the server has no such trip for this identity, and the
 * traveller is signed in, so it is gone. `needs-sign-in`: the same answer while signed out, when the
 * trip may belong to an account. `unavailable`: the server could not be reached. `superseded`: the
 * traveller asked for something else before the answer came, so nothing was shown.
 */
export type OpenOutcome = "opened" | "missing" | "needs-sign-in" | "unavailable" | "superseded";

export interface PathfinderHook {
  ready: boolean;
  sessionId: string | null;
  context: TripContext;
  /** The plan as shown: trusted display names, and the translation laid over it when the plan locale is not English. */
  plan: TripPlan | null;
  /** The English plan as saved; the source of truth for every number, time, id and URL. */
  englishPlan: TripPlan | null;
  /** The language the plan is shown in (session `plan_locale`). */
  planLocale: PlanLocale;
  /** Sets the plan locale and saves it on the session. */
  setPlanLocale: (locale: PlanLocale) => Promise<void>;
  translation: PlanTranslation | null;
  /** Why the English plan is shown although the plan locale is not English. */
  languageNotice: PlanLanguageNotice;
  versions: SessionState["versions"];
  messages: ChatMessage[];
  busy: boolean;
  agents: Record<string, AgentState>;
  progress: number;
  progressMessage: string;
  clarify: ClarifyPayload | null;
  clarifyValues: Record<string, string>;
  trace: RunTrace | null;
  /** Developer mode: each tool and model step as it finishes. */
  live: { id: string; title: string; body: string }[];
  route: RoutePayload | null;
  error: string | null;
  selectedItem: PlanItem | null;
  activeVersion: number;
  feedbackStars: number | null;
  feedbackSaved: boolean;
  feedbackAdded: string[];
  feedbackVerdict: "useful" | "not_useful" | null;
  changedIds: string[];
  hotelChanged: boolean;
  setContext: (patch: Partial<TripContext>) => void;
  saveContext: () => Promise<void>;
  generate: () => Promise<void>;
  send: (message: string) => Promise<void>;
  setClarifyValue: (slot: string, value: string) => void;
  submitClarify: (message?: string) => Promise<void>;
  selectItem: (item: PlanItem | null) => void;
  selectVersion: (version: number) => Promise<void>;
  feedback: (useful: boolean, starsOverride?: number | null) => Promise<void>;
  setStars: (stars: number) => void;
  retry: () => Promise<void>;
  reload: () => Promise<void>;
  clearChat: () => Promise<void>;
  /** Deletes the open trip and clears the page of it; false, with the trip left as it was, when the server refused. */
  scrapTrip: () => Promise<boolean>;
  /** Stops the reply being streamed, if there is one. */
  abort: () => void;
  openSession: (id: string) => Promise<OpenOutcome>;
  /** Makes a trip and opens it. Its id, or null (the traveller is told) when none could be made. */
  newPlan: () => Promise<string | null>;
}

export function usePathfinder(locale: string): PathfinderHook {
  // Read through a ref so a locale switch does not recreate the callbacks
  // (and with them re-run the bootstrap effect).
  const { t, locale: uiLocale, setLocale: setUiLocale } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const uiLocaleRef = useRef(uiLocale);
  uiLocaleRef.current = uiLocale;
  const [planLocale, setPlanLocaleState] = useState<PlanLocale>("en");
  const [translation, setTranslation] = useState<PlanTranslation | null>(null);
  const [translationLoading, setTranslationLoading] = useState(false);
  const [translationFailed, setTranslationFailed] = useState(false);
  // Bumped (with the cache cleared) to fetch the translation again.
  const [translationNonce, setTranslationNonce] = useState(0);
  const translationCache = useRef(new Map<string, PlanTranslation>());
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [context, setContextState] = useState<TripContext>(emptyContext());
  const [plan, setPlan] = useState<TripPlan | null>(null);
  const [versions, setVersions] = useState<SessionState["versions"]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [agents, setAgents] = useState<Record<string, AgentState>>({});
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const [clarify, setClarify] = useState<ClarifyPayload | null>(null);
  const [clarifyValues, setClarifyValues] = useState<Record<string, string>>({});
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [live, setLive] = useState<{ id: string; title: string; body: string }[]>([]);
  const [route, setRoute] = useState<RoutePayload | null>(null);
  // Kept as a message key where the page wrote the message, so a language switch translates it too.
  const [error, setError] = useState<Failure | null>(null);
  const [selectedItem, setSelectedItem] = useState<PlanItem | null>(null);
  const [activeVersion, setActiveVersion] = useState(1);
  const [feedbackStars, setFeedbackStars] = useState<number | null>(null);
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  const [feedbackAdded, setFeedbackAdded] = useState<string[]>([]);
  const [feedbackVerdict, setFeedbackVerdict] = useState<"useful" | "not_useful" | null>(null);
  const [changedIds, setChangedIds] = useState<string[]>([]);
  const [hotelChanged, setHotelChanged] = useState(false);
  const [ready, setReady] = useState(false);
  const lastMessage = useRef("");
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);
  // Every open, new trip and reload takes the next number, and only the latest is shown when its answer
  // arrives: the traveller's last click wins, whatever order the answers come back in.
  const navRef = useRef(0);
  const creatingRef = useRef<Promise<string | null> | null>(null);
  const creatingTicket = useRef(0);
  // What the Retry button does for an error that is not a failed turn: ask for the trip again.
  const retryRef = useRef<(() => Promise<unknown>) | null>(null);

  /**
   * Follow a language the traveller asked for in chat: zh-Hant shows the interface in 繁; English leaves 繁 for
   * EN, and EN/简 as they are. Opening a trip only sets the language its plan is shown in, not the interface.
   */
  const adoptPlanLocale = useCallback(
    (value: unknown) => {
      if (!isPlanLocale(value)) return;
      setPlanLocaleState(value);
      if (value === "zh-Hant") setUiLocale("zh-Hant");
      else if (uiLocaleRef.current === "zh-Hant") setUiLocale("en");
    },
    [setUiLocale],
  );

  /** A new session starts in English; carry the 繁 choice over to it. Returns the context to show. */
  const carryPlanLocale = useCallback(async (id: string, context: TripContext): Promise<TripContext> => {
    if (uiLocaleRef.current !== "zh-Hant" || context.plan_locale === "zh-Hant") return context;
    try {
      return (await patchContext(id, { plan_locale: "zh-Hant" })).context;
    } catch {
      // The view still switches; the session keeps its stored locale until the next save.
      return { ...context, plan_locale: "zh-Hant" };
    }
  }, []);

  /**
   * Forgets what the page shows of the trip being left besides its plan and messages: a failed turn's
   * banner and the text Retry would send again, a clarifying question, the last turn's trace and steps.
   */
  const forgetTripView = useCallback(() => {
    lastMessage.current = "";
    retryRef.current = null;
    setError(null);
    setClarify(null);
    setClarifyValues({});
    setTrace(null);
    setRoute(null);
    setLive([]);
    setSelectedItem(null);
    setFeedbackStars(null);
  }, []);

  /** Shows a trip in place of the one shown: its plan, messages and context, and the pointer a reload starts from. */
  const showTrip = useCallback((id: string, trip: Pick<SessionState, "context" | "plan" | "versions" | "messages">) => {
    if (sessionRef.current !== id) {
      // The reply being written for the trip being left stops with it, so its late events cannot land in this one.
      abortRef.current?.abort();
      forgetTripView();
    }
    setContextState(trip.context);
    if (isPlanLocale(trip.context.plan_locale)) setPlanLocaleState(trip.context.plan_locale);
    if (trip.plan) {
      setPlan(trip.plan);
      setActiveVersion(trip.plan.version);
    } else {
      setPlan(null);
      setActiveVersion(1);
    }
    setChangedIds([]);
    setHotelChanged(false);
    setVersions(trip.versions);
    setMessages(trip.messages);
    localStorage.setItem(SESSION_KEY, id);
    sessionRef.current = id;
    setSessionId(id);
  }, [forgetTripView]);

  const bootstrap = useCallback(async () => {
    const ticket = ++navRef.current;
    retryRef.current = null;
    setError(null);
    try {
      const id = localStorage.getItem(SESSION_KEY);
      if (id) {
        try {
          const data = await getSession(id);
          if (ticket !== navRef.current) return;
          showTrip(id, data);
          setReady(true);
          return;
        } catch (cause) {
          if (ticket !== navRef.current) return;
          if (cause instanceof ApiError && (cause.status === 404 || cause.status === 403)) {
            // The server says this trip is gone or is not this browser's: forget it.
            clearStoredValue("session");
          } else {
            // The server is down or slow, not forgetting the trip: keep the id and say so.
            setError(unreachableOf(cause));
            setReady(true);
            return;
          }
        }
      }
      const created = await createSession(clientToken());
      const context = await carryPlanLocale(created.session.id, created.context);
      if (ticket !== navRef.current) return;
      showTrip(created.session.id, { context, plan: null, versions: [], messages: [] });
      setReady(true);
    } catch (cause) {
      if (ticket !== navRef.current) return;
      setError(failureOf(cause, "errors.backend"));
      setReady(true);
    }
  }, [carryPlanLocale, showTrip]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    setFeedbackSaved(false);
    setFeedbackAdded([]);
    setFeedbackVerdict(null);
  }, [plan?.trip_id, plan?.version]);

  const openSession = useCallback(async (id: string): Promise<OpenOutcome> => {
    const ticket = ++navRef.current;
    if (id === sessionRef.current) {
      // Already open: nothing to load, and nothing the traveller asked for before is wanted any more.
      setReady(true);
      return "opened";
    }
    setError(null);
    setReady(false);
    try {
      const data = await getSession(id);
      if (ticket !== navRef.current) return "superseded";
      showTrip(id, data);
      setReady(true);
      return "opened";
    } catch (cause) {
      // Whatever came of this answer, the page belongs to the latest ask: it is told nothing about this one.
      const latest = ticket === navRef.current;
      if (latest) setReady(true);
      if (cause instanceof ApiError && (cause.status === 404 || cause.status === 403)) {
        // The server answers 404 both for a trip that is gone and for one that is not this
        // identity's. Signed out in a browser that has signed in before, it may be an account's
        // trip: say so and keep it. In a browser that never has, nobody else can own it.
        if (!localStorage.getItem(AUTH_KEY) && localStorage.getItem(GUEST_KEY)) {
          if (latest) setError("errors.signInToOpen");
          if (latest && sessionRef.current === null) localStorage.setItem(SESSION_KEY, id);
          return "needs-sign-in";
        }
        return "missing";
      }
      // The server is down or slow: the trip is not gone. The trip that is open stays as it is, and
      // Retry asks for this one again. With no trip open, a reload tries it again.
      if (latest) {
        setError(unreachableOf(cause));
        retryRef.current = () => openSession(id);
        if (sessionRef.current === null) localStorage.setItem(SESSION_KEY, id);
      }
      return "unavailable";
    }
  }, [showTrip]);

  const newPlan = useCallback((): Promise<string | null> => {
    const pending = creatingRef.current;
    if (pending) {
      // A second click while the first trip is still being made takes the view but makes no second trip.
      creatingTicket.current = ++navRef.current;
      setReady(false);
      return pending;
    }
    creatingTicket.current = ++navRef.current;
    setReady(false);
    const making = (async () => {
      try {
        const created = await createSession(clientToken());
        const context = await carryPlanLocale(created.session.id, created.context);
        if (creatingTicket.current === navRef.current) {
          showTrip(created.session.id, { context, plan: null, versions: [], messages: [] });
        }
        return created.session.id;
      } catch (cause) {
        // No trip was made: the one that is open stays, and the traveller is told.
        if (creatingTicket.current === navRef.current) {
          setError(unreachableOf(cause));
          retryRef.current = () => newPlan();
        }
        return null;
      } finally {
        creatingRef.current = null;
        if (creatingTicket.current === navRef.current) setReady(true);
      }
    })();
    creatingRef.current = making;
    return making;
  }, [carryPlanLocale, showTrip]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const refresh = useCallback(async () => {
    const id = sessionRef.current;
    if (!id) return;
    try {
      const data = await getSession(id);
      // The answer came after another trip was opened: it is not for the view that is open now.
      if (sessionRef.current !== id) return;
      setMessages(data.messages);
      setVersions(data.versions);
      setContextState(data.context);
      if (isPlanLocale(data.context.plan_locale)) setPlanLocaleState(data.context.plan_locale);
      if (data.plan) {
        setPlan(data.plan);
        setActiveVersion(data.plan.version);
      }
    } catch {
      // The optimistic state stays visible; the next action will retry.
    }
  }, []);

  const setPlanLocale = useCallback(async (value: PlanLocale) => {
    setPlanLocaleState(value);
    const id = sessionRef.current;
    if (!id) return;
    try {
      const data = await patchContext(id, { plan_locale: value });
      setContextState(data.context);
    } catch {
      // The view still switches; the session keeps its stored locale until the next save.
    }
  }, []);

  // The translation of the shown plan version, while the plan locale is not English.
  const planVersion = plan?.version ?? null;
  useEffect(() => {
    const id = sessionId;
    if (!id || planVersion === null || planLocale === "en") {
      setTranslation(null);
      setTranslationLoading(false);
      setTranslationFailed(false);
      return;
    }
    const key = `${id}:${planVersion}:${planLocale}`;
    const cached = translationCache.current.get(key);
    if (cached) {
      setTranslation(cached);
      setTranslationLoading(false);
      setTranslationFailed(false);
      return;
    }
    let cancelled = false;
    setTranslationLoading(true);
    setTranslationFailed(false);
    getTranslation(id, planVersion, planLocale)
      .then((result) => {
        if (cancelled) return;
        if (result.status === "translated") translationCache.current.set(key, result);
        setTranslation(result);
      })
      .catch(() => {
        if (cancelled) return;
        setTranslation(null);
        setTranslationFailed(true);
      })
      .finally(() => {
        if (!cancelled) setTranslationLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, planVersion, planLocale, translationNonce]);

  const displayPlan = useMemo(
    () => (plan ? localizePlan(plan, planLocale, translation) : null),
    [plan, planLocale, translation],
  );
  const languageNotice = planLanguageNotice(planLocale, translation, translationLoading, translationFailed);

  const setContext = useCallback((patch: Partial<TripContext>) => {
    setContextState((current) => ({ ...current, ...patch }));
  }, []);

  const saveContext = useCallback(async () => {
    const id = sessionRef.current;
    if (!id) return;
    try {
      const patch: ContextPatch = {
        destination: context.destination,
        origin: context.origin,
        start_date: context.start_date,
        end_date: context.end_date,
        party_size: context.party_size,
        budget: context.budget,
        currency: context.currency,
        hotel_style: context.hotel_style,
        preferences: context.preferences,
        hard_constraints: context.hard_constraints,
      };
      const data = await patchContext(id, patch);
      setContextState(data.context);
    } catch (cause) {
      setError(failureOf(cause, "errors.saveDetails"));
      throw cause;
    }
  }, [context]);

  const send = useCallback(
    async (message: string) => {
      const id = sessionRef.current;
      if (!id || busy) return;
      lastMessage.current = message;
      retryRef.current = null;
      let failed = false;
      setBusy(true);
      setError(null);
      setChangedIds([]);
      setHotelChanged(false);
      setClarify(null);
      setClarifyValues({});
      setAgents({});
      setProgress(0);
      setProgressMessage(tRef.current("chat.understanding"));
      setRoute(null);
      const scopeLabel = selectedItem
        ? (() => {
            const day = plan?.days.find((entry) =>
              entry.items.some((candidate) => candidate.id === selectedItem.id),
            );
            const index = day && plan ? plan.days.indexOf(day) + 1 : 0;
            // Sent to the planner as `scope_label`, so it stays in English and
            // uses the saved name, not the display name.
            const english =
              day?.items.find((candidate) => candidate.id === selectedItem.id) ??
              plan?.hotels.find((hotel) => hotel.id === selectedItem.id);
            const name = english?.name ?? selectedItem.name;
            return day ? `Day ${index} · ${name}` : name;
          })()
        : "";
      const optimistic: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: message,
        created_at: new Date().toISOString(),
        // Show which stop the message was about, so the transcript keeps the context.
        meta: scopeLabel ? { scope: scopeLabel } : undefined,
      };
      setMessages((current) => [...current, optimistic]);
      setLive([]);
      let streamingId: string | null = null;
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChat(
          {
            session_id: id,
            message,
            locale,
            scope_item_id: selectedItem?.id ?? null,
            scope_label: scopeLabel || null,
            developer: localStorage.getItem("pathfinder.developer") === "1",
          },
          {
            onEvent: (event, data) => {
              // Another trip was opened since this turn began: its events are not for this view.
              if (sessionRef.current !== id) return;
              if (event === "route") setRoute(data as unknown as RoutePayload);
              if (event === "locale" && isPlanLocale(data.plan_locale)) {
                // A set_language turn: follow it in the interface and fetch the translation afresh.
                adoptPlanLocale(data.plan_locale);
                setContextState((current) => ({ ...current, plan_locale: data.plan_locale as PlanLocale }));
                translationCache.current.clear();
                setTranslationNonce((count) => count + 1);
              }
              if (event === "progress") {
                setProgress(Number(data.progress ?? 0));
                setProgressMessage(String(data.message ?? ""));
              }
              if (event === "agent") {
                const name = String(data.agent);
                setAgents((current) => ({
                  ...current,
                  [name]:
                    data.phase === "start"
                      ? { agent: name, status: "running" }
                      : {
                          agent: name,
                          status: String(data.status ?? "ok") as AgentState["status"],
                          elapsed_ms: Number(data.elapsed_ms ?? 0),
                          summary: String(data.summary ?? ""),
                        },
                }));
              }
              if (event === "token") {
                const text = String(data.text ?? "");
                if (!streamingId) {
                  streamingId = `stream-${Date.now()}`;
                  const idForStream = streamingId;
                  setMessages((current) => [
                    ...current,
                    { id: idForStream, role: "assistant", content: text, created_at: new Date().toISOString() },
                  ]);
                } else {
                  const idForStream = streamingId;
                  setMessages((current) =>
                    current.map((item) => (item.id === idForStream ? { ...item, content: item.content + text } : item)),
                  );
                }
              }
              if (event === "plan") {
                setPlan(data.plan as unknown as TripPlan);
                const diff = (data.diff ?? []) as PlanDiffEntry[];
                setChangedIds(
                  diff
                    .filter((entry) => entry.kind === "added" || entry.kind === "changed")
                    .map((entry) => entry.item_id)
                    .filter((id): id is string => Boolean(id)),
                );
                setHotelChanged(diff.some((entry) => entry.kind === "hotel"));
                setProgress(100);
              }
              if (event === "clarify") setClarify(data as unknown as ClarifyPayload);
              if (event === "think" || event === "tool") {
                const title = event === "think"
                  ? `${String(data.purpose ?? "model")} · ${String(data.latency_ms ?? "")} ms`
                  : `${String(data.name ?? "tool")} · ${String(data.status ?? "")} · ${String(data.latency_ms ?? "")} ms`;
                const body = event === "think"
                  ? [data.reasoning, data.output_preview].filter((part) => typeof part === "string" && part).join("\n\n")
                  : [data.note, data.args ? JSON.stringify(data.args) : ""].filter((part) => typeof part === "string" && part).join("\n");
                setLive((current) => [...current, { id: `${event}-${current.length}-${Date.now()}`, title, body }]);
              }
              if (event === "trace") setTrace(data as unknown as RunTrace);
              if (event === "error") {
                failed = true;
                setError(data.message ? { text: String(data.message) } : "errors.turn");
              }
            },
          },
          controller.signal,
        );
      } catch (cause) {
        if (!controller.signal.aborted) {
          failed = true;
          setError(failureOf(cause, "errors.turn"));
        }
      } finally {
        // Only a turn that failed is something Retry sends again.
        if (!failed && sessionRef.current === id) lastMessage.current = "";
        // A newer stream (in the trip opened since) owns the busy state and the controller.
        const superseded = abortRef.current !== null && abortRef.current !== controller;
        if (!superseded) {
          abortRef.current = null;
          setBusy(false);
          setProgress(100);
        }
        // An aborted stream means the view moved on; there is nothing of this trip to reload.
        if (!controller.signal.aborted && sessionRef.current === id) await refresh();
      }
    },
    [adoptPlanLocale, busy, locale, plan, refresh, selectedItem?.id, selectedItem?.name],
  );

  const generate = useCallback(async () => {
    try {
      await saveContext();
    } catch {
      return;
    }
    await send(plan ? "Re-plan with the updated details." : "Plan my trip.");
  }, [plan, saveContext, send]);

  const submitClarify = useCallback(async (message?: string) => {
    if (message && message.trim()) {
      setClarify(null);
      setClarifyValues({});
      await send(message.trim());
      return;
    }
    const entries = Object.entries(clarifyValues).filter(([, value]) => value.trim());
    if (!entries.length) {
      // Nothing to send: an empty "please continue" just re-asks the same
      // question. Leave the card in place and let the traveller pick or type.
      return;
    }
    const sentence = entries.map(([slot, value]) => `${slot.replaceAll("_", " ")}: ${value}`).join("; ");
    await send(`Here are the missing details — ${sentence}.`);
  }, [clarifyValues, send]);

  const selectVersion = useCallback(async (version: number) => {
    const id = sessionRef.current;
    if (!id) return;
    setActiveVersion(version);
    setChangedIds([]);
    setHotelChanged(false);
    try {
      const data = await getPlan(id, version);
      setPlan(data.plan);
    } catch {
      // Keep the current plan.
    }
  }, []);

  const feedback = useCallback(
    async (useful: boolean, starsOverride?: number | null) => {
      const id = sessionRef.current;
      if (!id) return;
      try {
        const rating = starsOverride ?? feedbackStars;
        const result = await postFeedback(id, useful, rating);
        setFeedbackVerdict(useful ? "useful" : "not_useful");
        if (useful) {
          setFeedbackSaved(true);
          setFeedbackAdded(result.preferences_added ?? []);
        }
      } catch (cause) {
        setError(failureOf(cause, "errors.feedback"));
      }
    },
    [feedbackStars],
  );

  const reload = useCallback(async () => {
    setReady(false);
    await bootstrap();
  }, [bootstrap]);

  const clearChat = useCallback(async () => {
    const id = sessionRef.current;
    if (!id) return;
    try {
      await clearMessages(id);
      setMessages([]);
      setClarify(null);
      setClarifyValues({});
      setError(null);
    } catch (cause) {
      setError(failureOf(cause, "errors.clearChat"));
    }
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const scrapTrip = useCallback(async (): Promise<boolean> => {
    const id = sessionRef.current;
    if (!id) return false;
    // The reply being written for this trip stops before the trip is deleted.
    abortRef.current?.abort();
    try {
      await discardSession(id);
    } catch (cause) {
      // Only a 404 means the trip is already gone. Any other answer leaves it where it is, and the page says so.
      if (!(cause instanceof ApiError && cause.status === 404)) {
        setError("errors.scrap");
        await refresh();
        return false;
      }
    }
    forgetTripView();
    clearStoredValue("session");
    sessionRef.current = null;
    setSessionId(null);
    setPlan(null);
    setMessages([]);
    setVersions([]);
    setContextState(emptyContext());
    setChangedIds([]);
    setHotelChanged(false);
    setActiveVersion(1);
    return true;
  }, [forgetTripView, refresh]);

  return {
    ready,
    sessionId,
    context,
    plan: displayPlan,
    englishPlan: plan,
    planLocale,
    setPlanLocale,
    translation,
    languageNotice,
    versions,
    messages,
    busy,
    agents,
    progress,
    progressMessage,
    clarify,
    clarifyValues,
    trace,
    live,
    route,
    error: error === null ? null : describeFailure(error, t),
    selectedItem,
    activeVersion,
    feedbackStars,
    feedbackSaved,
    feedbackAdded,
    feedbackVerdict,
    changedIds,
    hotelChanged,
    setContext,
    saveContext,
    generate,
    send,
    setClarifyValue: (slot, value) => setClarifyValues((current) => ({ ...current, [slot]: value })),
    submitClarify,
    selectItem: setSelectedItem,
    selectVersion,
    feedback,
    setStars: setFeedbackStars,
    retry: async () => {
      if (retryRef.current) await retryRef.current();
      else if (lastMessage.current) await send(lastMessage.current);
      else await reload();
    },
    reload,
    clearChat,
    scrapTrip,
    abort,
    openSession,
    newPlan,
  };
}
