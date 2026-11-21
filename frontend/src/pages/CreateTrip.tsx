import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import gsap from 'gsap';
import { Flip } from 'gsap/Flip';
import { CreateSidebar } from '../components/create/CreateSidebar';
import { ChatWorkspace } from '../components/create/ChatWorkspace';
import type { ChatSessionItem } from '../components/create/ChatHistory';
import type { ChatMessageItem } from '../components/create/ChatMessage';
import type { CurrentTripContext } from '../components/create/CurrentTrip';
import type { CopilotOp, CopilotState } from '../components/create/CopilotPanel';
import type { OnboardingValues } from '../components/create/TripOnboardingForm';
import { prefillFromChat } from '../lib/readBrief';
import { graphFromLegacyText, type ItineraryGraph } from '../components/create/itineraryGraph';
import { TripPreviewCard } from '../components/create/TripPreviewCard';
import { withCopilot } from '../components/sketch/live';
import { TripStudio } from '../components/studio/TripStudio';
import { TripVerdict } from '../components/create/TripVerdict';
import { NEW_PLAN_KEY } from '../components/trending/TrendingPlaces';
import { prefersReducedMotion } from '../components/home/v2/motion';
import { useTheme } from '../context/ThemeContext';
import {
  createTrip, deleteTrip, finalizeTrip, getItineraryGraph, getTripBudget, getTripDocument, getTripMessages, listTrips, markTripUseful, sendTripMessageStream, undoTripPlan,
  type TripBudget, type TripDocument, type TripModelResponse,
} from '../services/tripService';
import { setPlannerBusy } from '../services/plannerActivity';

gsap.registerPlugin(Flip);
const BudgetWorkspace = React.lazy(() => import('../components/create/BudgetWorkspace').then((module) => ({ default: module.BudgetWorkspace })));

interface CreateTripProps {
  onNavigateHome?: () => void;
  onNavigateExplore?: () => void;
  onNavigateProfile?: () => void;
  onNavigateLogin?: () => void;
  onNavigateSignup?: () => void;
}

const sessionId = (tripId: string) => 'session-' + tripId;
/** `/trips/<id>` opens that trip's studio; the studio lives inside the planner. */
const studioTripFromPath = () => window.location.pathname.match(/^\/trips\/([^/]+)\/?$/)?.[1] ?? null;
const timeLabel = (date?: string) =>
  new Date(date || Date.now()).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

function tripContext(trip: TripModelResponse): CurrentTripContext {
  return {
    title: trip.destination ? trip.destination + ' Voyage' : 'New voyage',
    destination: trip.destination || undefined,
    origin: trip.origin_text || undefined,
    days: trip.duration_days || undefined,
    status: trip.status === 'COMPLETED' ? 'FINAL' : trip.onboarding_status !== 'COMPLETE' ? 'ONBOARDING' : trip.status === 'DRAFT' ? 'CHOOSING' : 'ROUTING',
  };
}

function sessionItem(trip: TripModelResponse): ChatSessionItem {
  return {
    id: sessionId(trip.id),
    title: (trip.destination ? trip.destination + ' voyage' : 'Your path starts here').toUpperCase(),
    timestamp: timeLabel(trip.updated_at),
    preview: trip.destination ? (trip.status === 'COMPLETED' ? 'Final · ' : '') + String(trip.duration_days || 0) + ' days in ' + trip.destination : 'Journey details needed',
  };
}


// The last studio document, so reopening a trip paints at once while the fresh one loads
// (the document takes 1-3 s against the remote database). One entry, so it never grows.
const STUDIO_DOC_KEY = 'tripverse-studio-doc';
function cachedStudioDoc(tripId: string): TripDocument | null {
  try {
    const doc = JSON.parse(localStorage.getItem(STUDIO_DOC_KEY) || 'null') as TripDocument | null;
    return doc?.trip_id === tripId ? doc : null;
  } catch {
    return null;
  }
}

function tripDateRange(start?: string | null, days?: number | null): string {
  if (!start) return 'Not set';
  const first = new Date(`${start}T00:00:00Z`);
  if (Number.isNaN(first.getTime())) return 'Not set';
  const last = new Date(first);
  last.setUTCDate(last.getUTCDate() + Math.max(0, (days || 1) - 1));
  return `${start} to ${last.toISOString().slice(0, 10)}`;
}

