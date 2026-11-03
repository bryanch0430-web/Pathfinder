import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  clearMessages,
  createSession,
  discardSession,
  getPlan,
  getSession,
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
  RoutePayload,
  RunTrace,
  TripContext,
  TripPlan,
} from "../design-system/types";
import { useI18n } from "./i18n";
import { clearStoredValue } from "./storage";

const SESSION_KEY = "pathfinder.session";
const TOKEN_KEY = "pathfinder.client";

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

export interface PathfinderHook {
  ready: boolean;
  sessionId: string | null;
  context: TripContext;
  plan: TripPlan | null;
  versions: SessionState["versions"];
  messages: ChatMessage[];
  busy: boolean;
  agents: Record<string, AgentState>;
  progress: number;
  progressMessage: string;
  clarify: ClarifyPayload | null;
  clarifyValues: Record<string, string>;
  trace: RunTrace | null;
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
  scrapTrip: () => Promise<void>;
  openSession: (id: string) => Promise<boolean>;
  newPlan: () => Promise<string>;
}

export function usePathfinder(locale: string): PathfinderHook {
  // Read through a ref so a locale switch does not recreate the callbacks
  // (and with them re-run the bootstrap effect).
  const { t } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
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
  const [route, setRoute] = useState<RoutePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  const loadSession = useCallback(async (id: string) => {
    const data = await getSession(id);
    setContextState(data.context);
    if (data.plan) {
      setPlan(data.plan);
      setActiveVersion(data.plan.version);
    } else {
      setPlan(null);
      setActiveVersion(1);
    }
    setChangedIds([]);
    setHotelChanged(false);
    setVersions(data.versions);
    setMessages(data.messages);
    sessionRef.current = id;
    setSessionId(id);
  }, []);

  const bootstrap = useCallback(async () => {
    setError(null);
    try {
      let id = localStorage.getItem(SESSION_KEY);
      if (id) {
        try {
          await loadSession(id);
          setReady(true);
          return;
        } catch {
          clearStoredValue("session");
        }
      }
      const created = await createSession(clientToken());
      id = created.session.id;
      localStorage.setItem(SESSION_KEY, id);
      setContextState(created.context);
      sessionRef.current = id;
      setSessionId(id);
      setReady(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tRef.current("errors.backend"));
      setReady(true);
    }
  }, [loadSession]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    setFeedbackSaved(false);
    setFeedbackAdded([]);
    setFeedbackVerdict(null);
  }, [plan?.trip_id, plan?.version]);

  const openSession = useCallback(async (id: string) => {
    setReady(false);
    try {
      await loadSession(id);
      localStorage.setItem(SESSION_KEY, id);
      setReady(true);
      return true;
    } catch {
      setReady(true);
      return false;
    }
  }, [loadSession]);

  const newPlan = useCallback(async () => {
    setReady(false);
    try {
      const created = await createSession(clientToken());
      await loadSession(created.session.id);
      localStorage.setItem(SESSION_KEY, created.session.id);
      setReady(true);
      return created.session.id;
    } finally {
      setReady(true);
    }
  }, [loadSession]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const refresh = useCallback(async () => {
    const id = sessionRef.current;
    if (!id) return;
    try {
      const data = await getSession(id);
      setMessages(data.messages);
      setVersions(data.versions);
      setContextState(data.context);
      if (data.plan) {
        setPlan(data.plan);
        setActiveVersion(data.plan.version);
      }
    } catch {
      // The optimistic state stays visible; the next action will retry.
    }
  }, []);

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
      setError(cause instanceof Error ? cause.message : tRef.current("errors.saveDetails"));
      throw cause;
    }
  }, [context]);

  const send = useCallback(
    async (message: string) => {
      const id = sessionRef.current;
      if (!id || busy) return;
      lastMessage.current = message;
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
            // Sent to the planner as `scope_label`, so it stays in English.
            return day ? `Day ${index} · ${selectedItem.name}` : selectedItem.name;
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
          },
          {
            onEvent: (event, data) => {
              if (event === "route") setRoute(data as unknown as RoutePayload);
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
              if (event === "trace") setTrace(data as unknown as RunTrace);
              if (event === "error") setError(String(data.message ?? tRef.current("errors.turn")));
            },
          },
          controller.signal,
        );
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause instanceof ApiError && cause.status === 429) setError(tRef.current("errors.rateLimited"));
          else setError(cause instanceof Error ? cause.message : tRef.current("errors.turn"));
        }
      } finally {
        abortRef.current = null;
        setBusy(false);
        setProgress(100);
        await refresh();
      }
    },
    [busy, locale, plan, refresh, selectedItem?.id, selectedItem?.name],
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
        setError(cause instanceof Error ? cause.message : tRef.current("errors.feedback"));
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
      setError(cause instanceof Error ? cause.message : tRef.current("errors.clearChat"));
    }
  }, []);

  const scrapTrip = useCallback(async () => {
    const id = sessionRef.current;
    abortRef.current?.abort();
    if (id) {
      try {
        await discardSession(id);
      } catch {
        // Even if the old row is already gone, drop the local pointer.
      }
    }
    clearStoredValue("session");
    sessionRef.current = null;
    setSessionId(null);
    setPlan(null);
    setMessages([]);
    setVersions([]);
    setContextState(emptyContext());
    setClarify(null);
    setClarifyValues({});
    setSelectedItem(null);
    setFeedbackStars(null);
    setChangedIds([]);
    setHotelChanged(false);
    setTrace(null);
    setRoute(null);
    setError(null);
    setActiveVersion(1);
    setReady(false);
    await bootstrap();
  }, [bootstrap]);

  return {
    ready,
    sessionId,
    context,
    plan,
    versions,
    messages,
    busy,
    agents,
    progress,
    progressMessage,
    clarify,
    clarifyValues,
    trace,
    route,
    error,
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
      if (lastMessage.current) await send(lastMessage.current);
      else await reload();
    },
    reload,
    clearChat,
    scrapTrip,
    openSession,
    newPlan,
  };
}
