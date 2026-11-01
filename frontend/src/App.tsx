import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AppHeader,
  AuthDialog,
  Banner,
  ChatPanel,
  GuideNotes,
  LoadingScreen,
  MapPanel,
  PlannerTemplate,
  StatusSummary,
  Text,
  TrendingRail,
  TripPanel,
  TripWorkspace,
  VideoNotes,
  isHotelScope,
  type AppTab,
  type AuthUser,
  type FxSnapshot,
  type GuideChunk,
  type TrendingPlace,
  type TrendingTrace,
} from "./design-system";
import { Icon } from "./design-system/icons";
import { claimSession, discardSession, getFx, getGuides, getTrending, login, me, register, shareSession, suggestPlaces } from "./lib/api";
import { downloadPlanMarkdown } from "./lib/export";
import { useI18n } from "./lib/i18n";
import { usePathfinder } from "./lib/usePathfinder";
import { EvaluationPage, SavedPage, SettingsPage } from "./app/pages";

const HASH_TAB: Record<string, AppTab> = {
  plan: "plan",
  saved: "saved",
  evaluation: "evaluation",
  settings: "settings",
};

function tabFromHash(): AppTab {
  const hash = window.location.hash.replace("#", "");
  return HASH_TAB[hash] ?? "plan";
}