export const CreateTrip: React.FC<CreateTripProps> = ({
  onNavigateHome, onNavigateExplore, onNavigateProfile,
}) => {
  const { theme } = useTheme();
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messagesMap, setMessagesMap] = useState<Record<string, ChatMessageItem[]>>({});
  const [tripsMap, setTripsMap] = useState<Record<string, TripModelResponse>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [replying, setReplying] = useState(false);
  const [loadingStage, setLoadingStage] = useState('Preparing your trip');
  // Below 1024 px the sidebar is a drawer over the chat, so it starts closed there.
  const [isSidebarOpen, setIsSidebarOpen] = useState(() => window.matchMedia('(min-width: 1024px)').matches);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [studioOpen, setStudioOpen] = useState(() => studioTripFromPath() !== null);
  const [studioDay, setStudioDay] = useState<number | null>(null);
  const [studioDoc, setStudioDoc] = useState<TripDocument | null>(null);
  const studioFlip = useRef<Flip.FlipState | null>(null);
  const [isBudgetOpen, setIsBudgetOpen] = useState(false);
  const [budgetRevision, setBudgetRevision] = useState(0);
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [verdictOpen, setVerdictOpen] = useState(false);
  const [planSeed, setPlanSeed] = useState<{ destination: string; days: number | null; places: string[]; sessionId: string } | null>(null);
  const openingPlan = useRef(false);
  const preferNewPlan = useRef(false);
  const [finalizeSaving, setFinalizeSaving] = useState(false);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);
  const [budgets, setBudgets] = useState<Record<string, TripBudget>>({});
  const [backfilledGraphs, setBackfilledGraphs] = useState<Record<string, ItineraryGraph>>({});
  // Build-with-agent state streamed mid-turn; the saved message takes over once the reply is done.
  const [liveCopilots, setLiveCopilots] = useState<Record<string, CopilotState>>({});
  const attemptedGraphIds = useRef(new Set<string>());

  const loadMessages = useCallback(async (id: string, tripId: string) => {
    const rows = await getTripMessages(tripId);
    setMessagesMap((prev) => ({
      ...prev,
      [id]: rows.map((row) => ({
        id: row.id,
        sender: row.role === 'ASSISTANT' ? 'assistant' : 'user',
        content: row.content || '',
        timestamp: timeLabel(row.created_at),
        payload: row.payload,
      })),
    }));
  }, []);

  const startNewTrip = useCallback(async () => {
    setIsLoading(true);
    try {
      const created = await createTrip();
      if (!created) throw new Error('Could not create a trip');
      const id = sessionId(created.trip_id);
      const trip: TripModelResponse = {
        id: created.trip_id, status: 'DRAFT', onboarding_status: 'IN_PROGRESS',
        places_to_visit: [], created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      setTripsMap((prev) => ({ ...prev, [id]: trip }));
      setSessions((prev) => [sessionItem(trip), ...prev]);
      setMessagesMap((prev) => ({
        ...prev,
        [id]: [{
          id: created.assistant_message.id,
          sender: 'assistant',
          content: created.assistant_message.content,
          timestamp: timeLabel(created.assistant_message.created_at),
          payload: created.assistant_message.payload,
        }],
      }));
      setActiveSessionId(id);
      setIsBudgetOpen(false);
      localStorage.setItem('tripverse-active-session-id', id);
      return { id, tripId: created.trip_id };
    } finally {
      setIsLoading(false);
    }
  }, []);

  const openNewPlan = useCallback(async () => {
    let raw: string | null = null;
    try { raw = sessionStorage.getItem(NEW_PLAN_KEY); } catch { raw = null; }
    if (!raw || openingPlan.current) return;
    let parsed: { destination?: string; days?: number | null; places?: string[] };
    try { parsed = JSON.parse(raw); } catch { return; }
    const destination = parsed.destination?.trim();
    if (!destination) return;
    openingPlan.current = true;
    preferNewPlan.current = true;
    try { sessionStorage.removeItem(NEW_PLAN_KEY); } catch { /* already taken */ }
    setStudioOpen(false);
    setVerdictOpen(false);
    setFinalizeOpen(false);
    try {
      const created = await startNewTrip();
      setPlanSeed({
        destination,
        days: typeof parsed.days === 'number' ? parsed.days : null,
        places: Array.isArray(parsed.places) ? parsed.places.filter((place) => typeof place === 'string').slice(0, 8) : [],
        sessionId: created.id,
      });
    } finally {
      openingPlan.current = false;
    }
  }, [startNewTrip]);

  useEffect(() => {
    void openNewPlan();
    const onNew = () => { void openNewPlan(); };
    window.addEventListener('tripverse-new-plan', onNew);
    return () => window.removeEventListener('tripverse-new-plan', onNew);
  }, [openNewPlan]);

  useEffect(() => {
    let alive = true;
    const boot = async () => {
      try {
        const trips = await listTrips();
        if (!alive) return;
        if (!trips.length) {
          await startNewTrip();
          return;
        }
        const map = Object.fromEntries(trips.map((trip) => [sessionId(trip.id), trip]));
        setTripsMap(map);
        setSessions(trips.map(sessionItem));
        if (!preferNewPlan.current) {
          const saved = localStorage.getItem('tripverse-active-session-id');
          const linked = studioTripFromPath();
          const id = linked && map[sessionId(linked)] ? sessionId(linked)
            : saved && map[saved] ? saved : sessionId(trips[0].id);
          setActiveSessionId(id);
          await loadMessages(id, map[id].id);
        }
      } catch (error) {
        console.error('Could not load trips', error);
      } finally {
        if (alive) setIsLoading(false);
      }
    };
    void boot();
    return () => { alive = false; };
  }, [loadMessages, startNewTrip]);

  const openStudio = (day: number | null = null, push = true) => {
    const tripId = activeSessionId ? tripsMap[activeSessionId]?.id : undefined;
    if (!tripId) return;
    const card = document.querySelector('.tv-chat [data-flip-id="trip-stage"]');
    const box = card?.getBoundingClientRect();
    // The card sits where the plan was made, so it may be scrolled away: morph only from a card on screen.
    if (card && box && box.bottom > 0 && box.top < window.innerHeight && !prefersReducedMotion()) studioFlip.current = Flip.getState(card);
    setStudioDay(day);
    setIsBudgetOpen(false);
    setStudioOpen(true);
    if (push && window.location.pathname !== `/trips/${tripId}`) window.history.pushState({ studio: true }, '', `/trips/${tripId}`);
  };

  const closeStudio = (push = true) => {
    const finish = () => {
      setStudioOpen(false);
      if (push) window.history.pushState({}, '', '/create');
    };
    const studio = document.querySelector('.tv-studio');
    if (!studio || prefersReducedMotion()) return finish();
    gsap.to(studio, { opacity: 0, scale: 0.985, duration: 0.28, ease: 'power2.in', onComplete: finish });
  };

  // Back and forward between chat and studio stay inside this page.
  useEffect(() => {
    const onPop = () => {
      if (studioTripFromPath()) openStudio(null, false);
      else if (studioOpen) closeStudio(false);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  });

  useLayoutEffect(() => {
    const state = studioFlip.current;
    studioFlip.current = null;
    const stage = studioOpen ? document.querySelector('.tv-studio [data-flip-id="trip-stage"]') : null;
    if (!state || !stage) return;
    Flip.from(state, { targets: stage, duration: 0.7, ease: 'power3.inOut', absolute: true, scale: false });
  }, [studioOpen]);

  const selectSession = (id: string) => {
    setActiveSessionId(id);
    localStorage.setItem('tripverse-active-session-id', id);
    if (!messagesMap[id] && tripsMap[id]) {
      void loadMessages(id, tripsMap[id].id);
    }
  };

  const removeSession = async (id: string, event: React.MouseEvent) => {
    event.stopPropagation();
    const trip = tripsMap[id];
    if (!trip) return;
    try {
      if (!await deleteTrip(trip.id)) throw new Error('Delete request failed');
      setSessions((prev) => prev.filter((item) => item.id !== id));
      setMessagesMap((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      setTripsMap((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      if (activeSessionId === id) {
        setActiveSessionId(null);
        localStorage.removeItem('tripverse-active-session-id');
      }
    } catch (error) {
      console.error('Could not delete trip', error);
    }
  };

  const sendMessage = async (
    content: string, _attachments?: File[], form?: OnboardingValues,
    uiAction?: Record<string, unknown>,
  ) => {
    if (isLoading) return;
    let sent = content;
    if (_attachments?.length) {
      const notes = await Promise.all(_attachments.map(async (file) => {
        const textFile = file.type.startsWith('text/') || /\.(txt|md|csv|json)$/i.test(file.name);
        if (!textFile) return `Attached file: ${file.name}`;
        return `Attached ${file.name}:\n${(await file.text()).slice(0, 4000)}`;
      }));
      sent = [content, ...notes].filter(Boolean).join('\n\n');
    }
    let id = activeSessionId;
    let tripId = id ? tripsMap[id]?.id : undefined;
    let assistantId = '';
    let pendingTokens = '';
    let tokenFrame = 0;
    const flushTokens = () => {
      tokenFrame = 0;
      const delta = pendingTokens;
      pendingTokens = '';
      if (!delta || !id) return;
      const currentId = id;
      setMessagesMap((prev) => {
        const rows = prev[currentId] || [];
        const found = rows.some((row) => row.id === assistantId);
        return {
          ...prev,
          [currentId]: found
            ? rows.map((row) => row.id === assistantId ? { ...row, content: row.content + delta } : row)
            : [...rows, { id: assistantId, sender: 'assistant', content: delta, timestamp: timeLabel() }],
        };
      });
    };
    try {
      if (!tripId) {
        const created = await startNewTrip();
        id = created.id;
        tripId = created.tripId;
      }
      const currentId = id!;
      assistantId = 'assistant-' + Date.now();
      setMessagesMap((prev) => ({
        ...prev,
        [currentId]: [...(prev[currentId] || []), {
          id: 'user-' + Date.now(), sender: 'user', content: sent, timestamp: timeLabel(),
        }],
      }));
      setIsLoading(true);
      setReplying(true);
      setPlannerBusy(true);
      setLoadingStage('Getting your trip ready');
      await sendTripMessageStream(
        tripId, sent,
        {
          onGraph: (graph, final) => {
            setBackfilledGraphs((previous) => {
              const current = previous[tripId!];
              const stops = (value?: ItineraryGraph) => (value?.nodes || []).filter((node) => node.kind !== 'origin').length;
              // A partial preview must not wipe the plan that is already on screen.
              if (!final && current && stops(graph) < stops(current)) return previous;
              return { ...previous, [tripId!]: graph };
            });
          },
          onCopilot: (copilot: CopilotState) => {
            setLiveCopilots((previous) => ({ ...previous, [tripId!]: copilot }));
          },
          onStage: (label) => setLoadingStage(label),
          // Tokens arrive far faster than frames; re-render the markdown once per frame.
          onToken: (delta) => {
            pendingTokens += delta;
            if (!tokenFrame) tokenFrame = requestAnimationFrame(flushTokens);
          },
          onDone: (result) => {
            cancelAnimationFrame(tokenFrame);
            tokenFrame = 0;
            pendingTokens = '';
            setBudgetRevision((value) => value + 1);
            const savedPlan = result.assistant_message?.payload?.day_plan;
            if (Array.isArray(savedPlan)) {
              const names = savedPlan.flatMap((day: { items?: { name?: string }[] }) =>
                (day?.items || []).map((item) => String(item?.name || '').trim().toLowerCase()));
              setPinnedStop((current) => current && names.includes(current.trim().toLowerCase()) ? current : null);
            }
            if (result.trip.status === 'COMPLETED') {
              setStudioDoc((previous) => previous?.trip_id === result.trip.id ? { ...previous, is_final: true } : previous);
              const cached = cachedStudioDoc(result.trip.id);
              if (cached) try { localStorage.setItem(STUDIO_DOC_KEY, JSON.stringify({ ...cached, is_final: true })); } catch { /* cache only */ }
            }
            setTripsMap((prev) => ({ ...prev, [currentId]: result.trip }));
            setSessions((prev) => prev.map((item) =>
              item.id === currentId ? sessionItem(result.trip) : item,
            ));
            setMessagesMap((prev) => {
              const rows = prev[currentId] || [];
              const message = result.assistant_message;
              if (!message) return prev;
              // Keep the streamed row's id so React updates it in place instead of
              // remounting it (which replayed the entrance animation on the whole itinerary).
              const saved: ChatMessageItem = {
                id: rows.some((row) => row.id === assistantId) ? assistantId : message.id,
                sender: 'assistant', content: message.content,
                timestamp: timeLabel(message.created_at), payload: message.payload,
              };
              return {
                ...prev,
                [currentId]: [...rows.filter((row) => row.id !== assistantId), saved],
              };
            });
          },
        },
        form || uiAction ? 'UI_ACTION' : 'TEXT',
        form ? { action: 'SUBMIT_TRIP_ONBOARDING', ...form }
          : {
              ...(uiAction || {}),
              ...(activeCopilot ? { copilot_day: copilotDay ?? activeCopilot.current_day } : {}),
              ...(pinnedStop && !uiAction?.selected_stop ? { selected_stop: pinnedStop } : {}),
            },
      );
      flushTokens();
    } catch (error) {
      cancelAnimationFrame(tokenFrame);
      tokenFrame = 0;
      pendingTokens = '';
      const currentId = id;
      if (currentId) {
        setMessagesMap((prev) => ({
          ...prev,
          [currentId]: [...(prev[currentId] || []).filter((row) => row.id !== assistantId), {
            id: 'error-' + Date.now(), sender: 'system',
            content: error instanceof Error ? error.message : 'Could not reach the planner. Please try again.',
            timestamp: timeLabel(), stage: 'AGENT STATUS',
          }],
        }));
      }
    } finally {
      setReplying(false);
      setPlannerBusy(false);
      setIsLoading(false);
      if (tripId) {
        setLiveCopilots((previous) => {
          const next = { ...previous };
          delete next[tripId!];
          return next;
        });
      }
    }
  };

  useEffect(() => {
    if (!replying) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [replying]);

  const activeMessages = activeSessionId ? messagesMap[activeSessionId] || [] : [];
  const activeTrip = activeSessionId ? tripsMap[activeSessionId] : undefined;
  const activeBudget = activeTrip ? budgets[activeTrip.id] : undefined;
  useEffect(() => {
    if (!activeTrip || activeTrip.status === 'DRAFT') return;
    let alive = true;
    void getTripBudget(activeTrip.id).then((value) => {
      if (alive) setBudgets((previous) => ({ ...previous, [activeTrip.id]: value }));
    }).catch((error) => console.warn('Could not refresh trip budget', error));
    return () => { alive = false; };
  }, [activeTrip?.id, activeTrip?.status, budgetRevision]);
  const latestAssistant = [...activeMessages].reverse().find((message) => message.sender === 'assistant');
  const formMessage = latestAssistant?.payload?.action === 'SHOW_ONBOARDING_FORM' ? latestAssistant : null;
  const saidSoFar = activeMessages.filter((message) => message.sender === 'user').map((message) => message.content).join('\n');
  const seededBrief = planSeed && planSeed.sessionId === activeSessionId && activeTrip?.onboarding_status !== 'COMPLETE'
    ? {
        origin: '',
        destination: planSeed.destination,
        duration_days: planSeed.days || undefined,
        places_to_visit: planSeed.places,
        planning_preferences: { pace: 'balanced' as const, interests: [] as string[], avoid: [] as string[] },
      }
    : null;
  const onboardingValues = activeTrip?.onboarding_status === 'COMPLETE'
    ? null
    : seededBrief ?? prefillFromChat((formMessage?.payload?.values || {}) as Partial<OnboardingValues>, saidSoFar);
  // Build-with-agent state rides on the latest itinerary message it produced.
  const activeCopilot = useMemo(
    () => {
      if (activeTrip && liveCopilots[activeTrip.id]) return liveCopilots[activeTrip.id];
      for (const message of [...activeMessages].reverse()) {
        if (message.payload?.kind === 'PLAN_CLEARED') return null;
        if (message.payload?.copilot) return message.payload.copilot;
      }
      return null;
    },
    [activeMessages, activeTrip, liveCopilots],
  );
  // Switching days is local; the agent's own focus takes over again after each reply.
  const [copilotDay, setCopilotDay] = useState<number | null>(null);
  const [pinnedStop, setPinnedStop] = useState<string | null>(null);
  useEffect(() => setCopilotDay(null), [activeCopilot]);
  const showPlanningChoice = activeTrip?.onboarding_status === 'COMPLETE'
    && activeTrip.status === 'DRAFT'
    && activeMessages.some((message) => message.payload?.action === 'SHOW_PLANNING_CHOICE');
  const activeGraph = useMemo((): ItineraryGraph | null => {
    if (activeTrip?.status === 'DRAFT') return null;
    if (activeMessages.some((message, index, all) => message.payload?.kind === 'PLAN_CLEARED'
      && !all.slice(index + 1).some((later) => later.payload?.kind === 'ITINERARY_GRAPH'))) return null;
    if (activeTrip && backfilledGraphs[activeTrip.id]) return backfilledGraphs[activeTrip.id];
    if (activeTrip?.onboarding_status !== 'COMPLETE' || activeTrip.status === 'DRAFT') return null;
    const saved = [...activeMessages].reverse().find((message) => message.payload?.kind === 'ITINERARY_GRAPH');
    if (saved?.payload?.graph?.nodes && saved.payload.graph.edges) return saved.payload.graph as ItineraryGraph;
    const lastDraft = [...activeMessages].reverse().find((message) => message.sender === 'assistant' && message.content);
    return lastDraft && activeTrip.destination
      ? graphFromLegacyText(lastDraft.content, activeTrip.origin_text, activeTrip.destination, activeTrip.places_to_visit || [])
      : null;
  }, [activeMessages, activeTrip, backfilledGraphs]);
  const heldGraph = useRef<ItineraryGraph | null>(null);
  if ((activeGraph?.nodes.length ?? 0) > 1) heldGraph.current = activeGraph;
  const shownGraph = (activeGraph?.nodes.length ?? 0) > 1
    ? activeGraph
    : (isLoading && heldGraph.current ? heldGraph.current : activeGraph);

  // The studio document, with the agent's streamed plan folded in so the sketch updates mid-reply.
  const liveDoc = useMemo(
    () => {
      if (!activeTrip) return null;
      const doc = studioDoc?.trip_id === activeTrip.id ? studioDoc : cachedStudioDoc(activeTrip.id);
      return doc ? withCopilot(doc, activeCopilot, activeBudget) : null;
    },
    [activeTrip, studioDoc, activeCopilot, activeBudget],
  );
  // Keep the days that are already drawn while a change is still being written.
  const heldDoc = useRef<TripDocument | null>(null);
  if (liveDoc?.days?.length) heldDoc.current = liveDoc;
  const shownDoc = liveDoc?.days?.length
    ? liveDoc
    : (isLoading && heldDoc.current?.trip_id === activeTrip?.id ? heldDoc.current : liveDoc);
  // A pin names a stop on the plan. After a swap removes that stop, the composer should not keep asking about it.
  useEffect(() => {
    if (!pinnedStop || !liveDoc?.days.length) return;
    const folded = pinnedStop.trim().toLowerCase();
    const present = liveDoc.days.some((day) => day.items.some((item) => item.name.trim().toLowerCase() === folded));
    if (!present) setPinnedStop(null);
  }, [liveDoc, pinnedStop]);
  const planned = Boolean(activeTrip && activeTrip.onboarding_status === 'COMPLETE' && activeTrip.status !== 'DRAFT' && activeGraph);
  const isFinal = activeTrip?.status === 'COMPLETED';
  const canFinalize = Boolean(planned && activeTrip?.status === 'PLANNING' && !isLoading
    && (!activeCopilot || activeCopilot.status === 'complete'));
  const confirmFinalize = async () => {
    if (!activeTrip || !canFinalize || finalizeSaving) return;
    setFinalizeSaving(true);
    setFinalizeError(null);
    try {
      const updated = await finalizeTrip(activeTrip.id);
      setTripsMap((previous) => ({ ...previous, [activeSessionId!]: updated }));
      setSessions((previous) => previous.map((item) => item.id === activeSessionId ? sessionItem(updated) : item));
      setStudioDoc((previous) => previous?.trip_id === updated.id ? { ...previous, is_final: true } : previous);
      const cached = cachedStudioDoc(updated.id);
      if (cached) try { localStorage.setItem(STUDIO_DOC_KEY, JSON.stringify({ ...cached, is_final: true })); } catch { /* cache only */ }
      setBudgetRevision((revision) => revision + 1);
      setFinalizeOpen(false);
      setVerdictOpen(true);
    } catch (error) {
      setFinalizeError(error instanceof Error ? error.message : 'Could not finalize the trip. Try again.');
    } finally {
      setFinalizeSaving(false);
    }
  };
  useEffect(() => {
    if (studioOpen && !planned && !isLoading && activeTrip) closeStudio();
  }, [studioOpen, planned, isLoading, activeTrip]);
  useEffect(() => {
    if (!studioOpen || !activeTrip) return;
    let alive = true;
    void getTripDocument(activeTrip.id).then((doc) => {
      if (!alive) return;
      setStudioDoc((previous) => (
        previous?.trip_id === doc.trip_id && previous.days.length && !doc.days.length ? previous : doc
      ));
      if (doc.days.length) {
        try { localStorage.setItem(STUDIO_DOC_KEY, JSON.stringify(doc)); } catch { /* cache only */ }
      }
    })
      .catch((error) => console.warn('Could not load the trip document', error));
    return () => { alive = false; };
  }, [studioOpen, activeTrip?.id, isLoading, budgetRevision, activeMessages.length]);
  useEffect(() => { setStudioDay(null); }, [activeTrip?.id]);

  useEffect(() => {
    if (!studioOpen || !activeTrip || activeTrip.onboarding_status !== 'COMPLETE' || activeTrip.status === 'DRAFT' || !activeMessages.length) return;
    if (activeMessages.some((message) => message.payload?.kind === 'ITINERARY_GRAPH' && (message.payload.graph?.version || 0) >= 8)) return;
    if (attemptedGraphIds.current.has(activeTrip.id)) return;
    attemptedGraphIds.current.add(activeTrip.id);
    void getItineraryGraph(activeTrip.id)
      .then((graph) => setBackfilledGraphs((previous) => ({ ...previous, [activeTrip.id]: graph })))
      .catch((error) => console.warn('Could not backfill itinerary graph', error));
  }, [activeMessages, activeTrip, studioOpen]);

  const chatProps = {
    messages: activeMessages,
    onSendMessage: sendMessage,
    isSidebarOpen,
    onToggleSidebar: () => setIsSidebarOpen((open) => !open),
    onOpenMobileSidebar: () => setIsSidebarOpen(true),
    activeChatTitle: sessions.find((item) => item.id === activeSessionId)?.title,
    isBudgetOpen,
    onToggleBudget: () => setIsBudgetOpen((open) => !open),
    onSelectPrompt: (prompt: string) => { void sendMessage(prompt); },
    onClarify: (text: string) => { void sendMessage(text); },
    onOpenVerdict: isFinal ? () => setVerdictOpen(true) : undefined,
    onUndo: activeTrip && activeSessionId ? () => {
      void undoTripPlan(activeTrip.id).then((ok) => { if (ok) void loadMessages(activeSessionId, activeTrip.id); });
    } : undefined,
    isLoading,
    loadingStage,
    guideId: activeTrip?.planning_preferences?.guide,
    pinnedStop,
    onClearPin: () => setPinnedStop(null),
    onResetChat: () => { void startNewTrip(); },
    onboardingValues: seededBrief ?? (formMessage ? onboardingValues : null),
    openBrief: Boolean(seededBrief),
    showComposer: !seededBrief && !formMessage && !showPlanningChoice,
    showPlanningChoice,
    planningBrief: showPlanningChoice && activeTrip ? {
      origin: activeTrip.origin_text || '',
      destination: activeTrip.destination || '',
      duration_days: activeTrip.duration_days || undefined,
      places_to_visit: activeTrip.places_to_visit || [],
      planning_preferences: {
        ...activeTrip.planning_preferences,
        pace: activeTrip.planning_preferences?.pace || 'balanced',
        interests: activeTrip.planning_preferences?.interests || [],
        avoid: activeTrip.planning_preferences?.avoid || [],
      },
      currency: activeTrip.currency as OnboardingValues['currency'],
    } : null,
    onGenerateFull: () => { void sendMessage('Generate the full itinerary', undefined, undefined, { action: 'GENERATE_FULL_ITINERARY' }); },
    onStartBuild: () => { void sendMessage("Let's build it day by day together", undefined, undefined, { action: 'START_BUILD_WITH_AGENT' }); },
    copilot: activeCopilot,
    budget: activeBudget,
    isFinal,
    canFinalize,
    onRequestFinalize: () => { setFinalizeError(null); setFinalizeOpen(true); },
    copilotDay: activeCopilot ? copilotDay ?? activeCopilot.current_day : null,
    onCopilotDay: setCopilotDay,
    onCopilotOps: (ops: CopilotOp[], label: string, day: number) => { void sendMessage(label, undefined, undefined, { action: 'COPILOT_OPS', ops, copilot_day: day }); },
    onSubmitOnboarding: (values: OnboardingValues) => {
      setPlanSeed(null);
      const summary = values.origin + ' to ' + values.destination + ' for ' + values.duration_days + ' days'
        + (values.places_to_visit.length ? ', visiting ' + values.places_to_visit.join(', ') : '');
      void sendMessage(summary, undefined, values);
    },
  };

  // "Add to day N" from Around here. Build-with-the-agent trips take a structured add (budget and day
  // checks run in the engine); one-shot plans get the same request as a chat message, which revises the draft.
  const addPlace = (name: string, day: number | null) => {
    if (isFinal) return;
    if (activeCopilot) {
      const target = day ?? copilotDay ?? activeCopilot.current_day;
      void sendMessage(`Add ${name} to day ${target}`, undefined, undefined,
        { action: 'COPILOT_OPS', ops: [{ op: 'add', name, day: target }], copilot_day: target });
    } else {
      void sendMessage(day ? `Please add ${name} to day ${day}.` : `Please add ${name} to the plan.`);
    }
  };

  return (
    <div className={'tv2 tv2-app tv-create ' + (theme === 'dark' ? 'is-dark dark' : '') + (studioOpen ? ' is-studio' : '')}>
      <CreateSidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        width={sidebarWidth}
        onWidthChange={setSidebarWidth}
        onNewChat={() => { void startNewTrip(); }}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={selectSession}
        onDeleteSession={removeSession}
        currentTrip={activeTrip ? tripContext(activeTrip) : null}
        onNavigateHome={onNavigateHome}
        onNavigateProfile={onNavigateProfile}
        onNavigateExplore={onNavigateExplore}
        onExploreSpatial={() => openStudio()}
      />
      <ChatWorkspace
        key={activeSessionId || 'new-trip'}
        {...chatProps}
        onOpenStudio={planned ? () => openStudio() : undefined}
        tripPreview={(planned || isLoading) && activeTrip && shownGraph ? (
          <TripPreviewCard tripId={activeTrip.id} destination={activeTrip.destination}
            days={activeTrip.duration_days} startDate={activeTrip.planning_preferences?.start_date}
            guideId={activeTrip.planning_preferences?.guide} graph={shownGraph} onOpen={(day) => openStudio(day ?? null)} />
        ) : null}
      />
      {studioOpen && (planned || isLoading) && activeTrip && (
        <TripStudio trip={activeTrip} tripContext={tripContext(activeTrip)} graph={shownGraph}
          document={shownDoc} dark={theme === 'dark'}
          isLoading={isLoading} loadingStage={loadingStage} day={studioDay} onSelectDay={setStudioDay}
          onBack={() => closeStudio()} onOpenBudget={() => setIsBudgetOpen(true)}
          onFinalize={canFinalize ? () => { setFinalizeError(null); setFinalizeOpen(true); } : undefined}
          onAddPlace={isLoading || isFinal ? undefined : addPlace}
          pinned={pinnedStop}
          onPin={(name) => setPinnedStop((current) => current === name ? null : name)}
          chat={<ChatWorkspace key={`drawer-${activeSessionId}`} {...chatProps} variant="drawer" />} />
      )}
      {isBudgetOpen && activeTrip && <React.Suspense fallback={null}>
        <BudgetWorkspace tripId={activeTrip.id} destination={activeTrip.destination} graph={activeGraph} finalTrip={isFinal}
          refreshVersion={budgetRevision} onClose={() => setIsBudgetOpen(false)}
          onChange={(value) => { setBudgets((previous) => ({ ...previous, [activeTrip.id]: value })); setBudgetRevision((revision) => revision + 1); }} />
      </React.Suspense>}
      {verdictOpen && activeTrip && isFinal && (
        <TripVerdict trip={activeTrip} onClose={() => setVerdictOpen(false)} onSaved={(saved) => {
          const sessionKey = activeSessionId;
          if (!sessionKey) return;
          setTripsMap((prev) => {
            const current = prev[sessionKey];
            if (!current) return prev;
            return { ...prev, [sessionKey]: { ...current, planning_preferences: { ...current.planning_preferences, useful: saved.useful, stars: saved.stars } } };
          });
        }} />
      )}
      {finalizeOpen && activeTrip && <div className="tv-finalize" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !finalizeSaving) setFinalizeOpen(false); }}>
        <section className="tv-finalize__card" role="dialog" aria-modal="true" aria-labelledby="tv-finalize-title">
          <span className="tv-label">FINAL REVIEW</span>
          <h2 id="tv-finalize-title">Finalize this trip?</h2>
          <p>Check the plan before making it read-only. You can still view and export it afterward.</p>
          <dl>
            <div><dt>Trip</dt><dd>{activeTrip.duration_days || 0} days in {activeTrip.destination || 'your destination'}</dd></div>
            <div><dt>Places</dt><dd>{[...new Set(activeGraph?.nodes.filter((node) => node.kind === 'stop').map((node) => node.name) || [])].join(' · ') || 'See itinerary'}</dd></div>
            <div><dt>Dates</dt><dd>{tripDateRange(activeTrip.planning_preferences?.start_date, activeTrip.duration_days)}</dd></div>
            <div><dt>Budget</dt><dd>{activeBudget ? `${activeBudget.is_locked ? 'Locked' : 'Estimated'} ${new Intl.NumberFormat(undefined, { style: 'currency', currency: activeBudget.currency }).format(Number(activeBudget.is_locked ? activeBudget.locked_total : activeBudget.projected_total))}${activeBudget.unpriced_count ? ` · ${activeBudget.unpriced_count} costs unknown` : ''}` : 'Loading budget…'}</dd></div>
          </dl>
          {finalizeError && <p className="tv-finalize__error" role="alert">{finalizeError}</p>}
          <div className="tv-finalize__actions"><button type="button" className="tv-btn tv-btn--ghost" disabled={finalizeSaving} onClick={() => setFinalizeOpen(false)}>Keep editing</button><button type="button" className="tv-btn tv-btn--primary" disabled={finalizeSaving || !activeBudget} onClick={() => { void confirmFinalize(); }}>{finalizeSaving ? 'Finalizing…' : 'Confirm final trip'}</button></div>
        </section>
      </div>}
    </div>
  );
};
