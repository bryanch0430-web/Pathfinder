import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppHeader,
  AuthDialog,
  Banner,
  ChatPanel,
  GuideNotes,
  LoadingScreen,
  MAIN_PANEL_ID,
  MapPanel,
  PlanLanguageBanner,
  PlannerTemplate,
  ShareDialog,
  StatusSummary,
  Text,
  TrendingRail,
  TripPanel,
  TripWorkspace,
  VideoNotes,
  appTabId,
  isHotelScope,
  onTablistKeyDown,
  useModalBehaviour,
  type AppTab,
  type AuthUser,
  type FxSnapshot,
  type GuideChunk,
  type TrendingPlace,
  type TrendingTrace,
} from "./design-system";
import { Icon } from "./design-system/icons";
import {
  ApiError,
  claimSession,
  getFx,
  getGuides,
  getTrending,
  login,
  me,
  register,
  revokeShare,
  shareSession,
  suggestPlaces,
} from "./lib/api";
import { downloadPlanMarkdown } from "./lib/export";
import { failureText } from "./lib/failure";
import { downloadPlanSlides } from "./lib/slides";
import { useI18n, type Locale } from "./lib/i18n";
import { planLocaleFor } from "./lib/planLocale";
import { forgetSignIn, readStoredValue, writeStoredValue } from "./lib/storage";
import { readTrips, subscribeTrips, writeTrips, type Trip } from "./lib/tripsStore";
import { usePathfinder } from "./lib/usePathfinder";
import { EvaluationPage, SavedPage, SettingsPage } from "./app/pages";

const HASH_TAB: Record<string, AppTab> = {
  plan: "plan",
  saved: "saved",
  evaluation: "evaluation",
  settings: "settings",
};

/** The share dialog: the link for one trip, from creation until it is turned off. */
interface ShareState {
  open: boolean;
  sessionId: string | null;
  url: string | null;
  expiresAt: string | null;
  loading: boolean;
  copied: boolean;
  revoked: boolean;
  revoking: boolean;
  error: string | null;
}

const CLOSED_SHARE: ShareState = {
  open: false,
  sessionId: null,
  url: null,
  expiresAt: null,
  loading: false,
  copied: false,
  revoked: false,
  revoking: false,
  error: null,
};

/**
 * The label a new tab is stored with. It is saved in `pathfinder.trips`, so it
 * stays in English there and is translated only when shown.
 */
const UNTITLED_TRIP = "Untitled trip";

/** The plan area under the trip tabs is their tabpanel, labelled by the selected tab. */
const TRIP_PANEL_ID = "pf-trip-panel";
const tripTabId = (id: string) => `pf-trip-tab-${id}`;

function tabFromHash(): AppTab {
  const hash = window.location.hash.replace("#", "");
  return HASH_TAB[hash] ?? "plan";
}

