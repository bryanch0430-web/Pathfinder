import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  clearMessages,
  createSession,
  discardSession,
  getPlan,
  getSession,
  getTranslation,
  me,
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
import { useI18n, type MessageKey } from "./i18n";
import { isPlanLocale, localizePlan, planLanguageNotice, type PlanLanguageNotice } from "./planLocale";
import { forgetSignIn, forgetTrip, readStoredValue, rememberedTrip, rememberTrip, writeStoredValue } from "./storage";
import { writeTrips } from "./tripsStore";

/**
 * A random version 4 UUID. `crypto.randomUUID` exists only in a secure context (https or localhost): a page served over
 * plain http from another address, such as the dev server opened from a phone, builds the same from `getRandomValues`.
 */
function randomUuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** The profile token the API resolves with the bearer: a guest UUID, or `user:<id>` once signed in. */
export function clientToken(): string {
  let token = readStoredValue("client");
  if (!token) {
    token = randomUuid();
    // A browser that will not keep it still has this token for the life of the page.
    writeStoredValue("client", token);
  }
  return token;
}

/**
 * How long the page waits for the server when it opens or makes a trip before it takes the server for down.
 * Longer than the 30 s the server gives its own look-ups when it reopens an outdated plan.
 */
const LOAD_TIMEOUT_MS = 45_000;

/**
 * The server has no such trip for this identity. Signed out in a browser that has signed in before, it may be an
 * account's trip. In a browser that never has, nobody else can own it, so it is gone.
 */
const mayBeAccountTrip = () => !readStoredValue("auth") && Boolean(readStoredValue("guest"));

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
 * What opening a trip came to. `missing`: the server has no such trip for this identity and it cannot
 * be an account's, so it is gone. `needs-sign-in`: the same answer while signed out in a browser that has
 * signed in before, when the trip may belong to an account. `unavailable`: the server could not be reached.
 * `superseded`: the traveller asked for something else before the answer came, so nothing was shown.
 */
export type OpenOutcome = "opened" | "missing" | "needs-sign-in" | "unavailable" | "superseded";

/**
 * What scrapping the open trip came to. `gone`: it is deleted and the page is cleared of it. `kept`: the server
 * refused, and the trip and the page stay as they were. `left`: it is deleted, but the traveller had already
 * opened another trip, which stays as it is.
 */
export type ScrapOutcome = "gone" | "kept" | "left";

/** What a banner is about, so that a later action can tell whether the banner still concerns what the traveller is doing. */
type BannerKind = "open" | "save" | "generate" | "rate" | "clear" | "turn" | "scrap";

/** What Retry does for the banner that is shown: `run` does again what failed. */
interface RetryTarget {
  kind: BannerKind;
  run: () => Promise<unknown>;
}