export default function App() {
  const { locale, setLocale, t } = useI18n();
  const pf = usePathfinder(locale);
  const [tab, setTab] = useState<AppTab>(tabFromHash);
  const [mapDay, setMapDay] = useState<string>("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [trending, setTrending] = useState<TrendingPlace[]>([]);
  const [trendingTrace, setTrendingTrace] = useState<TrendingTrace | null>(null);
  const [trendingLoading, setTrendingLoading] = useState(true);
  const [trendingError, setTrendingError] = useState<string | null>(null);
  const [trendingInput, setTrendingInput] = useState("");
  const [trendingQuery, setTrendingQuery] = useState("");
  const [fx, setFx] = useState<FxSnapshot | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [guides, setGuides] = useState<GuideChunk[]>([]);
  const [guidesLoading, setGuidesLoading] = useState(false);
  const [trips, setTrips] = useState<{ id: string; label: string }[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("pathfinder30.trips") || "[]") as { id: string; label: string }[];
    } catch {
      return [];
    }
  });

  const loadTrending = useCallback(async () => {
    setTrendingLoading(true);
    setTrendingError(null);
    try {
      const data = await getTrending(trendingQuery, {
        clientToken: localStorage.getItem("pathfinder30.client") || "",
        destination: pf.context.destination || "",
      });
      setTrending(data.items ?? []);
      setTrendingTrace(data.trace ?? null);
    } catch (cause) {
      setTrending([]);
      setTrendingError(cause instanceof Error ? cause.message : "Trending is unavailable.");
    } finally {
      setTrendingLoading(false);
    }
  }, [trendingQuery, pf.context.destination]);

  useEffect(() => {
    const timer = window.setTimeout(() => setTrendingQuery(trendingInput), 300);
    return () => window.clearTimeout(timer);
  }, [trendingInput]);

  useEffect(() => {
    void loadTrending();
  }, [loadTrending]);

  useEffect(() => {
    void getFx("HKD")
      .then(setFx)
      .catch(() => setFx(null));
  }, []);

  useEffect(() => {
    if (!pf.plan) {
      setGuides([]);
      return;
    }
    const destination = pf.plan.destination.name;
    setGuidesLoading(true);
    void getGuides(destination, pf.context.preferences.join(" "))
      .then((data) => setGuides(data.items))
      .catch(() => setGuides([]))
      .finally(() => setGuidesLoading(false));
  }, [pf.plan?.destination.name, pf.context.preferences]);

  useEffect(() => {
    if (!localStorage.getItem("pathfinder30.auth")) return;
    void me()
      .then((data) => setAuthUser(data.user))
      .catch(() => {
        localStorage.removeItem("pathfinder30.auth");
        setAuthUser(null);
      });
  }, []);

  useEffect(() => {
    const activeId = pf.sessionId;
    if (!activeId) return;
    setTrips((current) => {
      const label = pf.plan?.destination.name || current.find((trip) => trip.id === activeId)?.label || "Untitled trip";
      const next = current.some((trip) => trip.id === activeId)
        ? current.map((trip) => (trip.id === activeId ? { ...trip, label } : trip))
        : [...current, { id: activeId, label }];
      localStorage.setItem("pathfinder30.trips", JSON.stringify(next));
      return next;
    });
  }, [pf.sessionId, pf.plan?.destination.name]);

  const scrapActiveTrip = async () => {
    const id = pf.sessionId;
    if (!id || !window.confirm("Scrap this plan? Its chat and plan are deleted. Other tabs stay.")) return;
    try {
      await discardSession(id);
    } catch {
      // Already gone; drop it locally.
    }
    const remaining = trips.filter((trip) => trip.id !== id);
    setTrips(remaining);
    localStorage.setItem("pathfinder30.trips", JSON.stringify(remaining));
    if (remaining.length) await pf.openSession(remaining[0].id);
    else await createTripTab();
  };

  const openTrip = async (id: string) => {
    const opened = await pf.openSession(id);
    if (!opened) {
      setTrips((current) => {
        const next = current.filter((trip) => trip.id !== id);
        localStorage.setItem("pathfinder30.trips", JSON.stringify(next));
        return next;
      });
    }
  };

  const createTripTab = async () => {
    const id = await pf.newPlan();
    setTrips((current) => {
      const next = current.some((trip) => trip.id === id) ? current : [...current, { id, label: "Untitled trip" }];
      localStorage.setItem("pathfinder30.trips", JSON.stringify(next));
      return next;
    });
    return id;
  };

  const handleAuth = async (
    mode: "login" | "register",
    email: string,
    password: string,
    displayName: string,
  ) => {
    const guestToken = localStorage.getItem("pathfinder30.client") || "";
    if (!localStorage.getItem("pathfinder30.guest")) {
      localStorage.setItem("pathfinder30.guest", guestToken);
    }
    const payload = {
      email,
      password,
      display_name: displayName,
      client_token: guestToken,
      session_id: pf.sessionId,
    };
    const result = mode === "login" ? await login(payload) : await register(payload);
    localStorage.setItem("pathfinder30.auth", result.token);
    localStorage.setItem("pathfinder30.client", result.profile_token);
    setAuthUser(result.user);
    setAuthOpen(false);
    if (pf.sessionId) {
      try {
        await claimSession(pf.sessionId);
      } catch {
        // A new session will be created on the next plan if this one is gone.
      }
    }
    await pf.reload();
  };

  const handleShare = async () => {
    if (!pf.sessionId) return;
    try {
      const result = await shareSession(pf.sessionId);
      const url = `${window.location.origin}${result.url}`;
      try {
        await navigator.clipboard.writeText(url);
        window.alert(`Share link copied:\n${url}`);
      } catch {
        window.prompt("Copy this read-only trip link:", url);
      }
    } catch (cause) {
      window.alert(cause instanceof Error ? cause.message : "The trip could not be shared.");
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("pathfinder30.auth");
    const guest = localStorage.getItem("pathfinder30.guest");
    if (guest) localStorage.setItem("pathfinder30.client", guest);
    setAuthUser(null);
    void pf.reload();
  };

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (pf.plan && !mapDay) setMapDay(pf.plan.days[0]?.date ?? "");
  }, [pf.plan, mapDay]);

  useEffect(() => {
    setMapDay(pf.plan?.days[0]?.date ?? "");
  }, [pf.plan?.trip_id]);

  useEffect(() => {
    if (!pf.plan || !pf.selectedItem) return;
    const id = pf.selectedItem.id;
    const exists =
      pf.plan.days.some((day) => day.items.some((item) => item.id === id)) ||
      pf.plan.hotels.some((hotel) => hotel.id === id);
    if (!exists) pf.selectItem(null);
  }, [pf.plan, pf.selectedItem]);

  const changeTab = (next: AppTab) => {
    setTab(next);
    window.location.hash = next;
  };

  const selectItem = (item: typeof pf.selectedItem) => {
    pf.selectItem(item);
    if (item && pf.plan) {
      const day = pf.plan.days.find((entry) => entry.items.some((candidate) => candidate.id === item.id));
      if (day) setMapDay(day.date);
    }
  };

  const scope = useMemo(() => {
    if (!pf.selectedItem || !pf.plan) return null;
    if (isHotelScope(pf.selectedItem.id, pf.plan.hotels)) {
      return `Hotel · ${pf.selectedItem.name}`;
    }
    const day = pf.plan.days.find((entry) => entry.items.some((item) => item.id === pf.selectedItem?.id));
    const index = day ? pf.plan.days.indexOf(day) + 1 : 0;
    return `${day ? `Day ${index}` : "Stop"} · ${pf.selectedItem.name}`;
  }, [pf.plan, pf.selectedItem]);

  const trendingRail = (
    <TrendingRail
      items={trending}
      trace={trendingTrace}
      loading={trendingLoading}
      error={trendingError}
      onRefresh={() => void loadTrending()}
      query={trendingInput}
      onQueryChange={setTrendingInput}
      onTry={(prompt) => {
        void (async () => {
          changeTab("plan");
          await createTripTab();
          const origin = pf.context.origin?.trim();
          void pf.send(origin ? `${prompt} Start from ${origin}.` : prompt);
        })();
      }}
    />
  );

  return (
    <>
      <AppHeader
        activeTab={tab}
        onTabChange={changeTab}
        locale={locale}
        onLocaleChange={setLocale}
        onHelp={() => setHelpOpen(true)}
        user={authUser}
        onAuth={() => setAuthOpen(true)}
        onLogout={handleLogout}
        onExport={pf.plan ? () => downloadPlanMarkdown(pf.plan!) : undefined}
        onShare={pf.plan ? () => void handleShare() : undefined}
        onNewTrip={() => void createTripTab()}
      />
      {tab === "plan" ? (
        !pf.ready ? (
          <LoadingScreen label={t("common.loading", "Loading")} />
        ) : (
          <PlannerTemplate
            header={null}
            left={
              <div className="pf-left-stack">
                <TripPanel
                  context={pf.context}
                  plan={pf.plan}
                  onChange={pf.setContext}
                  onSave={() => void pf.saveContext()}
                  onGenerate={() => void pf.generate()}
                  onSuggestDestination={async (query) => (await suggestPlaces(query)).items}
                  submitting={pf.busy}
                />
                <ChatPanel
                  messages={pf.messages}
                  busy={pf.busy}
                  agents={pf.agents}
                  progress={pf.progress}
                  progressMessage={pf.progressMessage}
                  clarify={pf.clarify}
                  clarifyValues={pf.clarifyValues}
                  onClarifyChange={pf.setClarifyValue}
                  onClarifySubmit={() => void pf.submitClarify()}
                  onSend={(message) => void pf.send(message)}
                  scope={scope}
                  onClearScope={() => pf.selectItem(null)}
                  error={pf.error}
                  onRetry={() => void pf.retry()}
                  onClear={() => void pf.clearChat()}
                />
              </div>
            }
            centre={
              <div className="pf-plan-shell">
                <PlanTabs
                  trips={trips}
                  activeId={pf.sessionId}
                  onSelect={(id) => void openTrip(id)}
                  onNew={() => void createTripTab()}
                  onScrap={() => void scrapActiveTrip()}
                />
                {pf.plan ? (
                <TripWorkspace
                  plan={pf.plan}
                  versions={pf.versions}
                  activeVersion={pf.activeVersion}
                  onVersionChange={(version) => void pf.selectVersion(version)}
                  selectedItemId={pf.selectedItem?.id ?? null}
                  onSelectItem={selectItem}
                  onUseful={(stars) => {
                    pf.setStars(stars);
                    void pf.feedback(true, stars);
                  }}
                  onNotUseful={() => void pf.feedback(false)}
                  feedbackStars={pf.feedbackStars}
                  feedbackVerdict={pf.feedbackVerdict}
                  feedbackAdded={pf.feedbackAdded}
                  trace={pf.trace}
                  onRefresh={() => void pf.send("Refresh the data that is stale or unavailable.")}
                  onFocusDay={setMapDay}
                  updatedIds={pf.changedIds}
                  hotelChanged={pf.hotelChanged}
                />
              ) : (
                <WelcomePanel
                  onExample={(message) => void pf.send(message)}
                  backendError={pf.error}
                  onRetry={() => void pf.reload()}
                />
                )}
              </div>
            }
            right={
              <>
                {pf.plan ? (
                  <MapPanel
                    plan={pf.plan}
                    activeDay={mapDay || pf.plan.days[0]?.date}
                    onActiveDay={setMapDay}
                    selectedItemId={pf.selectedItem?.id ?? null}
                    onSelectItem={selectItem}
                    onOptimize={() => void pf.send("Optimise my budget and keep the confirmed stops.")}
                    fx={fx}
                    updatedIds={pf.changedIds}
                    hotelChanged={pf.hotelChanged}
                  />
                ) : (
                  <>
                    {trendingRail}
                    <HowItWorks />
                  </>
                )}
                {pf.plan ? <VideoNotes notes={pf.plan.video_notes} /> : null}
                {pf.plan ? <GuideNotes items={guides} loading={guidesLoading} /> : null}
                {pf.plan ? trendingRail : null}
              </>
            }
          />
        )
      ) : null}
      {tab === "saved" ? <SavedPage /> : null}
      {tab === "evaluation" ? <EvaluationPage /> : null}
      {tab === "settings" ? <SettingsPage signedIn={Boolean(authUser)} /> : null}

      <AuthDialog open={authOpen} onClose={() => setAuthOpen(false)} onSubmit={handleAuth} />
      {helpOpen ? <HelpDrawer onClose={() => setHelpOpen(false)} /> : null}
    </>
  );
}