export default function App() {
  const { locale, setLocale, t } = useI18n();
  const pf = usePathfinder(locale);
  // EN/简/繁 translate the interface; 繁 also shows the plan in Traditional Chinese.
  const changeLocale = (value: string) => {
    setLocale(value);
    const planLocale = planLocaleFor(value as Locale);
    if (planLocale !== pf.planLocale) void pf.setPlanLocale(planLocale);
  };
  // Exports carry the disclaimer whenever they carry translated text.
  const planDisclaimer =
    pf.planLocale !== "en" && pf.translation?.status === "translated"
      ? pf.translation.disclaimer?.trim() || t("planLang.disclaimer")
      : "";
  const languageBanner =
    pf.planLocale === "en" ? null : (
      <PlanLanguageBanner
        disclaimer={pf.translation?.disclaimer}
        notice={pf.languageNotice}
        reason={pf.translation?.reason}
      />
    );
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
  const [share, setShare] = useState<ShareState>(CLOSED_SHARE);
  const [guides, setGuides] = useState<GuideChunk[]>([]);
  const [guidesLoading, setGuidesLoading] = useState(false);
  // The tabs live in the browser store, shared with every other window of this browser.
  const [trips, setTrips] = useState<Trip[]>(readTrips);
  useEffect(() => subscribeTrips(setTrips), []);

  const loadTrending = useCallback(async () => {
    setTrendingLoading(true);
    setTrendingError(null);
    try {
      const data = await getTrending(trendingQuery, {
        clientToken: readStoredValue("client") || "",
        destination: pf.context.destination || "",
      });
      setTrending(data.items ?? []);
      setTrendingTrace(data.trace ?? null);
    } catch (cause) {
      setTrending([]);
      setTrendingError(failureText(cause, t, "errors.trending"));
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
    if (!readStoredValue("auth")) return;
    void me()
      .then((data) => setAuthUser(data.user))
      .catch((cause) => {
        // Only the server saying the sign-in is no longer valid signs the traveller out; an
        // outage or a slow answer keeps the stored sign-in for the next try.
        if (!(cause instanceof ApiError && cause.status === 401)) return;
        // A `user:` profile token is refused without its bearer, so fall back to the guest token.
        forgetSignIn();
        setAuthUser(null);
      });
  }, []);

  useEffect(() => {
    const activeId = pf.sessionId;
    if (!activeId) return;
    const known = trips.find((trip) => trip.id === activeId);
    const label = pf.plan?.destination.name || known?.label || UNTITLED_TRIP;
    // The open trip always has its tab, with its destination as the label. This runs again whenever the list
    // changes, so a tab that another window's write or a cleared store took away comes back.
    if (known?.label === label) return;
    writeTrips((current) => {
      const stored = current.find((trip) => trip.id === activeId);
      return stored
        ? current.map((trip) => (trip.id === activeId ? { ...trip, label } : trip))
        : [...current, { id: activeId, label }];
    });
  }, [pf.sessionId, pf.plan?.destination.name, trips]);

  const scrapActiveTrip = async () => {
    const id = pf.sessionId;
    if (!id || !window.confirm(t("plan.scrapConfirm"))) return;
    // The trip is deleted first: when the server refuses, it keeps its tab and the page says so.
    const outcome = await pf.scrapTrip();
    if (outcome === "kept") return;
    const remaining = writeTrips((current) => current.filter((trip) => trip.id !== id));
    // Already on another trip, which stays. Otherwise the next tab that still opens takes over, and a tab
    // for a trip that is gone is dropped on the way.
    if (outcome === "left") return;
    for (const trip of remaining) {
      if ((await openTrip(trip.id)) !== "missing") return;
    }
    await createTripTab();
  };

  const openTrip = async (id: string) => {
    // Only a trip the server has no record of for a signed-in traveller is dropped from the tabs. One
    // that could not be reached, or that may belong to an account, stays for the next try.
    const outcome = await pf.openSession(id);
    if (outcome === "missing") writeTrips((current) => current.filter((trip) => trip.id !== id));
    return outcome;
  };

  const createTripTab = async () => {
    const id = await pf.newPlan();
    // None was made: the page has said so, and the trip that was open stays.
    if (!id) return null;
    writeTrips((current) => (current.some((trip) => trip.id === id) ? current : [...current, { id, label: UNTITLED_TRIP }]));
    return id;
  };

  const handleAuth = async (
    mode: "login" | "register",
    email: string,
    password: string,
    displayName: string,
  ) => {
    const guestToken = readStoredValue("client") || "";
    if (!readStoredValue("guest")) {
      writeStoredValue("guest", guestToken);
    }
    const payload = {
      email,
      password,
      display_name: displayName,
      client_token: guestToken,
      session_id: pf.sessionId,
    };
    const result = mode === "login" ? await login(payload) : await register(payload);
    writeStoredValue("auth", result.token);
    writeStoredValue("client", result.profile_token);
    setAuthUser(result.user);
    setAuthOpen(false);
    if (pf.sessionId) {
      try {
        await claimSession(pf.sessionId, guestToken);
      } catch {
        // A new session will be created on the next plan if this one is gone.
      }
    }
    await pf.reload();
  };

  const handleExportSlides = async () => {
    if (!pf.plan) return;
    try {
      await downloadPlanSlides(pf.plan, planDisclaimer);
    } catch (cause) {
      window.alert(t("errors.slides"));
    }
  };

  const handleShare = async () => {
    const sessionId = pf.sessionId;
    if (!sessionId) return;
    setShare({ ...CLOSED_SHARE, open: true, sessionId, loading: true });
    // Only apply a result to the dialog that asked for it.
    const update = (patch: Partial<ShareState>) =>
      setShare((current) => (current.sessionId === sessionId ? { ...current, ...patch } : current));
    try {
      const result = await shareSession(sessionId);
      const url = `${window.location.origin}${result.url}`;
      let copied = false;
      try {
        await navigator.clipboard.writeText(url);
        copied = true;
      } catch {
        // The link stays in a selectable field for a manual copy.
      }
      update({ loading: false, url, expiresAt: result.expires_at ?? null, copied });
    } catch (cause) {
      update({ loading: false, error: failureText(cause, t, "errors.share") });
    }
  };

  const copyShareLink = async () => {
    if (!share.url) return;
    try {
      await navigator.clipboard.writeText(share.url);
      setShare((current) => ({ ...current, copied: true, error: null }));
    } catch {
      setShare((current) => ({ ...current, error: t("errors.copy") }));
    }
  };

  const turnOffShareLink = async () => {
    const sessionId = share.sessionId;
    if (!sessionId) return;
    setShare((current) => ({ ...current, revoking: true, error: null }));
    try {
      await revokeShare(sessionId);
      setShare((current) => ({ ...current, revoking: false, revoked: true, url: null, expiresAt: null, copied: false }));
    } catch (cause) {
      setShare((current) => ({
        ...current,
        revoking: false,
        error: failureText(cause, t, "errors.revoke"),
      }));
    }
  };

  const handleLogout = () => {
    // Under both prefixes, so the legacy-key migration cannot sign the user back in.
    forgetSignIn();
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
      return `${t("map.hotel")} · ${pf.selectedItem.name}`;
    }
    const day = pf.plan.days.find((entry) => entry.items.some((item) => item.id === pf.selectedItem?.id));
    const index = day ? pf.plan.days.indexOf(day) + 1 : 0;
    return `${day ? t("day.number", { n: index }) : t("plan.stop")} · ${pf.selectedItem.name}`;
  }, [pf.plan, pf.selectedItem, t]);

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
          if (!(await createTripTab())) return;
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
        onLocaleChange={changeLocale}
        onHelp={() => setHelpOpen(true)}
        user={authUser}
        onAuth={() => setAuthOpen(true)}
        onLogout={handleLogout}
        onExport={pf.plan ? () => downloadPlanMarkdown(pf.plan!, planDisclaimer) : undefined}
        onExportSlides={pf.plan ? () => void handleExportSlides() : undefined}
        onShare={pf.plan ? () => void handleShare() : undefined}
        onNewTrip={() => void createTripTab()}
      />
      <div role="tabpanel" id={MAIN_PANEL_ID} aria-labelledby={appTabId(tab)}>
      {tab === "plan" ? (
        !pf.ready ? (
          <LoadingScreen label={t("common.loading")} />
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
                  onClarifySubmit={(message) => void pf.submitClarify(message)}
                  onSend={(message) => void pf.send(message)}
                  scope={scope}
                  onClearScope={() => pf.selectItem(null)}
                  error={pf.sessionId ? pf.error : null}
                  disabled={!pf.sessionId}
                  onRetry={() => void pf.retry()}
                  onClear={() => void pf.clearChat()}
                  trace={pf.trace}
                  live={pf.live}
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
                <div
                  className="pf-plan-panel"
                  role="tabpanel"
                  id={TRIP_PANEL_ID}
                  aria-labelledby={pf.sessionId ? tripTabId(pf.sessionId) : undefined}
                  aria-busy={pf.opening}
                >
                {pf.plan ? (
                <TripWorkspace
                  key={pf.sessionId}
                  plan={pf.plan}
                  languageNotice={languageBanner}
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
                  key={pf.sessionId}
                  onExample={(message) => void pf.send(message)}
                  notice={pf.notice}
                  backendError={pf.sessionId ? null : pf.error}
                  disabled={!pf.sessionId}
                  onRetry={() => void pf.reload()}
                />
                )}
                </div>
              </div>
            }
            right={
              <>
                {pf.plan ? (
                  <MapPanel
                    key={pf.sessionId}
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
      </div>

      <AuthDialog open={authOpen} onClose={() => setAuthOpen(false)} onSubmit={handleAuth} />
      <ShareDialog
        open={share.open}
        onClose={() => setShare(CLOSED_SHARE)}
        url={share.url}
        expiresAt={share.expiresAt}
        loading={share.loading}
        copied={share.copied}
        revoked={share.revoked}
        revoking={share.revoking}
        error={share.error}
        onCopy={() => void copyShareLink()}
        onRevoke={() => void turnOffShareLink()}
      />
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
  const { t } = useI18n();
  const scroller = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = scroller.current?.querySelector<HTMLElement>("[data-active='true']");
    node?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [activeId, trips.length]);
  // One tab stop for the list: the selected tab, or the first when none is selected.
  const stop = trips.some((trip) => trip.id === activeId) ? activeId : trips[0]?.id;
  return (
    <div className="pf-plan-tabs">
      <div className="pf-plan-tabs__scroll" role="tablist" aria-label={t("plan.openTrips")} ref={scroller} onKeyDown={onTablistKeyDown}>
        {trips.map((trip, index) => (
          <button
            key={trip.id}
            id={tripTabId(trip.id)}
            type="button"
            role="tab"
            aria-selected={trip.id === activeId}
            aria-controls={trip.id === activeId ? TRIP_PANEL_ID : undefined}
            tabIndex={trip.id === stop ? 0 : -1}
            className="pf-plan-tab"
            data-active={trip.id === activeId}
            onClick={() => onSelect(trip.id)}
            title={trip.label === UNTITLED_TRIP ? t("plan.untitled") : trip.label}
          >
            <span className="pf-plan-tab__index">{index + 1}</span>
            <span className="pf-plan-tab__label">{trip.label === UNTITLED_TRIP ? t("plan.untitled") : trip.label}</span>
          </button>
        ))}
      </div>
      <button type="button" className="pf-plan-tab pf-plan-tab--add" onClick={onNew} aria-label={t("plan.createAnother")}>
        <Icon name="plus" size={12} />
        {t("header.newTrip")}
      </button>
      {activeId ? (
        <button type="button" className="pf-plan-tab pf-plan-tab--scrap" onClick={onScrap} aria-label={t("plan.scrap")}>
          <Icon name="trash" size={11} />
        </button>
      ) : null}
    </div>
  );
}

function WelcomePanel({
  onExample,
  notice,
  backendError,
  disabled,
  onRetry,
}: {
  onExample: (message: string) => void;
  notice: string | null;
  /** Shown here only while no trip is open; with a trip open the chat shows its errors. */
  backendError: string | null;
  /** No trip is open to send an example to. */
  disabled: boolean;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  return (
    <section className="pf-card pf-welcome">
      {notice ? <Banner tone="info">{notice}</Banner> : null}
      {backendError ? (
        <Banner>
          {backendError}
          <button type="button" className="pf-button" data-variant="ghost" data-size="sm" onClick={onRetry}>
            {t("common.retry")}
          </button>
        </Banner>
      ) : null}
      <div className="pf-welcome__mark">
        <Icon name="compass" size={30} />
      </div>
      <h1 className="pf-heading" data-level="2">
        {t("welcome.title")}
      </h1>
      <Text size="md" tone="secondary" className="pf-welcome__body">
        {t("welcome.body")}
      </Text>
      <div className="pf-welcome__examples">
        {/* Sent to the planner as the traveller's request, so they stay in English. */}
        {[
          "Plan a 3-day trip to Hong Kong from Singapore for 2 people in March, budget 9,000 HKD.",
          "Plan a 4-day Kyoto trip from Hong Kong for 2 in April, budget 400,000 JPY. I want Fushimi Inari and a slow pace.",
          "Plan a 2-day Taipei trip from Hong Kong for 1 person next month, budget 12,000 TWD.",
        ].map((example) => (
          <button key={example} type="button" className="pf-example" disabled={disabled} onClick={() => onExample(example)}>
            <Icon name="sparkles" size={14} />
            <span>{example}</span>
            <Icon name="arrowRight" size={14} />
          </button>
        ))}
      </div>
      <div className="pf-welcome__steps">
        {(
          [
            ["route", "welcome.step1", "welcome.step1Body"],
            ["users", "welcome.step2", "welcome.step2Body"],
            ["shield", "welcome.step3", "welcome.step3Body"],
            ["memory", "welcome.step4", "welcome.step4Body"],
          ] as const
        ).map(([icon, title, body]) => (
          <div className="pf-welcome__step" key={title}>
            <Icon name={icon} size={16} />
            <div className="pf-stack" style={{ gap: 1 }}>
              <Text size="sm" weight="semibold">{t(title)}</Text>
              <Text size="xs" tone="tertiary">{t(body)}</Text>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function HowItWorks() {
  const { t } = useI18n();
  return (
    <section className="pf-card pf-how">
      <Text size="xs" tone="tertiary" weight="semibold" style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        {t("workspace.howAiWorked")}
      </Text>
      <div className="pf-stack pf-stack--md">
        {(
          [
            ["compass", "how.system1", "how.system1Body"],
            ["users", "welcome.step2", "how.agentsBody"],
            ["route", "how.grounding", "how.groundingBody"],
            ["shield", "how.validation", "how.validationBody"],
          ] as const
        ).map(([icon, title, body]) => (
          <div className="pf-row" key={title} style={{ alignItems: "flex-start" }}>
            <span className="pf-how__icon"><Icon name={icon} size={15} /></span>
            <div className="pf-stack" style={{ gap: 2 }}>
              <Text size="sm" weight="semibold">{t(title)}</Text>
              <Text size="xs" tone="tertiary">{t(body)}</Text>
            </div>
          </div>
        ))}
      </div>
      <div className="pf-how__footer">
        <StatusSummary plan={{ constraints: [], data_quality: { gaps: [] } }} />
        <Text size="xs" tone="tertiary">{t("how.noPlan")}</Text>
      </div>
    </section>
  );
}

function HelpDrawer({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const modalRef = useModalBehaviour<HTMLElement>(true, onClose);
  return (
    <div className="pf-dialog-overlay" role="presentation" onMouseDown={onClose}>
      <aside ref={modalRef} className="pf-drawer" role="dialog" aria-modal="true" aria-label={t("help.label")} tabIndex={-1} onMouseDown={(event) => event.stopPropagation()}>
        <div className="pf-row pf-row--between">
          <h2 className="pf-heading" data-level="4">{t("help.title")}</h2>
          <button type="button" className="pf-icon-btn" aria-label={t("help.close")} onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <Text size="sm">{t("help.paths")}</Text>
        <Text size="sm">{t("help.agents")}</Text>
        <Text size="sm">{t("help.edits")}</Text>
        <Text size="sm" tone="tertiary">{t("help.languages")}</Text>
      </aside>
    </div>
  );
}