export interface PathfinderHook {
  ready: boolean;
  /** A trip is being opened or made. The trip that is shown stays on screen until the new one arrives. */
  opening: boolean;
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
  /** The message typed in the box of the open trip and not sent. Each trip keeps its own while the page stays open. */
  draft: string;
  setDraft: (value: string) => void;
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
  /** Something the traveller should know that is not a failure, such as the trip they had open being gone. */
  notice: string | null;
  selectedItem: PlanItem | null;
  activeVersion: number;
  feedbackStars: number | null;
  feedbackSaved: boolean;
  feedbackAdded: string[];
  feedbackVerdict: "useful" | "not_useful" | null;
  changedIds: string[];
  hotelChanged: boolean;
  setContext: (patch: Partial<TripContext>) => void;
  /** Saves the details form on the open trip. False when it is not saved: the traveller is told and Retry saves again. */
  saveContext: () => Promise<boolean>;
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
  /** Deletes the open trip. See `ScrapOutcome`. When the server refuses, Retry does `again`. */
  scrapTrip: (again?: () => Promise<unknown>) => Promise<ScrapOutcome>;
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
  /** Runs a request that is cut off after LOAD_TIMEOUT_MS and then fails like a server that cannot be reached. */
  const withTimeout = useCallback(async <T>(run: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
    try {
      return await run(controller.signal);
    } catch (cause) {
      // The message is for the console; what the page says is chosen by the caller (the reconnect message).
      if (controller.signal.aborted) throw new ApiError("The server did not answer in time.", 0);
      throw cause;
    } finally {
      window.clearTimeout(timer);
    }
  }, []);
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
  // The message typed and not sent, per trip. It is held here and not by the chat panel, which a visit to another page
  // and the loading screen of a reload take away.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draftKey = sessionId ?? "";
  const setDraft = useCallback(
    (value: string) => setDrafts((current) => ({ ...current, [draftKey]: value })),
    [draftKey],
  );
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
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [selectedItem, setSelectedItem] = useState<PlanItem | null>(null);
  const [activeVersion, setActiveVersion] = useState(1);
  const [feedbackStars, setFeedbackStars] = useState<number | null>(null);
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  const [feedbackAdded, setFeedbackAdded] = useState<string[]>([]);
  const [feedbackVerdict, setFeedbackVerdict] = useState<"useful" | "not_useful" | null>(null);
  const [changedIds, setChangedIds] = useState<string[]>([]);
  const [hotelChanged, setHotelChanged] = useState(false);
  const [ready, setReady] = useState(false);
  const [opening, setOpening] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);
  // Every open, new trip and reload takes the next number, and only the latest is shown when its answer
  // arrives: the traveller's last click wins, whatever order the answers come back in.
  const navRef = useRef(0);
  const creatingRef = useRef<Promise<string | null> | null>(null);
  const creatingTicket = useRef(0);
  // What the Retry button does for the banner that is shown, whatever failed: a turn, saving the details, opening a trip.
  // With nothing to do again it asks for the trip again. It is set and cleared with the banner (`showBanner`, `clearBanner`)
  // and with nothing else, or Retry would do what a banner that is gone was about.
  const retryRef = useRef<RetryTarget | null>(null);
  // The latest `send` and `generate`. A target stored when a turn failed calls through here, not the callback of the
  // render in which it was stored: the interface language may have changed since.
  const sendRef = useRef<(message: string) => Promise<void>>(async () => undefined);
  const generateRef = useRef<() => Promise<void>>(async () => undefined);
  // What `send` reads of the trip shown. It is a ref so that a handler made before the trip changed (the trending
  // list makes a trip and then sends its first message) still sends as the trip shown now, not as the one before.
  const shownRef = useRef<{ busy: boolean; plan: TripPlan | null; selectedItem: PlanItem | null }>({
    busy: false,
    plan: null,
    selectedItem: null,
  });
  shownRef.current = { busy, plan, selectedItem };
  // The details form as it is now, for a save made from a handler that was created before the last keystroke.
  const contextRef = useRef(context);
  contextRef.current = context;

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

  /**
   * A new session starts in English; carry the 繁 choice over to it. A language chosen while no trip was shown is
   * passed as `wanted` for the trip that arrives. Returns the context to show.
   */
  const carryPlanLocale = useCallback(
    async (
      id: string,
      context: TripContext,
      wanted: PlanLocale | null = uiLocaleRef.current === "zh-Hant" ? "zh-Hant" : null,
    ): Promise<TripContext> => {
      if (!wanted || context.plan_locale === wanted) return context;
      try {
        return (await withTimeout((signal) => patchContext(id, { plan_locale: wanted }, signal))).context;
      } catch {
        // The view still switches; the session keeps its stored locale until the next save.
        return { ...context, plan_locale: wanted };
      }
    },
    [withTimeout],
  );
  // The plan language the traveller chose while no trip was shown (the page was still opening): it is theirs, not the
  // trip's own, once the trip arrives.
  const chosenWhileOpening = useRef<PlanLocale | null>(null);

  /**
   * Shows a banner. Retry does `target`; with none it asks for the trip again. What Retry did for the banner before
   * goes with it, so that a banner never has the Retry of another.
   */
  const showBanner = useCallback((failure: Failure, target: RetryTarget | null = null) => {
    retryRef.current = target;
    setError(failure);
  }, []);

  /** Hides the banner and its Retry together. */
  const clearBanner = useCallback(() => {
    retryRef.current = null;
    setError(null);
  }, []);

  /**
   * Forgets what the page shows of the trip being left besides its plan and messages: a failed turn's
   * banner and its Retry, a clarifying question, the last turn's trace and steps.
   */
  const forgetTripView = useCallback(() => {
    shownRef.current = { busy: false, plan: null, selectedItem: null };
    clearBanner();
    setNotice(null);
    setClarify(null);
    setClarifyValues({});
    setTrace(null);
    setRoute(null);
    setLive([]);
    setSelectedItem(null);
    setFeedbackStars(null);
  }, [clearBanner]);

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
    rememberTrip(id);
    sessionRef.current = id;
    chosenWhileOpening.current = null;
    setSessionId(id);
  }, [forgetTripView]);

  /** Whether the stored sign-in has ended: another check dropped it already, or the server refuses it now (and it is dropped here). */
  const signInEnded = useCallback(async (): Promise<boolean> => {
    if (!readStoredValue("auth")) return true;
    try {
      await withTimeout((signal) => me(signal));
      return false;
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 401)) return false;
      forgetSignIn();
      return true;
    }
  }, [withTimeout]);

  /**
   * Whether a refusal was the stored sign-in having ended, which is dropped here if so (and the header with it). With a
   * sign-in stored the server refuses an account's trip as 404, and a profile that needs its bearer as 401: neither
   * says that the sign-in ended, so the server is asked.
   */
  const refusedForSignIn = useCallback(
    async (cause: unknown): Promise<boolean> => {
      if (!(cause instanceof ApiError) || ![401, 403, 404].includes(cause.status)) return false;
      return Boolean(readStoredValue("auth")) && (await signInEnded());
    },
    [signInEnded],
  );

  /** `failureOf` for a call made on the open trip, in the page's own words when a sign-in is why the trip is refused. */
  const failureOfCall = useCallback(
    async (cause: unknown, fallback: MessageKey): Promise<Failure> => {
      if (await refusedForSignIn(cause)) return "errors.signInEnded";
      if (cause instanceof ApiError && (cause.status === 404 || cause.status === 403) && mayBeAccountTrip()) {
        return "errors.signInToOpen";
      }
      return failureOf(cause, fallback);
    },
    [refusedForSignIn],
  );

  const bootstrap = useCallback(async () => {
    const ticket = ++navRef.current;
    clearBanner();
    setOpening(false);
    try {
      // This window's own trip when it has one: the stored pointer is shared with every window and names the trip that
      // was opened last, in whichever window.
      const id = sessionRef.current ?? rememberedTrip();
      const signedIn = Boolean(readStoredValue("auth"));
      let lost: string | null = null;
      // The stored sign-in had ended and was dropped here: the page says so once the trip is on screen.
      let signInLost = false;
      for (let attempt = 0; id && attempt < 2; attempt += 1) {
        try {
          const loaded = await withTimeout((signal) => getSession(id, signal));
          if (ticket !== navRef.current) return;
          const wanted = chosenWhileOpening.current;
          chosenWhileOpening.current = null;
          const data = wanted ? { ...loaded, context: await carryPlanLocale(id, loaded.context, wanted) } : loaded;
          if (ticket !== navRef.current) return;
          showTrip(id, data);
          if (signInLost) showBanner("errors.signInEnded");
          setReady(true);
          return;
        } catch (cause) {
          if (ticket !== navRef.current) return;
          if (!(cause instanceof ApiError && (cause.status === 404 || cause.status === 403))) {
            // The server is down or slow, not forgetting the trip: keep the id and say so.
            showBanner(unreachableOf(cause));
            setReady(true);
            return;
          }
          // An expired sign-in makes every trip look gone: drop it and ask again, as the guest.
          if (attempt === 0 && signedIn && (await signInEnded())) {
            signInLost = true;
            continue;
          }
          // The server says this trip is gone or is not this browser's: forget it.
          forgetTrip();
          lost = id;
          break;
        }
      }
      const created = await withTimeout((signal) => createSession(clientToken(), signal));
      chosenWhileOpening.current = null;
      const context = await carryPlanLocale(created.session.id, created.context);
      if (ticket !== navRef.current) return;
      showTrip(created.session.id, { context, plan: null, versions: [], messages: [] });
      if (lost && !mayBeAccountTrip()) {
        // The trip that was open is gone for good: its tab goes too, and the page says why it starts a new one.
        const gone = lost;
        writeTrips((current) => current.filter((trip) => trip.id !== gone));
        setNotice("errors.tripGone");
      }
      if (signInLost) showBanner("errors.signInEnded");
      setReady(true);
    } catch (cause) {
      if (ticket !== navRef.current) return;
      // A server that never answered is an outage like any other.
      showBanner(cause instanceof ApiError && cause.status === 0 ? "errors.reconnect" : failureOf(cause, "errors.backend"));
      setReady(true);
    }
  }, [carryPlanLocale, clearBanner, showBanner, showTrip, signInEnded, withTimeout]);

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
      // Already open: nothing to load, and no other trip is wanted any more. That includes the banner of a trip that could
      // not be opened, whose Retry would switch to it. What failed on this trip stays, with its Retry.
      setOpening(false);
      setReady(true);
      if (retryRef.current?.kind === "open") clearBanner();
      return "opened";
    }
    clearBanner();
    setOpening(true);
    try {
      const data = await withTimeout((signal) => getSession(id, signal));
      if (ticket !== navRef.current) return "superseded";
      showTrip(id, data);
      setOpening(false);
      setReady(true);
      return "opened";
    } catch (cause) {
      // Whatever came of this answer, the page belongs to the latest ask: it is told nothing about this one.
      const latest = ticket === navRef.current;
      if (latest) {
        setOpening(false);
        setReady(true);
      }
      if (cause instanceof ApiError && (cause.status === 404 || cause.status === 403)) {
        // The server answers 404 both for a trip that is gone and for one that is not this identity's. When it may
        // be an account's, because the sign-in has just ended or ended before, say so and keep it.
        const ended = await refusedForSignIn(cause);
        if (ended || mayBeAccountTrip()) {
          // Retry tries to open this trip again, not whatever failed before.
          if (ticket === navRef.current) {
            showBanner(ended ? "errors.signInEnded" : "errors.signInToOpen", { kind: "open", run: () => openSession(id) });
          }
          if (ticket === navRef.current && sessionRef.current === null) rememberTrip(id);
          return "needs-sign-in";
        }
        return "missing";
      }
      // The server is down or slow: the trip is not gone. The trip that is open stays as it is, and
      // Retry asks for this one again. With no trip open, a reload tries it again.
      if (latest) {
        showBanner(unreachableOf(cause), { kind: "open", run: () => openSession(id) });
        if (sessionRef.current === null) rememberTrip(id);
      }
      return "unavailable";
    }
  }, [clearBanner, refusedForSignIn, showBanner, showTrip, withTimeout]);

  const newPlan = useCallback((): Promise<string | null> => {
    const pending = creatingRef.current;
    if (pending) {
      // A second click while the first trip is still being made takes the view but makes no second trip.
      creatingTicket.current = ++navRef.current;
      setOpening(true);
      return pending;
    }
    creatingTicket.current = ++navRef.current;
    setOpening(true);
    const making = (async () => {
      try {
        const created = await withTimeout((signal) => createSession(clientToken(), signal));
        const context = await carryPlanLocale(created.session.id, created.context);
        if (creatingTicket.current === navRef.current) {
          showTrip(created.session.id, { context, plan: null, versions: [], messages: [] });
        }
        return created.session.id;
      } catch (cause) {
        // A sign-in that has ended is dropped, so Retry makes the trip as the guest.
        await refusedForSignIn(cause);
        // No trip was made: the one that is open stays, and the traveller is told.
        if (creatingTicket.current === navRef.current) {
          showBanner(unreachableOf(cause), { kind: "open", run: () => newPlan() });
        }
        return null;
      } finally {
        creatingRef.current = null;
        if (creatingTicket.current === navRef.current) {
          setOpening(false);
          setReady(true);
        }
      }
    })();
    creatingRef.current = making;
    return making;
  }, [carryPlanLocale, refusedForSignIn, showBanner, showTrip, withTimeout]);

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
    if (!id) {
      // No trip is shown yet: the trip that arrives is shown in this language.
      chosenWhileOpening.current = value;
      return;
    }
    try {
      const data = await patchContext(id, { plan_locale: value });
      // The answer came after another trip was opened: it is not for the view that is open now.
      if (sessionRef.current !== id) return;
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
    // At once, not at the next render: a save made by the same handler (the form adds the tag still in its field and then
    // saves) must see it.
    contextRef.current = { ...contextRef.current, ...patch };
    setContextState((current) => ({ ...current, ...patch }));
  }, []);

  /** Saves the form on the open trip. When it fails, Retry does `again`. True when the details are saved. */
  const persistContext = useCallback(
    async (again: RetryTarget): Promise<boolean> => {
      const id = sessionRef.current;
      if (!id) return false;
      const form = contextRef.current;
      try {
        const patch: ContextPatch = {
          destination: form.destination,
          origin: form.origin,
          start_date: form.start_date,
          end_date: form.end_date,
          party_size: form.party_size,
          budget: form.budget,
          currency: form.currency,
          hotel_style: form.hotel_style,
          preferences: form.preferences,
          hard_constraints: form.hard_constraints,
        };
        const data = await patchContext(id, patch);
        // The answer came after another trip was opened: its details are not this view's.
        if (sessionRef.current !== id) return false;
        setContextState(data.context);
        return true;
      } catch (cause) {
        const failure = await failureOfCall(cause, "errors.saveDetails");
        if (sessionRef.current === id) showBanner(failure, again);
        return false;
      }
    },
    [failureOfCall, showBanner],
  );
  const saveContext = useCallback(
    (): Promise<boolean> => persistContext({ kind: "save", run: () => saveContext() }),
    [persistContext],
  );

  const send = useCallback(
    async (message: string) => {
      const id = sessionRef.current;
      const { busy, plan, selectedItem } = shownRef.current;
      if (!id || busy) return;
      shownRef.current.busy = true;
      setBusy(true);
      clearBanner();
      // The text is on its way, so it is no longer a draft (Retry sends the text that came back into the box).
      setDrafts((current) => (current[id] === message ? { ...current, [id]: "" } : current));
      setNotice(null);
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
      // A failed turn is what Retry sends again, whatever failed while it was running. Its text goes back into the box of
      // its trip, unless something else was typed there since, so it is not lost when the banner goes.
      const failTurn = (failure: Failure) => {
        showBanner(failure, { kind: "turn", run: () => sendRef.current(message) });
        setDrafts((current) => (current[id] ? current : { ...current, [id]: message }));
      };
      try {
        await streamChat(
          {
            session_id: id,
            message,
            locale,
            scope_item_id: selectedItem?.id ?? null,
            scope_label: scopeLabel || null,
            developer: readStoredValue("developer") === "1",
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
              if (event === "error") failTurn(data.message ? { text: String(data.message) } : "errors.turn");
            },
          },
          controller.signal,
        );
      } catch (cause) {
        if (!controller.signal.aborted) {
          const failure = await failureOfCall(cause, "errors.turn");
          if (!controller.signal.aborted) failTurn(failure);
        }
      } finally {
        // A newer stream (in the trip opened since) owns the busy state and the controller.
        const superseded = abortRef.current !== null && abortRef.current !== controller;
        if (!superseded) {
          abortRef.current = null;
          shownRef.current.busy = false;
          setBusy(false);
          setProgress(100);
        }
        // An aborted stream means the view moved on; there is nothing of this trip to reload.
        if (!controller.signal.aborted && sessionRef.current === id) await refresh();
      }
    },
    [adoptPlanLocale, clearBanner, failureOfCall, locale, refresh, showBanner],
  );
  sendRef.current = send;

  const generate = useCallback(async (): Promise<void> => {
    // Not saved, or the traveller opened another trip while the details were being saved: nothing is planned.
    if (!(await persistContext({ kind: "generate", run: () => generateRef.current() }))) return;
    await send(plan ? "Re-plan with the updated details." : "Plan my trip.");
  }, [persistContext, plan, send]);
  generateRef.current = generate;

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
      // The answer came after another trip was opened: that version is not a plan of this one.
      if (sessionRef.current !== id) return;
      setPlan(data.plan);
    } catch {
      // Keep the current plan.
    }
  }, []);

  const feedback = useCallback(
    async (useful: boolean, starsOverride?: number | null): Promise<void> => {
      const id = sessionRef.current;
      if (!id) return;
      try {
        const rating = starsOverride ?? feedbackStars;
        const result = await postFeedback(id, useful, rating);
        // The answer came after another trip was opened: the verdict belongs to the trip that was rated.
        if (sessionRef.current !== id) return;
        setFeedbackVerdict(useful ? "useful" : "not_useful");
        if (useful) {
          setFeedbackSaved(true);
          setFeedbackAdded(result.preferences_added ?? []);
        }
      } catch (cause) {
        const failure = await failureOfCall(cause, "errors.feedback");
        if (sessionRef.current === id) showBanner(failure, { kind: "rate", run: () => feedback(useful, starsOverride) });
      }
    },
    [failureOfCall, feedbackStars, showBanner],
  );

  const reload = useCallback(async () => {
    setReady(false);
    await bootstrap();
  }, [bootstrap]);

  const clearChat = useCallback(async (): Promise<void> => {
    const id = sessionRef.current;
    if (!id) return;
    try {
      await clearMessages(id);
      // The answer came after another trip was opened: its chat is not the one that was cleared.
      if (sessionRef.current !== id) return;
      setMessages([]);
      setClarify(null);
      setClarifyValues({});
      clearBanner();
    } catch (cause) {
      const failure = await failureOfCall(cause, "errors.clearChat");
      if (sessionRef.current === id) showBanner(failure, { kind: "clear", run: () => clearChat() });
    }
  }, [clearBanner, failureOfCall, showBanner]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const scrapTrip = useCallback(async (again?: () => Promise<unknown>): Promise<ScrapOutcome> => {
    const id = sessionRef.current;
    if (!id) return "kept";
    const ticket = ++navRef.current;
    setOpening(false);
    // The reply being written for this trip stops before the trip is deleted.
    abortRef.current?.abort();
    try {
      await discardSession(id);
    } catch (cause) {
      // Only a 404 means the trip is already gone, and not when the sign-in that has ended is why the server does not know
      // it: the account still has the trip. Any other answer leaves it where it is, and the page says so.
      const notFound = cause instanceof ApiError && cause.status === 404;
      const ended = notFound && (await refusedForSignIn(cause));
      if (!notFound || ended) {
        if (ticket === navRef.current) {
          // Retry scraps the trip again, with what follows a scrap (the caller's `again`); it does not reload the page.
          // With the sign-in gone it would be refused as the guest and take for gone a trip that is not.
          if (ended) showBanner("errors.signInEnded");
          else showBanner("errors.scrap", again ? { kind: "scrap", run: again } : null);
          await refresh();
        }
        return "kept";
      }
    }
    // The traveller opened another trip while the delete was on its way: that trip stays as it is.
    if (ticket !== navRef.current) return "left";
    forgetTripView();
    forgetTrip();
    sessionRef.current = null;
    setSessionId(null);
    setPlan(null);
    setMessages([]);
    setVersions([]);
    setContextState(emptyContext());
    setChangedIds([]);
    setHotelChanged(false);
    setActiveVersion(1);
    return "gone";
  }, [forgetTripView, refresh, refusedForSignIn, showBanner]);

  return {
    ready,
    opening,
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
    draft: drafts[draftKey] ?? "",
    setDraft,
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
    notice: notice === null ? null : t(notice),
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
      const again = retryRef.current;
      // A turn is being answered: it is not sent over itself, so a failed turn's banner waits for the answer to end and a
      // second press of the Retry that sent it does nothing.
      if (shownRef.current.busy && (!again || again.kind === "turn")) return;
      if (!again) {
        await reload();
        return;
      }
      // The banner goes while the action is done again, and comes back if it fails again.
      clearBanner();
      await again.run();
    },
    reload,
    clearChat,
    scrapTrip,
    abort,
    openSession,
    newPlan,
  };
}