function PlanTabs({
  trips,
  activeId,
  onSelect,
  onNew,
  onScrap,
}: {
  trips: { id: string; label: string }[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onScrap: () => void;
}) {
  return (
    <div className="pf-plan-tabs" role="tablist" aria-label="Open trips">
      {trips.map((trip, index) => (
        <button
          key={trip.id}
          type="button"
          role="tab"
          aria-selected={trip.id === activeId}
          className="pf-plan-tab"
          data-active={trip.id === activeId}
          onClick={() => onSelect(trip.id)}
          title={trip.label}
        >
          <span className="pf-plan-tab__index">{index + 1}</span>
          <span className="pf-plan-tab__label">{trip.label}</span>
        </button>
      ))}
      <button type="button" className="pf-plan-tab pf-plan-tab--add" onClick={onNew} aria-label="Create another trip">
        <Icon name="plus" size={12} />
        New trip
      </button>
      {activeId ? (
        <button type="button" className="pf-plan-tab pf-plan-tab--scrap" onClick={onScrap} aria-label="Scrap this trip">
          <Icon name="trash" size={11} />
        </button>
      ) : null}
    </div>
  );
}

function WelcomePanel({
  onExample,
  backendError,
  onRetry,
}: {
  onExample: (message: string) => void;
  backendError: string | null;
  onRetry: () => void;
}) {
  return (
    <section className="pf-card pf-welcome">
      {backendError ? (
        <Banner>
          {backendError}
          <button type="button" className="pf-button" data-variant="ghost" data-size="sm" onClick={onRetry}>
            Retry
          </button>
        </Banner>
      ) : null}
      <div className="pf-welcome__mark">
        <Icon name="compass" size={30} />
      </div>
      <h1 className="pf-heading" data-level="2">
        Start with a trip, not a form
      </h1>
      <Text size="md" tone="secondary" className="pf-welcome__body">
        Tell the assistant where, when, with whom and for how much. It runs four specialist agents in
        parallel, keeps every fact traced to a source, and leaves a visible gap when a source is missing.
      </Text>
      <div className="pf-welcome__examples">
        {[
          "Plan a 3-day trip to Hong Kong from Singapore for 2 people in March, budget 9,000 HKD.",
          "Plan a 4-day Kyoto trip from Hong Kong for 2 in April, budget 400,000 JPY. I want Fushimi Inari and a slow pace.",
          "Plan a 2-day Taipei trip from Hong Kong for 1 person next month, budget 12,000 TWD.",
        ].map((example) => (
          <button key={example} type="button" className="pf-example" onClick={() => onExample(example)}>
            <Icon name="sparkles" size={14} />
            <span>{example}</span>
            <Icon name="arrowRight" size={14} />
          </button>
        ))}
      </div>
      <div className="pf-welcome__steps">
        {[
          ["route", "Typed route", "plan · modify · ask · clarify"],
          ["users", "Four agents", "attractions · hotels · weather · tickets"],
          ["shield", "Grounded", "source, freshness, no invention"],
          ["memory", "Memory", "useful trips seed the next plan"],
        ].map(([icon, title, body]) => (
          <div className="pf-welcome__step" key={title}>
            <Icon name={icon as "route"} size={16} />
            <div className="pf-stack" style={{ gap: 1 }}>
              <Text size="sm" weight="semibold">{title}</Text>
              <Text size="xs" tone="tertiary">{body}</Text>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section className="pf-card pf-how">
      <Text size="xs" tone="tertiary" weight="semibold" style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        How the AI worked
      </Text>
      <div className="pf-stack pf-stack--md">
        {[
          ["compass", "System 1", "Chooses exactly one path and a confidence. Below 0.8 it clarifies."],
          ["users", "Four agents", "Attraction, hotel, weather and ticket run in parallel under a time budget."],
          ["route", "Grounding", "The planner may only select and order candidate ids with a source."],
          ["shield", "Validation", "Dates, budget, route continuity and ticket conflicts are checked before return."],
        ].map(([icon, title, body]) => (
          <div className="pf-row" key={title} style={{ alignItems: "flex-start" }}>
            <span className="pf-how__icon"><Icon name={icon as "compass"} size={15} /></span>
            <div className="pf-stack" style={{ gap: 2 }}>
              <Text size="sm" weight="semibold">{title}</Text>
              <Text size="xs" tone="tertiary">{body}</Text>
            </div>
          </div>
        ))}
      </div>
      <div className="pf-how__footer">
        <StatusSummary plan={{ constraints: [], data_quality: { gaps: [] } }} />
        <Text size="xs" tone="tertiary">No plan yet. The first turn will create v1.</Text>
      </div>
    </section>
  );
}

function HelpDrawer({ onClose }: { onClose: () => void }) {
  return (
    <div className="pf-dialog-overlay" role="presentation" onMouseDown={onClose}>
      <aside className="pf-drawer" role="dialog" aria-modal="true" aria-label="Help" onMouseDown={(event) => event.stopPropagation()}>
        <div className="pf-row pf-row--between">
          <h2 className="pf-heading" data-level="4">How Pathfinder works</h2>
          <button type="button" className="pf-icon-btn" aria-label="Close help" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <Text size="sm">
          One message takes one path. A typed System 1 decision chooses plan, modify or ask; an unclear
          request is clarified instead of guessed.
        </Text>
        <Text size="sm">
          A new plan runs four agents in parallel: attractions, hotels, weather and tickets. The planner may
          select and order verified candidates, never invent one. Every item keeps a source and a freshness
          status.
        </Text>
        <Text size="sm">
          An edit reruns only the affected agents, preserves locked and confirmed stops, and increments the
          version. A quick question answers from the current plan first, then at most one allowlisted tool.
        </Text>
        <Text size="sm" tone="tertiary">
          English is the primary language. Simplified and Traditional Chinese are supplemental interface
          locales; place names remain in the form the source returned.
        </Text>
      </aside>
    </div>
  );
}
