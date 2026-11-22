import { Fragment, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { TripMap } from "./maps/TripMap";
import { kmBetween, MODE, modeForDistance, SEGMENT_COLOR, type TravelMode } from "./maps/geo";
import { CLOCK_MAX, CLOCK_MIN, CLOCK_STEP, clockText, minutesOf } from "./maps/time";
import { Avatar, Badge, Button, Dialog, Divider, Drawer, Heading, IconButton, Input, Select, Switch, Text } from "./atoms";
import { AgentChip, EmptyState, IntentBadge, StatusPill, type AgentStatus } from "./elements";
import { timelineAt } from "../lib/mapTimeline";
import { maskTyped, setRedaction, DEFAULT_PATTERNS, type RedactionFlags } from "../lib/maskPii";
import { previewRedaction } from "../lib/api";
import { ClashNote, DirectSheet, clashesOf, clashIds, type LayoutMove, type StopCommit } from "./directEdit";
import { Icon, type IconName } from "./icons";
import { SituationReading, WriterDiagnosis } from "./writerDiagnosis";
import { type ModelKeysProps } from "./ModelKeys";
import { OrchestratorSettings } from "./OrchestratorSettings";
import { ReviewerSettings, type ReviewerBody, type ReviewerConfig } from "./ReviewerSettings";
import { WriterSettings } from "./WriterSettings";
import { SerpApiRotation } from "./SerpApiRotation";
import {
  AgentActivity,
  BudgetPanel,
  ChatBubble,
  ClarifyCard,
  ConstraintList,
  DaySection,
  type StopOffer,
  DayStrip,
  ExperimentTable,
  failureReason,
  GapList,
  IntakeNotice,
  MetricCard,
  MiniStat,
  Field,
  PageHeader,
  PlaceCard,
  PlanAssessmentCard,
  BannerClose,
  PlanFeedback,
  StatusSummary,
  TravelOptions,
  TrendChart,
  TripContextForm,
  SearchField,
  LinkRow,
  PlaceImage,
  RankNote,
} from "./molecules";
import type {
  AgentState,
  AuthUser,
  ChatAttachment,
  ChatMessage,
  ClarifyPayload,
  ClientProfile,
  EvalCaseResult,
  EvalDataset,
  EvalPreview,
  EvalRun,
  EvalRunSummary,
  GuideChunk,
  HotelStay,
  IntakePayload,
  PlanItem,
  RoomType,
  StayMatch,
  DayPlan,
  TicketOption,
  TransportLeg,
  TripContext,
  TripPlan,
  RunTrace,
  TrendingPlace,
  TrendingTrace,
  VideoNote,
} from "./types";
import { hotelScopeItem } from "./scope";
import { openCount, slotCount, slotState, transportKinds } from "../lib/choices";
import { ApiError, getLegOptions, getLegs, type ChoiceKind, type LegOption, type LegOptions, type LlmProvider, type MapAnchor, type MapLeg, type MapStep } from "../lib/api";
import { dayRoute, dayScope, overviewStops, travelLine } from "../lib/mapScope";
import {
  chosenFor,
  daysBySegment,
  legHome,
  legInto,
  legsOf,
  nightsBetween,
  optionsFor,
  optionsOfStay,
  routeLabel,
  segmentOfDay,
  segmentsOf,
  staysIn,
  transferInto,
  transferLegsOf,
  transferOutOf,
} from "../lib/segments";
import {
  addAttachments,
  ATTACHMENT_MAX_BYTES,
  ATTACHMENT_TYPES,
  ATTACHMENTS_MAX,
  ATTACHMENTS_TOTAL_MAX_BYTES,
  fileSizeText,
  readAttachments,
  type AttachmentRefusal,
} from "../lib/attachments";
import { failureText } from "../lib/failure";
import { safeHref } from "../lib/safeHref";
import { addTags, isImeKey } from "../lib/typing";
import { useI18n, type MessageKey, type Translate } from "../lib/i18n";
import { readStoredValue, writeStoredValue } from "../lib/storage";
import { onTablistKeyDown } from "./tablist";

const FRESHNESS_SECTION: Record<"weather" | "tickets" | "hotels", MessageKey> = {
  weather: "workspace.freshness.weather",
  tickets: "workspace.freshness.tickets",
  hotels: "workspace.freshness.hotels",
};

/* --------------------------------------------------------------- header -- */

export type AppTab = "plan" | "saved" | "evaluation" | "cases" | "settings";

/** The page under the header's tabs is their tabpanel, labelled by the selected tab. */
export const MAIN_PANEL_ID = "pf-main-panel";
export const appTabId = (tab: AppTab) => `pf-tab-${tab}`;

const TABS: { id: AppTab; label: MessageKey; icon: "compass" | "book" | "route" | "shield" | "sparkles"; admin?: boolean }[] = [
  { id: "plan", label: "tab.plan", icon: "compass" },
  { id: "saved", label: "tab.saved", icon: "book" },
  { id: "evaluation", label: "tab.evaluation", icon: "route", admin: true },
  { id: "cases", label: "tab.cases", icon: "sparkles", admin: true },
  { id: "settings", label: "tab.settings", icon: "shield", admin: true },
];

export function AppHeader({
  activeTab,
  onTabChange,
  locale,
  onLocaleChange,
  onHelp,
  onNewTrip,
  onExportFile,
  onExportCalendar,
  onExportSketch,
  onImport,
  onExportSlides,
  onShare,
  user,
  onAuth,
  onLogout,
  signIn = true,
  theme,
  onTheme,
  onOpenDesk,
  deskActive = false,
  admin = false,
}: {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  locale: string;
  onLocaleChange: (locale: string) => void;
  onHelp?: () => void;
  onNewTrip?: () => void;
  onExportFile?: () => void;
  onExportCalendar?: () => void;
  onExportSketch?: () => void;
  onImport?: () => void;
  onExportSlides?: () => void;
  onShare?: () => void;
  user?: AuthUser | null;
  onAuth?: () => void;
  onLogout?: () => void;
  /**
   * False in local admin mode: no Log in button. Log out stays in the account menu of a stored sign-in, so one made
   * before the mode was turned on can still be cleared.
   */
  signIn?: boolean;
  theme?: "light" | "dark";
  onTheme?: () => void;
  /** Leaves the planner for the contact desk. Omitted for a traveller. */
  onOpenDesk?: () => void;
  /** The desk is open over this page. */
  deskActive?: boolean;
  /** Evaluation, error analysis, settings, and the desk are admin areas. */
  admin?: boolean;
}) {
  const { t } = useI18n();
  const [accountOpen, setAccountOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement | null>(null);
  const exportRef = useRef<HTMLDivElement | null>(null);
  const exportItems = [
    { id: "file", label: t("header.planFile"), title: t("header.planFileTitle"), run: onExportFile },
    { id: "calendar", label: t("header.calendar"), title: t("header.calendarTitle"), run: onExportCalendar },
    { id: "sketch", label: t("header.sketch"), title: t("header.sketchTitle"), run: onExportSketch },
    { id: "slides", label: t("header.slides"), title: t("header.slidesTitle"), run: onExportSlides },
  ];
  useEffect(() => {
    if (!accountOpen) return;
    const close = (event: PointerEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [accountOpen]);
  useEffect(() => {
    if (!exportOpen) return;
    const close = (event: PointerEvent) => {
      if (!exportRef.current?.contains(event.target as Node)) setExportOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExportOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [exportOpen]);

  return (
    <header className="pf-header">
      <div className="pf-header__inner">
        <a className="pf-logo" href="#plan" onClick={() => onTabChange("plan")}>
          <span className="pf-logo__mark">
            <Icon name="compass" size={18} />
          </span>
          <span className="pf-logo__word">
            Path<span>finder</span>
          </span>
        </a>
        <div className="pf-tabs" role="tablist" aria-label={t("header.sections")} onKeyDown={onTablistKeyDown}>
          {TABS.filter((tab) => !tab.admin).map((tab) => (
            <button
              key={tab.id}
              id={appTabId(tab.id)}
              type="button"
              role="tab"
              className="pf-tab"
              data-active={activeTab === tab.id}
              aria-selected={activeTab === tab.id}
              aria-controls={activeTab === tab.id ? MAIN_PANEL_ID : undefined}
              tabIndex={activeTab === tab.id ? 0 : -1}
              onClick={() => onTabChange(tab.id)}
            >
              {t(tab.label)}
            </button>
          ))}
        </div>
        {admin ? (
          <div className="pf-tabs pf-tabs__admin" role="tablist" aria-label={t("header.adminOnly")} onKeyDown={onTablistKeyDown}>
            <span className="pf-tabs__admin-label">{t("header.adminOnly")}</span>
            {TABS.filter((tab) => tab.admin).map((tab) => (
              <button
                key={tab.id}
                id={appTabId(tab.id)}
                type="button"
                role="tab"
                className="pf-tab"
                data-active={activeTab === tab.id}
                aria-selected={activeTab === tab.id}
                aria-controls={activeTab === tab.id ? MAIN_PANEL_ID : undefined}
                tabIndex={activeTab === tab.id ? 0 : -1}
                onClick={() => onTabChange(tab.id)}
              >
                {t(tab.label)}
              </button>
            ))}
            {onOpenDesk ? (
              <button type="button" role="tab" className="pf-tab" data-active={deskActive} onClick={onOpenDesk}>
                {t("tab.desk")}
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="pf-header__actions">
          {onTheme ? (
            <IconButton icon="sun" label={t("header.theme")} aria-pressed={theme === "dark"} onClick={onTheme} />
          ) : null}
          <select className="pf-lang" aria-label={t("header.language")} value={locale} onChange={(event) => onLocaleChange(event.target.value)}>
            <option value="en">EN</option>
            <option value="zh-Hans">简</option>
            <option value="zh-Hant">繁</option>
          </select>
          <div className="pf-export" ref={exportRef}>
            <Button
              size="sm"
              variant="ghost"
              iconLeft="download"
              iconRight="chevronDown"
              aria-haspopup="menu"
              aria-expanded={exportOpen}
              onClick={() => setExportOpen((value) => !value)}
            >
              {t("header.export")}
            </Button>
            {exportOpen ? (
              <div className="pf-export__menu" role="menu" aria-label={t("header.export")}>
                {exportItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="menuitem"
                    className="pf-export__item"
                    title={item.title}
                    disabled={!item.run}
                    onClick={() => {
                      setExportOpen(false);
                      item.run?.();
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          {onImport ? (
            <Button size="sm" variant="ghost" iconLeft="plus" onClick={onImport} title={t("header.importTitle")}>
              {t("header.import")}
            </Button>
          ) : null}
          {onShare ? (
            <Button size="sm" variant="ghost" iconLeft="external" onClick={onShare}>
              {t("header.share")}
            </Button>
          ) : null}
          {onNewTrip ? (
            <Button size="sm" variant="secondary" iconLeft="plus" onClick={onNewTrip}>
              {t("header.newTrip")}
            </Button>
          ) : null}
          <IconButton icon="book" label={t("header.help")} onClick={onHelp} />
          {user ? (
            <div className="pf-account" ref={accountRef}>
              <button
                type="button"
                className="pf-account__trigger"
                aria-haspopup="menu"
                aria-expanded={accountOpen}
                onClick={() => setAccountOpen((value) => !value)}
              >
                <span aria-hidden="true"><Avatar name={user.display_name || user.email} /></span>
                <span className="pf-account__name">{user.display_name || user.email}</span>
                <Icon name="chevronDown" size={12} />
              </button>
              {accountOpen ? (
                <div className="pf-account__popover" role="menu">
                  <div className="pf-account__identity">
                    <strong>{user.display_name || user.email}</strong>
                    <span>{user.email}</span>
                  </div>
                  <button
                    type="button"
                    role="menuitem"
                    className="pf-account__item"
                    onClick={() => {
                      onTabChange("saved");
                      setAccountOpen(false);
                    }}
                  >
                    <Icon name="book" size={14} /> {t("tab.saved")}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="pf-account__item"
                    onClick={() => {
                      onTabChange("settings");
                      setAccountOpen(false);
                    }}
                  >
                    <Icon name="shield" size={14} /> {t("tab.settings")}
                  </button>
                  <div className="pf-account__divider" />
                  <button
                    type="button"
                    role="menuitem"
                    className="pf-account__item pf-account__item--danger"
                    onClick={() => {
                      setAccountOpen(false);
                      onLogout?.();
                    }}
                  >
                    <Icon name="logout" size={14} /> {t("header.logout")}
                  </button>
                </div>
              ) : null}
            </div>
          ) : signIn ? (
            <Button size="sm" variant="secondary" iconLeft="user" onClick={onAuth}>
              {t("auth.login")}
            </Button>
          ) : null}
        </div>
      </div>
    </header>
  );
}

/* ------------------------------------------------------------- trip form -- */

export function TripPanel({
  context,
  plan,
  onChange,
  onSave,
  onGenerate,
  onSuggestDestination,
  saving,
  submitting,
  initialOpen = false,
}: {
  context: Partial<TripContext>;
  plan: TripPlan | null;
  onChange: (patch: Partial<TripContext>) => void;
  onSave: () => void;
  onGenerate: () => void;
  onSuggestDestination?: (query: string) => Promise<{ label: string }[]>;
  saving?: boolean;
  submitting?: boolean;
  initialOpen?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(initialOpen);
  const title = plan?.destination.name || context.destination || t("trip.setDetails");
  const dates = plan
    ? `${plan.dates.start} → ${plan.dates.end}`
    : context.start_date && context.end_date
      ? `${context.start_date} → ${context.end_date}`
      : t("trip.noDates");
  const party = plan?.party_size ?? context.party_size;
  const budget = plan?.budget
    ? `${plan.budget.currency} ${plan.budget.amount.toLocaleString()}`
    : context.budget
      ? `${context.currency ?? ""} ${context.budget.toLocaleString()}`
      : t("trip.noBudget");
  return (
    <>
      <button
        type="button"
        className="pf-trip-bar"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span className="pf-trip-bar__icon"><Icon name="compass" size={17} /></span>
        <span className="pf-trip-bar__body">
          <span className="pf-trip-bar__kicker">{t("trip.kicker")}</span>
          <span className="pf-trip-bar__title">{title}</span>
          <span className="pf-trip-bar__meta">
            {dates} · {party ? t(party === 1 ? "trip.travellersOne" : "trip.travellers", { count: party }) : t("trip.noParty")} · {budget}
          </span>
        </span>
        <span className="pf-trip-bar__action">
          {t("trip.edit")} <Icon name="chevronRight" size={13} />
        </span>
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title={t("trip.details")}>
        <div className="pf-trip-drawer">
          <Text size="sm" tone="secondary">
            {t("trip.drawerIntro")}
          </Text>
          <TripContextForm
            values={context}
            onChange={onChange}
            onSubmit={() => {
              setOpen(false);
              onGenerate();
            }}
            onSave={() => {
              onSave();
              setOpen(false);
            }}
            onSuggestDestination={onSuggestDestination}
            saving={saving}
            submitting={submitting}
          />
        </div>
      </Drawer>
    </>
  );
}

/* ----------------------------------------------------------------- chat -- */

// Sent to the planner as the traveller's own words, whose routing is tuned on
// English phrasing, so these stay in English in every locale.
const QUICK_PROMPTS = ["Plan my trip", "Make it cheaper", "What is the weather on day 2?", "Add a museum"];

const DEVELOPER_EVENT = "pathfinder-developer";

function readDeveloperMode() {
  return readStoredValue("developer") === "1";
}

function writeDeveloperMode(on: boolean) {
  writeStoredValue("developer", on ? "1" : "0");
  window.dispatchEvent(new Event(DEVELOPER_EVENT));
}

function useDeveloperMode() {
  const [on, setOn] = useState(readDeveloperMode);
  useEffect(() => {
    const sync = () => setOn(readDeveloperMode());
    window.addEventListener(DEVELOPER_EVENT, sync);
    return () => window.removeEventListener(DEVELOPER_EVENT, sync);
  }, []);
  return [on, (next: boolean) => {
    writeDeveloperMode(next);
    setOn(next);
  }] as const;
}

function downloadTurnLog(
  trace: RunTrace | null | undefined,
  live: { id: string; title: string; body: string }[] | undefined,
  messages: ChatMessage[],
) {
  const file = {
    downloaded_at: new Date().toISOString(),
    request_id: trace?.request_id ?? "",
    user_message: trace?.message ?? [...messages].reverse().find((item) => item.role === "user")?.content ?? "",
    messages: messages.map((item) => ({ role: item.role, content: item.content, at: item.created_at })),
    why: trace?.why ?? [],
    route: trace?.reason ?? "",
    spans: trace?.spans ?? [],
    generations: trace?.generations ?? [],
    searches: trace?.searches ?? [],
    tools: trace?.tools ?? [],
    live: live ?? [],
  };
  const blob = new Blob([JSON.stringify(file, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${file.request_id || "turn"}-log.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function DeveloperToggle() {
  const { t } = useI18n();
  const [on, setOn] = useDeveloperMode();
  return (
    <button
      type="button"
      className="pf-plan-tab"
      data-active={on ? "true" : "false"}
      aria-pressed={on}
      onClick={() => setOn(!on)}
    >
      {t("settings.developer")} · {on ? t("settings.on") : t("settings.off")}
    </button>
  );
}

interface DecisionCall {
  purpose?: string;
  model?: string;
  latency_ms?: number;
  reasoning?: string;
  output_preview?: string;
  user_preview?: string;
}

interface DecisionStep {
  id?: string;
  phase?: string;
  model?: string;
  status?: string;
  seconds?: number;
  note?: string;
}

interface DecisionRecord {
  why?: string[];
  generations?: DecisionCall[];
  steps?: DecisionStep[];
}

interface DecisionSection {
  id: string;
  phase: string;
  model: string;
  seconds?: number;
  call: DecisionCall | null;
  notes: string[];
}

function phaseOfPurpose(purpose: string): string {
  const text = purpose.toLowerCase();
  if (text.includes("route") || text.includes("router")) return "routing";
  if (text.includes("modify") || text.includes("extract")) return "editing";
  if (text.includes("review")) return "review";
  if (text.includes("reply")) return "reply";
  if (text.includes("plan") || text.includes("compose")) return "planning";
  return text || "model";
}

function decisionSections(decision: DecisionRecord): DecisionSection[] {
  const calls = decision.generations ?? [];
  const used = new Set<number>();
  const take = (phase: string): DecisionCall | null => {
    const index = calls.findIndex((call, cursor) => !used.has(cursor) && phaseOfPurpose(call.purpose ?? "") === phase);
    if (index < 0) return null;
    used.add(index);
    return calls[index];
  };
  const steps = (decision.steps ?? []).filter((step) => (step.phase ?? "").trim());
  const sections: DecisionSection[] = steps.map((step, index) => ({
    id: step.id || `${step.phase}-${index}`,
    phase: step.phase ?? "",
    model: step.model ?? "",
    seconds: typeof step.seconds === "number" ? step.seconds : undefined,
    call: take(step.phase ?? ""),
    notes: step.note ? [step.note] : [],
  }));
  calls.forEach((call, index) => {
    if (used.has(index)) return;
    const phase = phaseOfPurpose(call.purpose ?? "");
    sections.push({
      id: `call-${index}`,
      phase,
      model: call.model ?? "",
      seconds: call.latency_ms ? call.latency_ms / 1000 : undefined,
      call,
      notes: [],
    });
  });
  const notes = decision.why ?? [];
  if (notes.length) {
    const routing = sections.find((section) => section.phase === "routing");
    if (routing) routing.notes = notes;
    else sections.unshift({ id: "decision", phase: "decision", model: "", notes, call: null });
  }
  return sections;
}

function decisionOf(meta?: Record<string, unknown>): DecisionRecord | null {
  const raw = meta?.decision;
  if (!raw || typeof raw !== "object") return null;
  const decision = raw as DecisionRecord;
  const why = (decision.why ?? []).filter((line) => line.trim());
  const generations = (decision.generations ?? []).filter(
    (call) => call.purpose || call.model || call.reasoning?.trim() || call.output_preview?.trim() || call.user_preview?.trim(),
  );
  const steps = (decision.steps ?? []).filter((step) => (step.phase ?? "").trim());
  if (!why.length && !generations.length && !steps.length) return null;
  return { why, generations, steps };
}

function decisionFromLive(
  live: { title: string; body: string }[] | undefined,
  trace: RunTrace | null | undefined,
): DecisionRecord | null {
  const generations = [
    ...(trace?.generations ?? []).map((call) => ({
      purpose: call.purpose,
      model: call.model,
      latency_ms: call.latency_ms,
      reasoning: call.reasoning,
      output_preview: call.output_preview,
      user_preview: call.user_preview,
    })),
    ...(live ?? []).map((step) => ({ purpose: step.title, reasoning: step.body })),
  ];
  const why = trace?.why ?? [];
  if (!generations.length && !why.length) return null;
  return { why, generations };
}

function DecisionCallBody({ call }: { call: DecisionCall }) {
  const { t } = useI18n();
  return (
    <>
      {call.user_preview ? (
        <>
          <p className="pf-decision__call">{t("chat.sentToModel")}</p>
          <pre className="pf-decision__prompt">{call.user_preview}</pre>
        </>
      ) : null}
      {call.reasoning?.trim() ? <p>{call.reasoning}</p> : null}
      {call.output_preview ? <pre>{call.output_preview}</pre> : null}
    </>
  );
}

/** Headings the review prompt already writes. Each one becomes its own section. */
const REVIEW_MARKS: { key: "skill" | "traveller" | "edit" | "plan" | "pages"; mark: string }[] = [
  { key: "skill", mark: "Reasonableness skill" },
  { key: "traveller", mark: "Traveller:" },
  { key: "edit", mark: "What the edit did:" },
  { key: "plan", mark: "Plan now:" },
  { key: "pages", mark: "Duration pages" },
];

function reviewParts(preview: string): { key: (typeof REVIEW_MARKS)[number]["key"]; body: string }[] | null {
  const found = REVIEW_MARKS
    .map((item) => ({ ...item, at: preview.indexOf(item.mark) }))
    .filter((item) => item.at >= 0)
    .sort((a, b) => a.at - b.at);
  if (!found.some((item) => item.key === "skill")) return null;
  return found
    .map((item, index) => {
      const lineEnd = preview.indexOf("\n", item.at);
      const start = lineEnd >= 0 ? lineEnd + 1 : item.at + item.mark.length;
      const end = index + 1 < found.length ? found[index + 1].at : preview.length;
      return { key: item.key, body: preview.slice(start, end).trim() };
    })
    .filter((item) => item.body);
}

function DecisionPart({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="pf-decision__part">
      <summary>{title}</summary>
      <div className="pf-decision__part-body">{children}</div>
    </details>
  );
}

/** A review call, opened as separate sections instead of one prompt dump. */
function ReviewCall({ call, reply }: { call: DecisionCall; reply?: string }) {
  const { t } = useI18n();
  const parts = reviewParts(call.user_preview ?? "");
  if (!parts) return <DecisionCallBody call={call} />;
  const time = typeof call.latency_ms === "number" && call.latency_ms > 0 ? `${call.latency_ms} ms` : "";
  const label: Record<(typeof REVIEW_MARKS)[number]["key"], "chat.review.skill" | "chat.review.traveller" | "chat.review.edit" | "chat.review.plan" | "chat.review.pages"> = {
    skill: "chat.review.skill",
    traveller: "chat.review.traveller",
    edit: "chat.review.edit",
    plan: "chat.review.plan",
    pages: "chat.review.pages",
  };
  return (
    <>
      <DecisionPart title={[t("chat.review.model"), call.model, time].filter(Boolean).join(" · ")}>
        <p>{[call.purpose, call.model, time].filter(Boolean).join(" · ")}</p>
      </DecisionPart>
      {parts.map((part) => (
        <DecisionPart key={part.key} title={t(label[part.key])}>
          <pre className="pf-decision__prompt">{part.body}</pre>
        </DecisionPart>
      ))}
      {call.reasoning?.trim() || call.output_preview ? (
        <DecisionPart title={t("chat.review.output")}>
          {call.output_preview ? <pre>{call.output_preview}</pre> : null}
          {call.reasoning?.trim() ? (
            <DecisionPart title={t("chat.review.reasoning")}>
              <p>{call.reasoning}</p>
            </DecisionPart>
          ) : null}
        </DecisionPart>
      ) : null}
      {reply?.trim() ? (
        <DecisionPart title={t("chat.review.reply")}>
          <p>{reply}</p>
        </DecisionPart>
      ) : null}
    </>
  );
}

/** Developer mode: the reasoning for this reply, closed until the admin opens it.
 * Each stage opens on its own. The model call inside a stage opens on its own too. */
function WhyThisDecision({ decision, reply }: { decision: DecisionRecord | null; reply?: string }) {
  const { t } = useI18n();
  const [developer] = useDeveloperMode();
  if (!developer || !decision) return null;
  const sections = decisionSections(decision);
  if (!sections.length) return null;
  const replyOn = [...sections].reverse().find((section) => section.phase === "review")?.id;
  return (
    <details className="pf-decision">
      <summary>{t("chat.thinking")}</summary>
      <div className="pf-decision__body">
        {sections.map((section) => {
          const time = typeof section.seconds === "number" ? `${section.seconds}s` : "";
          const call = section.call;
          const callTime = call && typeof call.latency_ms === "number" && call.latency_ms > 0 ? `${call.latency_ms} ms` : "";
          const structured = section.phase === "review" && call != null && reviewParts(call.user_preview ?? "") != null;
          return (
            <details key={section.id} className="pf-decision__part">
              <summary>
                {section.phase}
                {section.model ? ` · ${section.model}` : ""}
                {time ? ` · ${time}` : ""}
                {section.notes[0] ? ` — ${section.notes[0]}` : ""}
              </summary>
              <div className="pf-decision__part-body">
                {section.notes.map((line) => (
                  <p key={line}>{line}</p>
                ))}
                {structured && call ? (
                  <ReviewCall call={call} reply={section.id === replyOn ? reply : undefined} />
                ) : call ? (
                  <details className="pf-decision__part">
                    <summary>
                      model
                      {call.model ? ` · ${call.model}` : ""}
                      {callTime ? ` · ${callTime}` : ""}
                    </summary>
                    <div className="pf-decision__part-body">
                      <DecisionCallBody call={call} />
                    </div>
                  </details>
                ) : null}
              </div>
            </details>
          );
        })}
      </div>
    </details>
  );
}

/** The names of the files a message was sent with (kept in its `meta`). */
function attachmentNames(message: ChatMessage): string[] {
  const names = message.meta?.attachments;
  return Array.isArray(names) ? names.filter((name): name is string => typeof name === "string") : [];
}

function EditDecision({
  messageId,
  meta,
  busy,
  onDecide,
}: {
  messageId: string;
  meta?: Record<string, unknown>;
  busy: boolean;
  onDecide?: (messageId: string, changeId: string, accept: boolean) => void;
}) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState<string | null>(null);
  const log = meta?.log as { steps?: unknown } | undefined;
  const steps = Array.isArray(log?.steps) ? log.steps.filter((step): step is string => typeof step === "string" && step !== "") : [];
  const proposal = meta?.proposal as { changes?: unknown } | undefined;
  const changes = Array.isArray(proposal?.changes) ? proposal.changes : [];
  if (!steps.length && !changes.length) return null;
  const choose = (changeId: string, accept: boolean) => {
    if (!onDecide || waiting) return;
    setWaiting(changeId);
    Promise.resolve(onDecide(messageId, changeId, accept)).finally(() => setWaiting(null));
  };
  return (
    <div className="pf-edit">
      {steps.length ? (
        <details className="pf-edit__log">
          <summary>{t("chat.why")}</summary>
          <ol>
            {steps.map((step, index) => (
              <li key={`${index}-${step}`}>{step}</li>
            ))}
          </ol>
        </details>
      ) : null}
      {changes.length ? <p className="pf-edit__hint">{t("chat.proposalHint")}</p> : null}
      {changes.map((row) => {
        const change = row as { id?: unknown; label?: unknown; detail?: unknown; status?: unknown };
        const id = typeof change.id === "string" ? change.id : "";
        const status = change.status === "accepted" || change.status === "rejected" ? change.status : "pending";
        if (!id) return null;
        return (
          <div key={id} className="pf-edit__row" data-status={status}>
            <div>
              <div className="pf-edit__label">{typeof change.label === "string" ? change.label : id}</div>
              {typeof change.detail === "string" && change.detail ? (
                <div className="pf-edit__detail">{change.detail}</div>
              ) : null}
              {status !== "pending" ? (
                <div className="pf-edit__detail">{status === "accepted" ? t("chat.accepted") : t("chat.rejected")}</div>
              ) : null}
            </div>
            {status === "pending" ? (
              <div className="pf-edit__actions">
                <IconButton
                  icon="check"
                  label={t("chat.acceptChange")}
                  disabled={busy || waiting !== null}
                  onClick={() => choose(id, true)}
                />
                <IconButton
                  icon="close"
                  label={t("chat.rejectChange")}
                  disabled={busy || waiting !== null}
                  onClick={() => choose(id, false)}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function ChatPanel({
  messages,
  onSend,
  onStop,
  busy,
  agents,
  progress,
  progressMessage,
  clarify,
  clarifyValues,
  onClarifyChange,
  onClarifySubmit,
  scope,
  onClearScope,
  error,
  onRetry,
  onClear,
  onDecide,
  trace,
  live,
  steps = [],
  held = false,
  agentName = "",
  disabled = false,
  finished = false,
  onStartNew,
  tripId = null,
  draft: heldDraft,
  onDraftChange,
  intake = null,
  focusSignal = 0,
  initialFiles,
}: {
  messages: ChatMessage[];
  /** `attachments` are the files chosen in the box, already read; absent when there are none. */
  onSend: (message: string, attachments?: ChatAttachment[]) => void;
  /** Drops the call that is running. A later send starts its own call. */
  onStop?: () => void;
  /** What the last turn read from the links and files of its message. */
  intake?: IntakePayload | null;
  /** Each change of this number puts the cursor in the message box (as when a question on the plan is to be answered). */
  focusSignal?: number;
  /** Files already chosen when the panel opens (for stories and tests). */
  initialFiles?: File[];
  busy: boolean;
  agents: Record<string, AgentState>;
  progress: number;
  progressMessage: string;
  clarify: ClarifyPayload | null;
  clarifyValues: Record<string, string>;
  onClarifyChange: (slot: string, value: string) => void;
  onClarifySubmit: (message?: string) => void;
  scope: string | null;
  onClearScope: () => void;
  error: string | null;
  onRetry?: () => void;
  onClear?: () => void;
  /** Tick or cross one change the assistant offered. Absent in stories. */
  onDecide?: (messageId: string, changeId: string, accept: boolean) => void;
  trace?: RunTrace | null;
  live?: { id: string; title: string; body: string }[];
  /** Stages of this turn, updated as each one starts and finishes. */
  steps?: { id: string; phase: string; model: string; status: "running" | "done"; seconds?: number }[];
  /** A person on the desk has this chat. */
  held?: boolean;
  /** That person's account name. */
  agentName?: string;
  /** No trip is open to send to (the server could not be reached): nothing can be typed or sent. */
  disabled?: boolean;
  /** The plan is final, so the box asks for a new trip instead of a reconnect. */
  finished?: boolean;
  onStartNew?: () => void;
  /** The trip the chat belongs to. A message typed and not sent stays with its trip, not with the next one opened. */
  tripId?: string | null;
  /**
   * The message typed in the box and not sent, and the call that changes it. The page holds the drafts of its trips, so
   * that they outlive the panel; without `onDraftChange` the panel keeps a draft per trip itself.
   */
  draft?: string;
  onDraftChange?: (value: string) => void;
}) {
  const { t } = useI18n();
  const [ownDrafts, setOwnDrafts] = useState<Record<string, string>>({});
  const draftKey = tripId ?? "";
  const draft = onDraftChange ? (heldDraft ?? "") : (ownDrafts[draftKey] ?? "");
  const setDraft = (value: string) =>
    onDraftChange ? onDraftChange(value) : setOwnDrafts((current) => ({ ...current, [draftKey]: value }));
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // The box grows with what is typed in it: it is measured again whenever the draft changes, as when the trip shown
  // changes or a message that was not sent comes back.
  useEffect(() => {
    const box = composerRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 168)}px`;
  }, [draft]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastContent = messages[messages.length - 1]?.content ?? "";
  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, lastContent, busy, clarify?.question, intake, steps.length]);
  useEffect(() => {
    if (focusSignal) composerRef.current?.focus();
  }, [focusSignal]);
  // The files chosen for the next message. They belong to the trip they were chosen in.
  const [files, setFiles] = useState<File[]>(initialFiles ?? []);
  const [fileError, setFileError] = useState<AttachmentRefusal | { reason: "read" } | null>(null);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const shownTrip = useRef(draftKey);
  useEffect(() => {
    if (shownTrip.current === draftKey) return;
    shownTrip.current = draftKey;
    setFiles([]);
    setFileError(null);
  }, [draftKey]);
  const addFiles = (incoming: File[]) => {
    if (!incoming.length || disabled) return;
    const result = addAttachments(files, incoming);
    setFiles(result.files);
    setFileError(result.refusal);
  };
  const megabytes = (bytes: number) => bytes / (1024 * 1024);
  const fileErrorText = !fileError
    ? ""
    : fileError.reason === "type"
      ? t("chat.attach.errorType", { name: fileError.name })
      : fileError.reason === "size"
        ? t("chat.attach.errorSize", { name: fileError.name, mb: megabytes(ATTACHMENT_MAX_BYTES) })
        : fileError.reason === "count"
          ? t("chat.attach.errorCount", { count: ATTACHMENTS_MAX })
          : fileError.reason === "total"
            ? t("chat.attach.errorTotal", { mb: megabytes(ATTACHMENTS_TOTAL_MAX_BYTES) })
            : t("chat.attach.errorRead");
  const composerLocked = disabled || busy;
  const submit = async () => {
    const typed = draft.trim();
    if ((!typed && !files.length) || composerLocked || reading) return;
    if (!files.length) {
      setDraft("");
      onSend(typed);
      return;
    }
    // Sent to the planner, so it stays in English, like the quick prompts.
    const value = typed || "Plan a trip from what I attached.";
    setReading(true);
    setFileError(null);
    try {
      const attachments = await readAttachments(files);
      setDraft("");
      setFiles([]);
      onSend(value, attachments);
    } catch {
      setFileError({ reason: "read" });
    } finally {
      setReading(false);
    }
  };
  const running = Object.values(agents).filter((agent) => agent.status === "running").length;
  const [developer] = useDeveloperMode();
  return (
    <section className="pf-card pf-chat" aria-label={t("chat.label")}>
      <div className="pf-chat__head">
        <div className="pf-row pf-chat__identity">
          <span className="pf-logo__mark" style={{ width: 26, height: 26, background: "var(--p30-tone-brand-primary)" }}>
            <Icon name="sparkles" size={14} />
          </span>
          <div className="pf-stack" style={{ gap: 0 }}>
            <Text size="sm" weight="semibold">
              {t("chat.title")}
            </Text>
            {held ? (
              <Text size="xs" tone="tertiary">
                {t("chat.taken", { name: agentName.trim() || t("chat.liveAgent") })}
              </Text>
            ) : null}
          </div>
        </div>
        <div className="pf-row pf-chat__tools">
          <DeveloperToggle />
          {developer ? (
            <button type="button" className="pf-plan-tab" onClick={() => downloadTurnLog(trace, live, messages)}>
              {t("settings.downloadLog")}
            </button>
          ) : null}
          <Badge tone={busy ? "busy" : "success"} dot>
            {busy ? t("chat.working") : t("chat.ready")}
          </Badge>
          {onClear && messages.length > 0 ? (
            <IconButton
              icon="trash"
              label={t("chat.clear")}
              onClick={() => {
                if (window.confirm(t("chat.clearConfirm"))) onClear();
              }}
            />
          ) : null}
        </div>
      </div>
      {held ? (
        <p className="pf-chat__taken" role="status">{t("chat.taken", { name: agentName.trim() || t("chat.liveAgent") })}</p>
      ) : null}
      <div className="pf-chat__messages pf-scroll" ref={scrollRef}>
        {messages.length === 0 ? (
          <EmptyState
            icon="compass"
            title={t("chat.emptyTitle")}
            body={t("chat.emptyBody")}
          />
        ) : (
          <>
          {messages.map((message) => {
            const lastAssistantId = [...messages].reverse().find((item) => item.role !== "user")?.id;
            const fromOperator = Boolean(message.meta?.operator);
            const fromAgent = fromOperator || (message.role !== "user" && Boolean(message.meta?.live_agent));
            const speaker = fromOperator
              ? (typeof message.meta?.agent_name === "string" && message.meta.agent_name.trim()) || t("chat.liveAgent")
              : message.role === "user"
                ? ""
                : fromAgent
                  ? (typeof message.meta?.agent_name === "string" && message.meta.agent_name.trim()) || t("chat.liveAgent")
                  : t("chat.pathfinder");
            return (
            <div key={message.id} className="pf-turn" data-role={fromOperator || message.role !== "user" ? "assistant" : "user"} data-live={fromAgent || undefined}>
              <ChatBubble
                role={fromOperator || message.role !== "user" ? "assistant" : "user"}
                intent={fromAgent ? undefined : message.intent ?? undefined}
                time={new Date(message.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
                above={
                  speaker || typeof message.meta?.scope === "string" || attachmentNames(message).length ? (
                    <>
                      {speaker ? <span className="pf-speaker" data-live={fromAgent || undefined}>{speaker}</span> : null}
                      {typeof message.meta?.scope === "string" ? (
                        <span className="pf-scope-chip">{String(message.meta.scope)}</span>
                      ) : null}
                      {attachmentNames(message).map((name, index) => (
                        <span className="pf-attachment" key={`${index}-${name}`}>
                          <Icon name="paperclip" size={12} />
                          {name}
                        </span>
                      ))}
                    </>
                  ) : null
                }
                meta={
                  <>
                    {message.meta && typeof message.meta.version === "number" ? (
                      <span>v{String(message.meta.version)}</span>
                    ) : null}
                    {message.meta && typeof message.meta.stuck_flag === "number" ? (
                      <span className="pf-flag" data-flag={message.meta.stuck_flag}>
                        flag {String(message.meta.stuck_flag)}
                      </span>
                    ) : null}
                  </>
                }
              >
                {message.role === "user" && !fromOperator ? maskTyped(message.content) : message.content}
              </ChatBubble>
              {message.role !== "user" && !fromAgent ? (
                <WhyThisDecision
                  reply={message.content}
                  decision={
                    decisionOf(message.meta)
                    ?? (message.id === lastAssistantId ? decisionFromLive(live, trace) : null)
                  }
                />
              ) : null}
              {message.role !== "user" && !fromAgent ? (
                <EditDecision messageId={message.id} meta={message.meta} busy={busy} onDecide={onDecide} />
              ) : null}
            </div>
            );
          })}
          </>
        )}
        {error ? (
          <div className="pf-banner" role="alert">
            <Icon name="alert" size={14} />
            <div className="pf-stack" style={{ gap: 4 }}>
              <span>{error}</span>
              {onRetry ? (
                <Button size="sm" variant="ghost" onClick={onRetry} iconLeft="refresh">
                  {t("common.retry")}
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
        {intake ? <IntakeNotice intake={intake} /> : null}
        {clarify ? (
          <ClarifyCard
            payload={clarify}
            values={clarifyValues}
            onChange={onClarifyChange}
            onSubmit={onClarifySubmit}
            submitting={busy}
          />
        ) : null}
        {busy && steps.length ? (
          <ol className="pf-steps" aria-live="polite">
            {steps.map((step) => (
              <li key={step.id} data-status={step.status}>
                <span>{step.phase}</span>
                <span className="pf-steps__model">{step.model}</span>
                <span className="pf-steps__time">
                  {step.status === "done" && typeof step.seconds === "number" ? `${step.seconds}s` : "…"}
                </span>
              </li>
            ))}
          </ol>
        ) : null}
        {busy ? (
          <div className="pf-thinking" role="status">
            <span className="pf-thinking__dots" aria-hidden="true"><i /><i /><i /></span>
            <Text size="sm">{progressMessage || t("chat.working")}</Text>
          </div>
        ) : null}
        {busy && (running > 0 || progress > 0) ? (
          <AgentActivity agents={agents} progress={progress} message={progressMessage || t("chat.progressFallback")} />
        ) : null}
      </div>
      <div
        className="pf-chat__composer"
        data-dragging={dragging}
        onDragOver={(event) => {
          if (composerLocked || !Array.from(event.dataTransfer.types).includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          setDragging(false);
          if (!event.dataTransfer.files.length) return;
          event.preventDefault();
          addFiles(Array.from(event.dataTransfer.files));
        }}
      >
        {scope ? (
          <span className="pf-scope-chip">
            <Icon name="map" size={12} />
            {t("chat.about", { scope })}
            <button type="button" aria-label={t("chat.clearScope")} onClick={onClearScope} style={{ border: 0, background: "transparent", padding: 0 }}>
              <Icon name="close" size={12} />
            </button>
          </span>
        ) : null}
        <div className="pf-quick">
          {QUICK_PROMPTS.map((prompt) => (
            <button key={prompt} type="button" className="pf-chip" disabled={composerLocked} onClick={() => onSend(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
        {files.length ? (
          <div className="pf-attachments">
            {files.map((file, index) => (
              <span className="pf-attachment" key={`${index}-${file.name}`}>
                <Icon name="paperclip" size={12} />
                {file.name}
                <span className="pf-muted">{fileSizeText(file.size)}</span>
                <button
                  type="button"
                  aria-label={t("chat.attach.remove", { name: file.name })}
                  disabled={reading}
                  onClick={() => {
                    setFiles((current) => current.filter((_, position) => position !== index));
                    setFileError(null);
                  }}
                >
                  <Icon name="close" size={12} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {fileErrorText ? (
          <span className="pf-field__error" role="alert">{fileErrorText}</span>
        ) : null}
        {finished && onStartNew ? (
          <div className="pf-row pf-row--between">
            <Text size="xs" tone="secondary">{t("chat.final")}</Text>
            <Button size="sm" variant="primary" onClick={onStartNew}>{t("chat.startNew")}</Button>
          </div>
        ) : null}
        <div className="pf-composer">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            accept={ATTACHMENT_TYPES.join(",")}
            onChange={(event) => {
              addFiles(Array.from(event.target.files ?? []));
              // The same file can be chosen again after it is removed.
              event.target.value = "";
            }}
          />
          <IconButton
            icon="paperclip"
            label={t("chat.attach")}
            disabled={composerLocked || reading}
            onClick={() => fileInputRef.current?.click()}
          />
          <textarea
            ref={composerRef}
            value={draft}
            rows={1}
            disabled={composerLocked}
            placeholder={finished ? t("chat.final") : disabled ? t("chat.offline") : busy ? t("chat.working") : scope ? t("chat.placeholderScoped") : t("chat.placeholder")}
            aria-label={t("chat.message")}
            onChange={(event) => setDraft(event.target.value)}
            onPaste={(event) => {
              const pasted = Array.from(event.clipboardData.files);
              if (!pasted.length) return;
              event.preventDefault();
              addFiles(pasted);
            }}
            onInput={(event) => {
              const el = event.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 72)}px`;
            }}
            onKeyDown={(event) => {
              // Enter that confirms an input-method candidate (Chinese, Japanese, Korean) is not a send.
              if (event.key === "Enter" && !event.shiftKey && !isImeKey(event)) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <Button
            variant={busy ? "danger" : "primary"}
            data-round="true"
            aria-label={busy ? t("chat.stop") : t("chat.send")}
            disabled={disabled || reading || (!busy && !draft.trim() && !files.length)}
            onClick={() => {
              if (busy) onStop?.();
              else void submit();
            }}
          >
            <Icon name={busy ? "stop" : "send"} size={16} />
          </Button>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ workspace -- */

function Collapsible({
  title,
  icon,
  badge,
  children,
  defaultOpen = false,
  id,
  openToken = 0,
}: {
  title: string;
  icon: "wallet" | "check" | "ticket" | "alert" | "info" | "hotel" | "route";
  badge?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  id?: string;
  /** Bump this to open the section from a badge. */
  openToken?: number;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (openToken) setOpen(true);
  }, [openToken]);
  return (
    <div className="pf-collapsible" id={id}>
      <button type="button" className="pf-collapsible__head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="pf-row" style={{ gap: 7 }}>
          <Icon name={icon} size={14} />
          {title}
          {badge}
        </span>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
      </button>
      {open ? <div className="pf-collapsible__body">{children}</div> : null}
    </div>
  );
}

/** A pending request on one button and the reason it last failed, for the option cards. */
function useOptionAction() {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<{ key: string; reason: string } | null>(null);
  const run = async (key: string, action: () => Promise<unknown>) => {
    setPending(key);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError({ key, reason: failureReason(cause) });
    } finally {
      setPending(null);
    }
  };
  return { pending, error, run };
}

function moneyText(money: { amount: number; currency: string } | null | undefined): string {
  return money ? `${money.currency} ${money.amount.toLocaleString()}` : "";
}

const AMENITIES_SHOWN = 4;

const STAY_VERDICT: Record<StayMatch["verdict"], { label: MessageKey; tone: "success" | "warning" | "neutral" | "danger" }> = {
  meets: { label: "options.match.meets", tone: "success" },
  partly: { label: "options.match.partly", tone: "warning" },
  unclear: { label: "options.match.unclear", tone: "neutral" },
  fails: { label: "options.match.fails", tone: "danger" },
};

/** How far a stay meets the traveller's stay wishes: the verdict, the wish, the reason and what the sources showed. */
function StayMatchNote({ match }: { match: StayMatch }) {
  const { t } = useI18n();
  const verdict = STAY_VERDICT[match.verdict] ?? STAY_VERDICT.unclear;
  const wishes = (match.wishes ?? []).filter(Boolean);
  const evidence = (match.evidence ?? []).filter(Boolean);
  const refs = (match.evidence_refs ?? []).filter((ref) => ref.title || ref.url);
  return (
    <div className="pf-stay-match">
      <div className="pf-row pf-row--wrap" style={{ gap: 6 }}>
        <Badge tone={verdict.tone} dot>{t(verdict.label)}</Badge>
        {wishes.length ? <span className="pf-muted">{t("options.match.wish", { wish: wishes.join("; ") })}</span> : null}
      </div>
      {match.reason ? <span>{match.reason}</span> : null}
      {evidence.length || refs.length ? (
        <ul className="pf-stay-match__evidence">
          {evidence.map((line, index) => (
            <li key={`e-${index}`}>{line}</li>
          ))}
          {refs.map((ref, index) => {
            const href = safeHref(ref.url);
            const title = ref.title || ref.source || ref.url;
            return (
              <li key={`r-${index}`}>
                {href ? (
                  <a href={href} target="_blank" rel="noreferrer noopener">{title}</a>
                ) : (
                  title
                )}
                {ref.source && ref.source !== title ? <span className="pf-muted"> · {ref.source}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/** One stay in the hotel comparison. Every line is hidden when the source gave nothing for it. */
function HotelOptionCard({
  hotel,
  chosen,
  stale = false,
  onChoose,
  onLoadRooms,
}: {
  hotel: HotelStay;
  chosen: boolean;
  /** The source priced (and listed rooms for) other dates than the stay's own. */
  stale?: boolean;
  onChoose?: () => Promise<void>;
  onLoadRooms?: () => Promise<RoomType[]>;
}) {
  const { t } = useI18n();
  const { pending, error, run } = useOptionAction();
  const [allAmenities, setAllAmenities] = useState(false);
  const [rooms, setRooms] = useState<RoomType[] | null>(null);
  const [roomsOpen, setRoomsOpen] = useState(false);
  const amenities = hotel.amenities ?? [];
  const shownAmenities = allAmenities ? amenities : amenities.slice(0, AMENITIES_SHOWN);
  const nearby = (hotel.nearby ?? []).filter((place) => place.name);
  const knownRooms = rooms ?? (!stale && hotel.room_types?.length ? hotel.room_types : null);
  const location = [
    hotel.area,
    typeof hotel.distance_to_centre_km === "number" ? t("workspace.kmToCentre", { km: hotel.distance_to_centre_km }) : "",
    typeof hotel.nearest_stop_km === "number" && hotel.nearest_stop_name
      ? t("options.nearestStop", { km: hotel.nearest_stop_km, stop: hotel.nearest_stop_name })
      : "",
  ].filter(Boolean);
  const quality = [
    typeof hotel.rating === "number"
      ? `\u2605 ${hotel.rating.toFixed(1)}${
          typeof hotel.review_count === "number" ? ` ${t("options.reviews", { count: hotel.review_count.toLocaleString() })}` : ""
        }`
      : "",
    typeof hotel.hotel_class === "number" && hotel.hotel_class > 0 ? t("options.stars", { n: hotel.hotel_class }) : "",
  ].filter(Boolean);

  const toggleRooms = () => {
    if (roomsOpen) {
      setRoomsOpen(false);
      return;
    }
    if (knownRooms || !onLoadRooms) {
      setRoomsOpen(true);
      return;
    }
    void run("rooms", async () => {
      const loaded = await onLoadRooms();
      setRooms(loaded);
      setRoomsOpen(true);
    });
  };

  return (
    <div className="pf-option-card" data-chosen={chosen}>
      <div className="pf-row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <Text size="sm" weight="semibold">{hotel.name}</Text>
        {chosen ? <Badge tone="brand" dot>{t("options.chosen")}</Badge> : null}
      </div>
      <PlaceImage image={hotel.image} />
      {stale ? (
        <Text size="sm" tone="secondary">{t("options.priceNotChecked")}</Text>
      ) : hotel.price_per_night ? (
        <Text size="sm" weight="semibold">
          {t("workspace.perNight", { price: moneyText(hotel.price_per_night) })}
          {hotel.price_kind !== "none" ? <span className="pf-muted pf-small"> · {hotel.price_kind}</span> : null}
        </Text>
      ) : null}
      {location.length ? <Text size="xs" tone="secondary">{location.join(" · ")}</Text> : null}
      {quality.length ? <Text size="xs" tone="secondary">{quality.join(" · ")}</Text> : null}
      {hotel.match ? <StayMatchNote match={hotel.match} /> : null}
      {amenities.length ? (
        <div className="pf-option-card__chips">
          {shownAmenities.map((amenity) => (
            <Badge key={amenity} tone="neutral">{amenity}</Badge>
          ))}
          {amenities.length > AMENITIES_SHOWN ? (
            <button
              type="button"
              className="pf-alts__toggle"
              aria-expanded={allAmenities}
              onClick={() => setAllAmenities(!allAmenities)}
            >
              {allAmenities ? t("options.amenitiesLess") : t("options.amenitiesMore", { count: amenities.length - AMENITIES_SHOWN })}
            </button>
          ) : null}
        </div>
      ) : null}
      {nearby.length ? (
        <div className="pf-stack" style={{ gap: 1 }}>
          <span className="pf-muted pf-small">{t("options.nearby")}</span>
          {nearby.slice(0, 3).map((place) => (
            <span className="pf-small" key={`${place.name}-${place.duration}`}>
              {place.name}
              {place.duration ? <span className="pf-muted"> · {place.duration}</span> : null}
            </span>
          ))}
        </div>
      ) : null}
      {error ? (
        <span className="pf-field__error" role="alert">
          {error.key === "rooms" ? t("options.roomsFailed", { reason: error.reason }) : t("options.failed", { reason: error.reason })}
        </span>
      ) : null}
      <RankNote rank={hotel.rank} reasons={hotel.reasons} />
      <LinkRow links={hotel.links} />
      <div className="pf-option-card__actions">
        {!chosen && onChoose ? (
          <Button
            size="sm"
            variant="primary"
            loading={pending === "choose"}
            disabled={pending !== null && pending !== "choose"}
            onClick={() => void run("choose", onChoose)}
          >
            {t("options.chooseHotel")}
          </Button>
        ) : null}
        {knownRooms || onLoadRooms ? (
          <Button
            size="sm"
            variant="ghost"
            iconLeft="hotel"
            aria-expanded={roomsOpen}
            loading={pending === "rooms"}
            disabled={pending !== null && pending !== "rooms"}
            onClick={toggleRooms}
          >
            {roomsOpen ? t("options.hideRooms") : t("options.roomTypes")}
          </Button>
        ) : null}
      </div>
      {roomsOpen ? (
        knownRooms && knownRooms.length ? (
          <ul className="pf-option-card__rooms">
            {knownRooms.map((room, index) => {
              const href = safeHref(room.link);
              return (
                <li key={`${room.name}-${index}`}>
                  <span>{room.name}</span>
                  {room.price_per_night ? <span className="pf-muted"> · {t("workspace.perNight", { price: moneyText(room.price_per_night) })}</span> : null}
                  {href ? (
                    <>
                      {" · "}
                      <a href={href} target="_blank" rel="noreferrer">{room.source || t("workspace.checkHotel")}</a>
                    </>
                  ) : room.source ? (
                    <span className="pf-muted"> · {room.source}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <Text size="xs" tone="tertiary">{t("options.noRooms")}</Text>
        )
      ) : null}
    </div>
  );
}

/** The stays the hotel source returned, compared side by side (stacked on a phone); the first of `plan.hotels` is chosen. */
export function HotelOptions({
  options,
  chosenId,
  current = null,
  onChoose,
  onLoadRooms,
}: {
  options: HotelStay[];
  chosenId: string | null;
  /** The stay as it stands; an option found for other dates shows no price for these ones. */
  current?: HotelStay | null;
  onChoose?: (hotelId: string) => Promise<void>;
  onLoadRooms?: (hotelId: string) => Promise<RoomType[]>;
}) {
  if (!options.length) return null;
  // The stay itself leads, as it stands now (an edit may have moved it without its options).
  const shown = current ? [current, ...options.filter((hotel) => hotel.id !== current.id)] : options;
  return (
    <div className="pf-option-grid">
      {shown.map((hotel) => (
        <HotelOptionCard
          key={hotel.id}
          hotel={hotel}
          chosen={hotel.id === chosenId}
          stale={current !== null && (hotel.check_in !== current.check_in || hotel.check_out !== current.check_out)}
          onChoose={onChoose ? () => onChoose(hotel.id) : undefined}
          onLoadRooms={onLoadRooms ? () => onLoadRooms(hotel.id) : undefined}
        />
      ))}
    </div>
  );
}

function stamp(value: string | null | undefined): string {
  return value ? value.replace("T", " ").slice(0, 16) : "";
}

function durationText(minutes: number | null | undefined, t: Translate): string {
  if (typeof minutes !== "number" || minutes <= 0) return "";
  return t("options.duration", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

/** One fare: the flight (airline and number), times, duration and price; the selected one is marked. */
export function TicketCard({
  ticket,
  selected,
  choosable,
  onChoose,
  side = "both",
}: {
  ticket: TicketOption;
  selected: boolean;
  /** More than one option exists, so the card shows whether it is the one chosen. */
  choosable: boolean;
  onChoose?: () => Promise<void>;
  /** ``out`` is only the flight there. ``return`` is only the flight home. ``both`` shows the round trip. */
  side?: "both" | "out" | "return";
}) {
  const { t } = useI18n();
  const { pending, error, run } = useOptionAction();
  const depart = stamp(ticket.depart_at);
  const arrive = stamp(ticket.arrive_at);
  // The arrival time alone when it lands the same day.
  const arriveShown = arrive && depart && arrive.slice(0, 10) === depart.slice(0, 10) ? arrive.slice(11) : arrive;
  const duration = durationText(ticket.duration_minutes, t);
  const returnDuration = durationText(ticket.return_duration_minutes, t);
  const operator = ticket.operator && !ticket.name.includes(ticket.operator) ? ticket.operator : "";
  const estimated = Boolean(ticket.estimated);
  // An estimate has no timetable: its times are "about" times, and it says so.
  const clock = (value: string | null | undefined) => (value ? t("options.aboutTime", { time: stamp(value).slice(11) }) : "");
  const icon = ticket.mode === "flight" ? "plane" : ticket.mode === "drive" ? "car" : "train";
  return (
    <div className="pf-ticket-card" data-selected={choosable && selected} data-estimated={estimated}>
      <span className="pf-ticket-card__thumb"><Icon name={icon} size={20} /></span>
      <div className="pf-stack pf-grow" style={{ gap: 2 }}>
        <Text size="sm" weight="semibold">
          {side === "return" && ticket.return_airline
            ? `${ticket.return_airline} ${ticket.return_flight_number ?? ""}`.trim()
            : ticket.name}
          {side !== "return" && operator ? <span className="pf-muted pf-small"> · {operator}</span> : null}
        </Text>
        {side === "return" ? (
          <Text size="xs" tone="secondary">
            <strong>{t("workspace.return")}</strong>
            {` · ${ticket.return_origin || ticket.destination} → ${ticket.return_destination || ticket.origin}`}
            {ticket.return_depart_at ? ` · ${stamp(ticket.return_depart_at)}` : ""}
            {ticket.return_arrive_at ? ` · ${t("options.arrives", { time: stamp(ticket.return_arrive_at).slice(11) })}` : ""}
            {returnDuration ? ` · ${returnDuration}` : ""}
          </Text>
        ) : null}
        {side !== "return" ? (
        <Text size="xs" tone="secondary">
          <strong>{t("workspace.outbound")}</strong>
          {` · ${ticket.origin} → ${ticket.destination}`}
          {estimated
            ? ` · ${[clock(ticket.depart_at), clock(ticket.arrive_at)].filter(Boolean).join(" – ")} (${t("options.estimate")})`
            : `${depart ? ` · ${depart}` : ""}${arriveShown ? ` · ${t("options.arrives", { time: arriveShown })}` : ""}`}
          {duration ? ` · ${duration}` : ""}
        </Text>
        ) : null}
        {side === "both" && ticket.mode === "flight" ? (
          ticket.return_depart_at ? (
            <Text size="xs" tone="secondary">
              <strong>{t("workspace.return")}</strong>
              {ticket.return_airline ? ` · ${ticket.return_airline} ${ticket.return_flight_number ?? ""}` : ""}
              {` · ${ticket.return_origin} → ${ticket.return_destination} · ${stamp(ticket.return_depart_at)}`}
              {ticket.return_arrive_at ? ` · ${t("options.arrives", { time: stamp(ticket.return_arrive_at).slice(11) })}` : ""}
              {returnDuration ? ` · ${returnDuration}` : ""}
            </Text>
          ) : (
            <Text size="xs" tone="tertiary">{t("workspace.returnMissing")}</Text>
          )
        ) : null}
        <Text size="sm" weight="semibold">
          {ticket.price ? `${moneyText(ticket.price)} · ${ticket.price_kind}` : estimated ? t("options.estimatedNote") : t("workspace.noFare")}
        </Text>
        {ticket.booking_url ? (
          <a href={ticket.booking_url} target="_blank" rel="noopener noreferrer">{t("workspace.book")}</a>
        ) : null}
        <RankNote rank={ticket.rank} reasons={ticket.reasons} />
        <LinkRow links={ticket.links} />
        {error ? <span className="pf-field__error" role="alert">{t("options.failed", { reason: error.reason })}</span> : null}
      </div>
      <div className="pf-stack" style={{ alignItems: "flex-end", gap: 6 }}>
        {choosable && selected ? <Badge tone="brand" dot>{t("options.selected")}</Badge> : null}
        <StatusPill status={ticket.data_status} />
        {choosable && !selected && onChoose ? (
          <Button size="sm" variant="secondary" loading={pending === "choose"} onClick={() => void run("choose", onChoose)}>
            {ticket.mode === "flight" ? t("options.chooseFlight") : t("options.chooseOption")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** The chosen stay of a city (or of the trip): the card the traveller can select, with its link to the hotel. */
function StayCard({
  hotel,
  changed,
  selected,
  onSelect,
}: {
  hotel: HotelStay;
  changed: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  const bookingHref = safeHref(hotel.booking_url);
  return (
    <div
      className="pf-hotel-card"
      data-updated={changed}
      data-selected={selected}
      role="button"
      tabIndex={0}
      title={t("workspace.hotelSelect")}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onSelect();
      }}
    >
      {hotel.image ? <PlaceImage image={hotel.image} compact /> : <span className="pf-hotel-card__thumb"><Icon name="hotel" size={20} /></span>}
      <div className="pf-stack pf-grow" style={{ gap: 2 }}>
        <Text size="sm" weight="semibold">
          {hotel.name}
          {changed ? <Badge tone="success" dot>{t("common.updated")}</Badge> : null}
        </Text>
        <Text size="xs" tone="tertiary">
          {hotel.area || t("workspace.noArea")} · {hotel.rating ? `${hotel.rating} / 5` : t("workspace.noRating")}
          {hotel.distance_to_centre_km !== null ? ` · ${t("workspace.kmToCentre", { km: hotel.distance_to_centre_km })}` : ""}
        </Text>
        <Text size="xs" tone="secondary">
          {hotel.price_per_night
            ? `${t("workspace.perNight", { price: `${hotel.price_per_night.currency} ${hotel.price_per_night.amount.toLocaleString()}` })} · ${hotel.price_kind}`
            : t("workspace.noRate")}
        </Text>
        <RankNote rank={hotel.rank} reasons={hotel.reasons} />
      </div>
      <div className="pf-stack" style={{ alignItems: "flex-end", gap: 4 }}>
        <StatusPill status={hotel.data_status} />
        {hotel.links?.length ? (
          <LinkRow links={hotel.links} />
        ) : bookingHref ? (
          <a className="pf-small" href={bookingHref} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>{t("workspace.checkHotel")}</a>
        ) : null}
      </div>
    </div>
  );
}

/**
 * A stay or a leg the traveller picks one option for, and then locks in.
 *
 * Open: every option is shown (per tab when there are several kinds), the suggested one marked, each with a Lock in
 * button. Locked: only the chosen option, with a Change button that brings the others back. With no handlers (an older
 * version is being read) the chosen option is shown with a count of the others and no buttons.
 */
export function ChoiceSlot<T extends { id: string }>({
  title,
  icon,
  state,
  chosen,
  options,
  renderOption,
  tabs,
  emptyText,
  busy = false,
  changed = false,
  error,
  onLock,
  onReopen,
  booked = false,
  onBooked,
  fold = false,
}: {
  title: ReactNode;
  icon: IconName;
  state: "locked" | "open" | "single" | "empty";
  chosen: T | null;
  /** Every option, the chosen one included. */
  options: T[];
  renderOption: (option: T, role: "chosen" | "option") => ReactNode;
  /** When more than one, the options are shown per tab. */
  tabs?: { label: string; options: T[] }[];
  emptyText: ReactNode;
  busy?: boolean;
  changed?: boolean;
  /** Why the last Lock in or Change failed. */
  error?: string;
  onLock?: (optionId: string) => void;
  onReopen?: () => void;
  /** The traveller marked the locked choice as booked: Change is hidden until it is marked not booked. */
  booked?: boolean;
  onBooked?: (booked: boolean) => void;
  /** Show the chosen option, and the rest behind "N other options", as a meal does. */
  fold?: boolean;
}) {
  const { t } = useI18n();
  const [foldedOpen, setFoldedOpen] = useState(false);
  const foldedId = useId();
  const groups = tabs && tabs.length > 1 ? tabs : null;
  const holding = groups ? Math.max(0, groups.findIndex((group) => group.options.some((option) => option.id === chosen?.id))) : 0;
  const [picked, setPicked] = useState<string | null>(null);
  const activeTab = groups ? (groups.find((group) => group.label === picked) ?? groups[holding]) : null;
  const shown = activeTab ? activeTab.options : options;
  const interactive = Boolean(onLock || onReopen);
  const others = options.filter((option) => option.id !== chosen?.id).length;
  const badge =
    state === "locked" ? (
      <Badge tone="success" dot>{t("choice.locked")}</Badge>
    ) : state === "open" && interactive ? (
      <Badge tone="brand" dot>{t("choice.chooseOne")}</Badge>
    ) : null;

  const lockButton = (option: T) =>
    onLock ? (
      <Button size="sm" variant={option.id === chosen?.id ? "primary" : "secondary"} iconLeft="lock" disabled={busy} loading={busy} onClick={() => onLock(option.id)}>
        {t("choice.lockIn")}
      </Button>
    ) : null;

  let body: ReactNode;
  if (state === "empty" || (!chosen && !options.length)) {
    body = <Text size="xs" tone="tertiary">{emptyText}</Text>;
  } else if (state === "locked" || !interactive) {
    const one = chosen ?? options[0];
    body = (
      <>
        {one ? renderOption(one, "chosen") : null}
        {state !== "locked" && others > 0 ? (
          <Text size="xs" tone="tertiary">{t("choice.others", { count: others })}</Text>
        ) : null}
      </>
    );
  } else if (state === "single") {
    const one = options[0] ?? chosen;
    body = one ? (
      <div className="pf-choice__option" data-suggested="true">
        {renderOption(one, "chosen")}
        <div className="pf-choice__row">
          <Text size="xs" tone="tertiary">{t("choice.onlyOne")}</Text>
          {lockButton(one)}
        </div>
      </div>
    ) : null;
  } else if (fold && chosen) {
    const rest = options.filter((option) => option.id !== chosen.id);
    body = (
      <>
        {renderOption(chosen, "chosen")}
        <div className="pf-choice__row">
          {lockButton(chosen)}
        </div>
        {rest.length ? (
          <div className="pf-alts">
            <button
              type="button"
              className="pf-alts__toggle"
              aria-expanded={foldedOpen}
              aria-controls={foldedId}
              onClick={() => setFoldedOpen(!foldedOpen)}
            >
              <Icon name={foldedOpen ? "chevronDown" : "chevronRight"} size={12} />
              {rest.length === 1 ? t("options.otherOne") : t("options.other", { count: rest.length })}
            </button>
            {foldedOpen ? (
              <div className="pf-alts__body" id={foldedId}>
                {rest.map((option) => (
                  <div className="pf-alt-card" key={option.id}>
                    <div className="pf-grow">{renderOption(option, "option")}</div>
                    {lockButton(option)}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </>
    );
  } else {
    body = (
      <>
        {groups ? (
          <div className="pf-choice__tabs" role="tablist" aria-label={typeof title === "string" ? title : undefined}>
            {groups.map((group) => (
              <button
                key={group.label}
                type="button"
                role="tab"
                className="pf-choice__tab"
                aria-selected={activeTab?.label === group.label}
                aria-pressed={activeTab?.label === group.label}
                onClick={() => setPicked(group.label)}
              >
                {group.label}
                <span className="pf-muted"> {group.options.length}</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="pf-stack" style={{ gap: 8 }} role={groups ? "tabpanel" : undefined}>
          {shown.map((option) => {
            const suggested = option.id === chosen?.id;
            return (
              <div className="pf-choice__option" key={option.id} data-suggested={suggested}>
                {renderOption(option, suggested ? "chosen" : "option")}
                <div className="pf-choice__row">
                  {suggested ? <Badge tone="brand">{t("choice.suggested")}</Badge> : <span />}
                  {lockButton(option)}
                </div>
              </div>
            );
          })}
        </div>
      </>
    );
  }

  return (
    <section className="pf-choice" data-state={state} data-updated={changed} data-choice-state={state} aria-label={typeof title === "string" ? title : undefined}>
      <div className="pf-choice__head">
        <Icon name={icon} size={15} />
        <Text size="sm" weight="semibold">{title}</Text>
        {changed ? <Badge tone="success" dot>{t("common.updated")}</Badge> : null}
        {badge}
        {state === "locked" && booked ? <Badge tone="success">{t("choice.booked")}</Badge> : null}
        {state === "locked" && onBooked ? (
          <Button size="sm" variant="ghost" aria-pressed={booked} disabled={busy} loading={busy} onClick={() => onBooked(!booked)}>
            {booked ? t("choice.markNotBooked") : t("choice.markBooked")}
          </Button>
        ) : null}
        {state === "locked" && onReopen && !booked ? (
          <Button size="sm" variant="ghost" disabled={busy} loading={busy} onClick={onReopen}>
            {t("choice.change")}
          </Button>
        ) : null}
      </div>
      {body}
      {error ? <span className="pf-field__error" role="alert">{t("options.failed", { reason: error })}</span> : null}
    </section>
  );
}

/** What an action on a slot is doing (its buttons are disabled meanwhile) and why the last one failed. */
function useSlotActions() {
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const act = (slot: string, action: () => Promise<void>) => {
    setBusy((current) => new Set(current).add(slot));
    setErrors((current) => ({ ...current, [slot]: "" }));
    action()
      .catch((cause) => setErrors((current) => ({ ...current, [slot]: failureReason(cause) })))
      .finally(() =>
        setBusy((current) => {
          const next = new Set(current);
          next.delete(slot);
          return next;
        }),
      );
  };
  return { busy, errors, act };
}

/** A stay's choice slot: the stay as it stands (selectable for chat) and the other stays found for its city. */
function StaySlot({
  plan,
  stay,
  title,
  changed,
  selected,
  busy,
  error,
  onSelect,
  onLock,
  onReopen,
  onBooked,
  onLoadRooms,
}: {
  plan: TripPlan;
  stay: HotelStay;
  title: string;
  changed: boolean;
  selected: boolean;
  busy: boolean;
  error?: string;
  onSelect: () => void;
  onLock?: (optionId: string) => void;
  onReopen?: () => void;
  onBooked?: (booked: boolean) => void;
  onLoadRooms?: (hotelId: string) => Promise<RoomType[]>;
}) {
  const { t } = useI18n();
  const options = optionsOfStay(plan, stay);
  return (
    <ChoiceSlot
      title={title}
      icon="hotel"
      state={slotState(Boolean(stay.locked), options.length)}
      chosen={stay}
      options={options}
      busy={busy}
      changed={changed}
      error={error}
      emptyText={t("city.noStay", { city: "" }).trim()}
      onLock={onLock}
      onReopen={onReopen}
      booked={Boolean(stay.booked)}
      onBooked={onBooked}
      renderOption={(option) =>
        option.id === stay.id ? (
          <StayCard hotel={stay} changed={false} selected={selected} onSelect={onSelect} />
        ) : (
          <HotelOptionCard
            hotel={option}
            chosen={false}
            stale={option.check_in !== stay.check_in || option.check_out !== stay.check_out}
            onLoadRooms={onLoadRooms ? () => onLoadRooms(option.id) : undefined}
          />
        )
      }
    />
  );
}

/** A leg's (or an airport transfer's) choice slot: one tab per kind of transport that has options. */
function LegSlot({
  plan,
  leg,
  title,
  icon,
  changed,
  busy,
  error,
  onLock,
  onReopen,
  onBooked,
}: {
  plan: TripPlan;
  leg: TransportLeg;
  title: string;
  icon: IconName;
  changed: boolean;
  busy: boolean;
  error?: string;
  onLock?: (optionId: string) => void;
  onReopen?: () => void;
  onBooked?: (booked: boolean) => void;
}) {
  const { t } = useI18n();
  const options = optionsFor(plan, leg.id);
  const chosen = chosenFor(plan, leg.id) ?? null;
  const flight = options.some((option) => option.mode === "flight") || leg.kind === "outbound" || leg.kind === "return";
  const directions =
    leg.from_place && leg.to_place
      ? `https://www.google.com/maps/dir/?${new URLSearchParams({ api: "1", origin: leg.from_place, destination: leg.to_place, travelmode: "transit" }).toString()}`
      : "";
  return (
    <ChoiceSlot
      title={title}
      icon={icon}
      state={slotState(Boolean(leg.locked), options.length)}
      chosen={chosen}
      options={options}
      tabs={transportKinds(options).map((group) => ({ label: t(`choice.kind.${group.kind}` as MessageKey), options: group.options }))}
      busy={busy}
      changed={changed}
      error={error}
      onLock={onLock}
      onReopen={onReopen}
      booked={Boolean(leg.booked)}
      onBooked={onBooked}
      emptyText={
        <>
          {t("choice.noLegOptions")}
          {directions ? (
            <>
              {" "}
              <a href={directions} target="_blank" rel="noopener noreferrer">{t("choice.directions")}</a>
            </>
          ) : null}
        </>
      }
      renderOption={(option) => (
        <TicketCard ticket={option} selected={false} choosable={false} side={leg.kind === "return" ? "return" : flight ? "out" : "both"} />
      )}
      fold={flight}
    />
  );
}

/** The box that actually scrolls this node: the plan list inside a window, or the window itself. */
function scrollPort(node: HTMLElement | null): HTMLElement | null {
  let current: HTMLElement | null = node;
  while (current && current !== document.documentElement) {
    const y = getComputedStyle(current).overflowY;
    if ((y === "auto" || y === "scroll") && current.scrollHeight > current.clientHeight + 4) return current;
    current = current.parentElement;
  }
  return null;
}

/** Leave the pinned Trip map / Detail view / Direct edit bar clear of the target. */
function scrollTopBelowBar(scroller: HTMLElement, node: HTMLElement): number {
  const bar = scroller.querySelector<HTMLElement>(".pf-view-switch");
  const cover = bar && getComputedStyle(bar).position === "sticky" ? bar.getBoundingClientRect().height : 0;
  return node.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - cover - 8;
}

type PlanView = "map" | "detail" | "edit";

function stopOffers(messages: ChatMessage[]): StopOffer[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const raw = (messages[index].meta?.proposal as { changes?: unknown } | undefined)?.changes;
    if (!Array.isArray(raw) || !raw.length) continue;
    const found: StopOffer[] = [];
    for (const row of raw) {
      const change = row as { id?: unknown; status?: unknown; puts?: unknown; drops?: unknown };
      if (change.status === "accepted" || change.status === "rejected") continue;
      const changeId = typeof change.id === "string" ? change.id : "";
      const head = changeId.split("-")[0];
      const puts = Array.isArray(change.puts) ? change.puts : [];
      const drops = Array.isArray(change.drops) ? change.drops.map(String) : [];
      if (!changeId) continue;
      if (head === "drop" || head === "replace") {
        for (const itemId of drops) {
          found.push({ messageId: messages[index].id, changeId, kind: "drop", day: 0, itemId });
        }
      }
      for (const put of puts) {
        const entry = put as { day?: unknown; item?: PlanItem };
        const item = entry.item;
        if (!item || typeof item.id !== "string") continue;
        const kind = head === "add" || head === "replace" ? "add" : "time";
        if (kind !== "add" && head !== "time" && head !== "move" && head !== "rename") continue;
        found.push({
          messageId: messages[index].id,
          changeId,
          kind,
          day: Number(entry.day) || 0,
          itemId: item.id,
          item,
        });
      }
    }
    if (found.length) return found;
  }
  return [];
}

export function TripWorkspace({
  plan,
  versions,
  activeVersion,
  onVersionChange,
  onRestoreVersion,
  selectedItemId,
  onSelectItem,
  finalized = false,
  onFinalise,
  onUseful,
  onNotUseful,
  onStars,
  feedbackStars,
  feedbackVerdict,
  feedbackAdded = [],
  trace,
  onRefresh,
  onFocusDay,
  focusedDay,
  map = null,
  updatedIds,
  changedStayIds,
  changedLegIds,
  languageNotice = null,
  onChooseOption,
  onLockChoice,
  onReopenChoice,
  onMarkBooked,
  onLoadHotelRooms,
  onAnswerQuestion,
  onApplyLayout,
  held = false,
  agentName = "",
  paused = false,
  pausedNote = "",
  onCommitStop,
  messages = [],
  onDecide,
}: {
  plan: TripPlan;
  /** Takes the traveller to the chat box to answer the question the plan's assessment asks. */
  onAnswerQuestion?: () => void;
  /** Swaps in another option shown on the plan (no model call); resolves once the updated plan is shown, rejects with the reason. */
  onChooseOption?: (kind: ChoiceKind, targetId: string | null, optionId: string) => Promise<void>;
  /** Chooses this option for a stay or leg and locks it in. */
  onLockChoice?: (kind: "hotel" | "transport", targetId: string, optionId: string) => Promise<void>;
  /** Unlocks a stay or leg so its options come back. */
  onReopenChoice?: (kind: "hotel" | "transport", targetId: string) => Promise<void>;
  /** Marks a locked stay or leg as booked by the traveller, or not booked. */
  onMarkBooked?: (kind: "hotel" | "transport", targetId: string, booked: boolean) => Promise<void>;
  /** The room types the hotel source lists for a stay. */
  onLoadHotelRooms?: (hotelId: string) => Promise<RoomType[]>;
  /** Write the direct-edit grid onto the latest plan. */
  onApplyLayout?: (items: LayoutMove[], drops?: string[]) => Promise<void>;
  /** While an operator holds the chat, the traveller cannot edit this plan. */
  held?: boolean;
  agentName?: string;
  /** Pathfinder is writing this plan, so direct edit waits. */
  paused?: boolean;
  pausedNote?: string;
  onCommitStop?: (change: StopCommit) => Promise<void>;
  /** Chat turns, so a held edit can be drawn on the plan. */
  messages?: ChatMessage[];
  onDecide?: (messageId: string, changeId: string, accept: boolean) => void;
  /** The disclaimer and translation notice shown at the top of a non-English plan view. */
  languageNotice?: ReactNode;
  versions: { version: number; change_summary: string; created_at: string }[];
  activeVersion: number;
  onVersionChange: (version: number) => void;
  /** Copy the version on screen forward so the next message edits it. */
  onRestoreVersion?: (version: number) => void;
  selectedItemId: string | null;
  onSelectItem: (item: PlanItem | null) => void;
  finalized?: boolean;
  onFinalise?: () => void;
  onUseful: () => void;
  onNotUseful: () => void;
  feedbackStars: number | null;
  feedbackVerdict: "useful" | "not_useful" | null;
  feedbackAdded?: string[];
  onStars?: (stars: number) => void;
  trace?: RunTrace | null;
  onRefresh?: () => void;
  onFocusDay?: (date: string) => void;
  /** The day chosen on the left rail. The workspace follows it. */
  focusedDay?: string;
  /** Shown when the middle view is the map. */
  map?: ReactNode;
  updatedIds?: string[];
  /** The stays and legs (by id) the last change touched. */
  changedStayIds?: ReadonlySet<string>;
  changedLegIds?: ReadonlySet<string>;
}) {
  const { t } = useI18n();
  const [activeDay, setActiveDay] = useState(plan.days[0]?.date ?? "");
  const [view, setView] = useState<PlanView>("detail");
  // The desk studio is the editor while it is open. This one would cover the same screen.
  useEffect(() => {
    const onDeskStudio = (event: Event) => {
      if ((event as CustomEvent<{ open?: boolean }>).detail?.open) setView("detail");
    };
    window.addEventListener("pf-desk-studio", onDeskStudio);
    return () => window.removeEventListener("pf-desk-studio", onDeskStudio);
  }, []);
  const [hideBudget, setHideBudget] = useState(false);
  const [openToken, setOpenToken] = useState<{ which: "constraints" | "gaps" | ""; n: number }>({ which: "", n: 0 });
  const [savingSheet, setSavingSheet] = useState(false);
  const dayRefs = useRef<Record<string, HTMLElement | null>>({});
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const activeRef = useRef(activeDay);
  activeRef.current = activeDay;
  useEffect(() => {
    if (!focusedDay || !plan.days.some((day) => day.date === focusedDay)) return;
    if (focusedDay === activeRef.current) return;
    activeRef.current = focusedDay;
    setActiveDay(focusedDay);
  }, [focusedDay, plan]);
  const hotel = plan.hotels[0];
  const multi = segmentsOf(plan).length > 1;
  const anyStayChanged = (changedStayIds?.size ?? 0) > 0;
  const unknownGaps = plan.data_quality.gaps.length;
  const slotActions = useSlotActions();
  // Options are chosen on the latest version only; an older one is for reading.
  const latestVersion = versions.reduce((latest, row) => Math.max(latest, row.version), plan.version);
  const viewingLatest = plan.version >= latestVersion;
  const choose = viewingLatest ? onChooseOption : undefined;
  const lockChoice = viewingLatest ? onLockChoice : undefined;
  const reopenChoice = viewingLatest ? onReopenChoice : undefined;
  const markBooked = viewingLatest ? onMarkBooked : undefined;
  const firstUpdated = updatedIds?.[0] ?? "";
  // The clock follows the city of the day on show.
  const activeZone = segmentOfDay(plan, activeDay || plan.dates.start).destination.timezone;

  const scrollToDay = (date: string) => {
    const node = dayRefs.current[date];
    if (!node) return;
    if (window.matchMedia("(max-width: 899px)").matches) {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const scroller = scrollPort(node);
    if (!scroller) {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    scroller.scrollTo({ top: Math.max(0, scrollTopBelowBar(scroller, node)), behavior: "smooth" });
  };

  const jump = (date: string) => {
    setActiveDay(date);
    activeRef.current = date;
    onFocusDay?.(date);
    if (view === "detail") scrollToDay(date);
  };

  useEffect(() => {
    if (!firstUpdated) return;
    const day = plan.days.find((entry) => entry.items.some((item) => item.id === firstUpdated));
    if (!day) return;
    setActiveDay(day.date);
    onFocusDay?.(day.date);
    const timer = window.setTimeout(() => scrollToDay(day.date), 160);
    return () => window.clearTimeout(timer);
  }, [firstUpdated, plan]);

  const offers = stopOffers(messages);
  const clashes = clashesOf(
    plan.days.map((day, index) => {
      const dayNumber = index + 1;
      const items = day.items.map((item) => {
        const time = offers.find((offer) => offer.kind === "time" && offer.itemId === item.id && offer.item);
        return { id: item.id, name: item.name, start: time?.item?.start || item.start, end: time?.item?.end || item.end };
      });
      for (const offer of offers) {
        if (offer.kind === "add" && offer.day === dayNumber && offer.item) {
          items.push({ id: offer.item.id, name: offer.item.name, start: offer.item.start, end: offer.item.end });
        }
      }
      return { label: `${t("day.number", { n: dayNumber })} · ${day.date}`, items };
    }),
  );
  const overlapping = clashIds(clashes);
  const history = (plan.data_quality.notes ?? []).filter((line) => line.toLowerCase().includes("overlap"));
  const recorded = [
    ...history,
    ...clashes
      .filter((clash) => !history.some((line) => line.includes(clash.a) && line.includes(clash.b)))
      .map((clash) => t("warnings.overlap", { a: clash.a, b: clash.b, day: clash.label })),
  ];
  const openRecord = (which: "constraints" | "gaps") => {
    setView("detail");
    setOpenToken((current) => ({ which, n: current.n + 1 }));
  };
  useEffect(() => {
    if (!openToken.which || view !== "detail") return;
    const node = document.getElementById(`pf-${openToken.which}`);
    if (!node) return;
    const scroller = scrollPort(node);
    if (!scroller) {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    scroller.scrollTo({ top: Math.max(0, scrollTopBelowBar(scroller, node)), behavior: "smooth" });
  }, [openToken, view]);
  const groups = multi ? daysBySegment(plan) : [];
  const homeLeg = multi ? legHome(plan) : undefined;
  const renderDay = (day: DayPlan) => (
    <div key={day.date} ref={(node) => { dayRefs.current[day.date] = node; }}>
      <DaySection
        day={day}
        index={plan.days.findIndex((entry) => entry.date === day.date)}
        selectedItemId={selectedItemId}
        updatedIds={new Set(updatedIds ?? [])}
        clashIds={overlapping}
        onSelectItem={(item) => onSelectItem(item)}
        onChooseAlternative={choose ? (item, alternativeId) => choose("item", item.id, alternativeId) : undefined}
        offers={offers}
        onDecide={onDecide}
      />
    </div>
  );
  const renderLeg = (leg: TransportLeg) => (
    <LegSlot
      key={leg.id}
      plan={plan}
      leg={leg}
      title={`${leg.kind === "return" ? `${t("workspace.return")} · ` : ""}${leg.from_place} → ${leg.to_place}${leg.depart_date ? ` · ${leg.depart_date}` : ""}`}
      icon="route"
      changed={Boolean(changedLegIds?.has(leg.id))}
      busy={slotActions.busy.has(leg.id)}
      error={slotActions.errors[leg.id]}
      onLock={lockChoice ? (optionId) => slotActions.act(leg.id, () => lockChoice("transport", leg.id, optionId)) : undefined}
      onReopen={reopenChoice ? () => slotActions.act(leg.id, () => reopenChoice("transport", leg.id)) : undefined}
      onBooked={markBooked ? (booked) => slotActions.act(leg.id, () => markBooked("transport", leg.id, booked)) : undefined}
    />
  );
  // An airport transfer is a leg inside one city, titled by the airport and the stay it joins.
  const renderTransfer = (leg: TransportLeg) => (
    <LegSlot
      key={leg.id}
      plan={plan}
      leg={leg}
      title={
        leg.kind === "to_stay"
          ? `${t("transfer.toStay")} · ${leg.airport} → ${leg.to_place}`
          : `${t("transfer.fromStay")} · ${leg.from_place} → ${leg.airport}`
      }
      icon="car"
      changed={Boolean(changedLegIds?.has(leg.id))}
      busy={slotActions.busy.has(leg.id)}
      error={slotActions.errors[leg.id]}
      onLock={lockChoice ? (optionId) => slotActions.act(leg.id, () => lockChoice("transport", leg.id, optionId)) : undefined}
      onReopen={reopenChoice ? () => slotActions.act(leg.id, () => reopenChoice("transport", leg.id)) : undefined}
      onBooked={markBooked ? (booked) => slotActions.act(leg.id, () => markBooked("transport", leg.id, booked)) : undefined}
    />
  );
  const renderStay = (stay: HotelStay, title: string) => (
    <StaySlot
      key={stay.id}
      plan={plan}
      stay={stay}
      title={title}
      changed={Boolean(changedStayIds?.has(stay.id))}
      selected={selectedItemId === stay.id}
      busy={slotActions.busy.has(stay.id)}
      error={slotActions.errors[stay.id]}
      onSelect={() => onSelectItem(selectedItemId === stay.id ? null : hotelScopeItem(stay))}
      onLock={lockChoice ? (optionId) => slotActions.act(stay.id, () => lockChoice("hotel", stay.id, optionId)) : undefined}
      onReopen={reopenChoice ? () => slotActions.act(stay.id, () => reopenChoice("hotel", stay.id)) : undefined}
      onBooked={markBooked ? (booked) => slotActions.act(stay.id, () => markBooked("hotel", stay.id, booked)) : undefined}
      onLoadRooms={onLoadHotelRooms}
    />
  );
  // How many of the choices (stays, legs, airport transfers) are still to make; a click goes to the first one.
  const open = openCount(plan);
  const total = slotCount(plan);
  const summary =
    total > 0 ? (
      <button
        type="button"
        className="pf-choice__summary"
        data-all-locked={open === 0}
        onClick={() => document.querySelector<HTMLElement>('[data-choice-state="open"]')?.scrollIntoView({ behavior: "smooth", block: "center" })}
      >
        {open === 0 ? t("choice.allLocked") : t("choice.summary", { open, total })}
      </button>
    ) : null;

  return (
    <section className="pf-card pf-workspace" aria-label={t("workspace.label")}>
      <div className="pf-view-switch" role="tablist" aria-label={t("workspace.label")}>
        {([
          ["map", t("map.label")],
          ["detail", t("sheet.detail")],
          ["edit", t("sheet.edit")],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={view === id}
            onClick={() => setView(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="pf-workspace__head" data-compact={view === "detail" ? undefined : "true"}>
        <div className="pf-workspace__title">
          <div className="pf-stack" style={{ gap: 3 }}>
            <div className="pf-row">
              <Heading level={3}>{routeLabel(plan)}</Heading>
              <Badge tone="brand">v{plan.version}</Badge>
              {updatedIds?.length ? (
                <Badge tone="success" dot>{t("workspace.updatedCount", { count: updatedIds.length })}</Badge>
              ) : anyStayChanged ? (
                <Badge tone="success" dot>{t("workspace.hotelUpdated")}</Badge>
              ) : null}
            </div>
            {activeZone ? (
              <Text size="xs" tone="tertiary" title={activeZone}>
                {localClock(activeZone, t)}
              </Text>
            ) : null}
            <Text size="sm" tone="secondary">
              {plan.dates.start} → {plan.dates.end} · {t(plan.party_size === 1 ? "trip.travellersOne" : "trip.travellers", { count: plan.party_size })}
              {plan.origin ? ` · ${t("workspace.from", { origin: plan.origin })}` : ""}
            </Text>
          </div>
          <div className="pf-stack" style={{ gap: 6, alignItems: "flex-end" }}>
            <PlanFeedback
              finalized={finalized}
              verdict={feedbackVerdict}
              stars={feedbackStars}
              added={feedbackAdded}
              onFinalise={onFinalise}
              onUseful={(stars) => (stars ? onStars?.(stars) : onUseful())}
              onNotUseful={onNotUseful}
            />
            <StatusSummary plan={plan} conflicts={recorded.length} onOpen={openRecord} />
            <span className="pf-version">
              {t("workspace.version")}
              <select value={activeVersion} onChange={(event) => onVersionChange(Number(event.target.value))}>
                {versions.map((version) => (
                  <option key={version.version} value={version.version}>
                    v{version.version}
                  </option>
                ))}
              </select>
              {!viewingLatest && onRestoreVersion ? (
                <Button size="sm" variant="ghost" onClick={() => onRestoreVersion(plan.version)}>
                  {t("workspace.useVersion")}
                </Button>
              ) : null}
            </span>
          </div>
        </div>
        {view === "detail" ? languageNotice : null}
        <div className="pf-workspace__meta">
          <MiniStat icon="calendar" label={t("workspace.dates")} value={t(plan.days.length === 1 ? "workspace.daysOne" : "workspace.days", { count: plan.days.length })} />
          <MiniStat icon="users" label={t("workspace.party")} value={String(plan.party_size)} />
          {plan.budget ? <MiniStat icon="wallet" label={t("workspace.budget")} value={`${plan.cost.currency} ${plan.budget.amount.toLocaleString()}`} /> : null}
          {onRefresh ? <Button size="sm" variant="ghost" iconLeft="refresh" onClick={onRefresh}>{t("workspace.refresh")}</Button> : null}
        </div>
        <DayStrip
          days={plan.days}
          active={activeDay}
          onSelect={jump}
          cityOf={multi ? (day) => segmentOfDay(plan, day.date).destination.name : undefined}
        />
      </div>
      <div className="pf-workspace__body pf-scroll" ref={bodyRef}>
        {plan.cost.status === "over" && !hideBudget ? (
          <div className="pf-banner" data-tone="warning" role="status">
            <span>{t("warnings.budget")}</span>
            <BannerClose onClose={() => setHideBudget(true)} />
          </div>
        ) : null}
        {view === "map" ? (
          map ?? <Text size="sm" tone="secondary">{t("map.empty")}</Text>
        ) : view === "edit" ? (
          <DirectSheet
            plan={plan}
            activeDay={activeDay}
            canApply={viewingLatest && Boolean(onApplyLayout) && !held && !paused}
            saving={savingSheet}
            locked={held || !viewingLatest || !onApplyLayout || paused}
            lockNote={held ? t("sheet.operatorEditing", { name: agentName.trim() || t("chat.liveAgent") }) : paused ? pausedNote : ""}
            onFocusDay={onFocusDay}
            onApply={async (items, drops) => {
              if (!onApplyLayout) return;
              setSavingSheet(true);
              try {
                await onApplyLayout(items, drops);
              } finally {
                setSavingSheet(false);
              }
            }}
          />
        ) : (
        <>
        <ClashNote clashes={clashes} />
        {onChooseOption && !viewingLatest ? (
          <Text size="xs" tone="secondary">{t("options.latestOnly")}</Text>
        ) : null}
        {summary}
        {multi ? (
          <>
            {groups.map(({ segment, days }) => {
              if (!days.some((day) => day.date === activeDay)) return null;
              const into = legInto(plan, segment.id);
              const arriving = transferInto(plan, segment.id);
              const leaving = transferOutOf(plan, segment.id);
              const stays = staysIn(plan, segment.id);
              const nights = nightsBetween(segment.dates.start, segment.dates.end);
              return (
                <Fragment key={segment.id}>
                  {into ? renderLeg(into) : null}
                  {arriving ? renderTransfer(arriving) : null}
                  <section className="pf-city" aria-label={segment.destination.name}>
                    <div className="pf-city__head">
                      <Icon name="map" size={16} />
                      <Heading level={4}>
                        {t("city.heading", {
                          city: segment.destination.name,
                          arrive: segment.dates.start,
                          leave: segment.dates.end,
                          nights: t(nights === 1 ? "city.nightsOne" : "city.nights", { count: nights }),
                        })}
                      </Heading>
                    </div>
                    {nights > 0 && stays.length === 0 ? (
                      <Text size="xs" tone="tertiary">{t("city.noStay", { city: segment.destination.name })}</Text>
                    ) : null}
                    {stays.map((stay) =>
                      renderStay(
                        stay,
                        t("choice.stay", {
                          city: segment.destination.name,
                          nights: t(nights === 1 ? "city.nightsOne" : "city.nights", { count: nights }),
                        }),
                      ),
                    )}
                    {days.filter((day) => day.date === activeDay).map(renderDay)}
                  </section>
                  {leaving ? renderTransfer(leaving) : null}
                </Fragment>
              );
            })}
            {homeLeg ? renderLeg(homeLeg) : null}
          </>
        ) : (
          <>
        {plan.days.filter((day) => day.date === activeDay).map((day) => (
          <div key={day.date} ref={(node) => { dayRefs.current[day.date] = node; }}>
            <DaySection
              day={day}
              index={plan.days.findIndex((entry) => entry.date === day.date)}
              selectedItemId={selectedItemId}
              updatedIds={new Set(updatedIds ?? [])}
              clashIds={overlapping}
              onSelectItem={(item) => onSelectItem(item)}
              onChooseAlternative={choose ? (item, alternativeId) => choose("item", item.id, alternativeId) : undefined}
              offers={offers}
              onDecide={onDecide}
            />
          </div>
        ))}

        {hotel
          ? renderStay(
              hotel,
              t("choice.stayOne", {
                nights: t(nightsBetween(hotel.check_in, hotel.check_out) === 1 ? "city.nightsOne" : "city.nights", {
                  count: nightsBetween(hotel.check_in, hotel.check_out),
                }),
              }),
            )
          : null}

        {legsOf(plan).filter((leg) => leg.kind !== "return").map(renderLeg)}
        {transferLegsOf(plan).filter((leg) => leg.kind === "to_stay").map(renderTransfer)}
        {legsOf(plan).filter((leg) => leg.kind === "return").map(renderLeg)}
        {transferLegsOf(plan).filter((leg) => leg.kind !== "to_stay").map(renderTransfer)}

          </>
        )}

        <div className="pf-stack" style={{ gap: 0, marginTop: 8 }}>
          <Collapsible title={t("workspace.cost")} icon="wallet" defaultOpen>
            <BudgetPanel cost={plan.cost} />
          </Collapsible>
          <Collapsible
            id="pf-constraints"
            openToken={openToken.which === "constraints" ? openToken.n : 0}
            title={t("workspace.constraints")}
            icon="check"
            badge={<Badge tone={plan.constraints.some((c) => c.status === "fail") || recorded.length ? "danger" : "success"}>{plan.constraints.length + recorded.length}</Badge>}
          >
            <ConstraintList checks={plan.constraints} />
            {recorded.length ? (
              <div className="pf-stack" style={{ marginTop: 8 }}>
                <Text size="xs" tone="tertiary">{t("warnings.record")}</Text>
                {recorded.map((line) => (
                  <div className="pf-gap" key={line}>
                    <Icon name="alert" size={13} />
                    <span>{line}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </Collapsible>
          <Collapsible title={t("workspace.reservations")} icon="ticket" badge={<Badge tone="warning">{plan.reservations.length}</Badge>}>
            {plan.reservations.length ? (
              <div className="pf-stack">
                {plan.reservations.map((reservation) => (
                  <div className="pf-row" key={reservation.id} style={{ alignItems: "flex-start" }}>
                    <Badge tone={reservation.status === "to_book" ? "warning" : "neutral"} dot>{reservation.status}</Badge>
                    <div className="pf-stack" style={{ gap: 1 }}>
                      <Text size="sm" weight="semibold">{reservation.name}</Text>
                      <Text size="xs" tone="tertiary">{reservation.reason}</Text>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Text size="xs" tone="tertiary">{t("workspace.noReservations")}</Text>
            )}
          </Collapsible>
          <Collapsible title={t("workspace.disruptions")} icon="alert" badge={<Badge tone={plan.disruptions.length ? "danger" : "neutral"}>{plan.disruptions.length}</Badge>}>
            {plan.disruptions.length ? (
              <div className="pf-stack">
                {plan.disruptions.map((disruption) => (
                  <div className="pf-gap" key={disruption.id}>
                    <Icon name="alert" size={13} />
                    <span>{disruption.summary} — {disruption.resolved ? t("workspace.resolved") : t("workspace.open")}</span>
                  </div>
                ))}
              </div>
            ) : (
              <Text size="xs" tone="tertiary">{t("workspace.noDisruptions")}</Text>
            )}
          </Collapsible>
          <PlanAssessmentCard assessment={plan.assessment} onAnswer={onAnswerQuestion} />
          <Collapsible
            id="pf-gaps"
            openToken={openToken.which === "gaps" ? openToken.n : 0}
            title={t("workspace.dataSources", { count: unknownGaps })}
            icon="info"
            badge={<Badge tone={unknownGaps ? "warning" : "success"} dot>{unknownGaps ? t("workspace.gaps") : t("workspace.complete")}</Badge>}
          >
            <GapList gaps={plan.data_quality.gaps} />
            {(plan.data_quality.freshness ?? []).map((mark) => (
              <div className="pf-gap" key={mark.section}>
                <Icon name={mark.status === "outdated" ? "alert" : "info"} size={13} />
                <span>
                  {t(mark.status === "outdated" ? "workspace.freshness.outdated" : "workspace.freshness.refreshed", {
                    section: t(FRESHNESS_SECTION[mark.section]),
                    fetched: new Date(mark.fetched_at).toLocaleString("en-GB"),
                  })}
                </span>
              </div>
            ))}
            <Divider />
            <div className="pf-row pf-row--wrap">
              {plan.data_quality.sources_used.map((source) => (
                <Badge key={source} tone="neutral">{source}</Badge>
              ))}
            </div>
            <Text size="xs" tone="tertiary">
              {t("workspace.searchWindow", {
                months: plan.data_quality.search_window_months,
                generated: new Date(plan.data_quality.generated_at).toLocaleString("en-GB"),
              })}
            </Text>
          </Collapsible>
          {trace ? (
            <Collapsible title={t("workspace.howAiWorked")} icon="info">
              <TraceList trace={trace} />
            </Collapsible>
          ) : null}
        </div>
        <Divider />
        </>
        )}
      </div>
    </section>
  );
}

export function ThoughtLog({ trace, developer = false }: { trace: RunTrace; developer?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="pf-card" style={{ padding: 12 }}>
      <Text size="xs" weight="semibold">{t("chat.thinking")}</Text>
      {(trace.why ?? []).map((line) => (
        <Text size="xs" key={line}>{line}</Text>
      ))}
      {developer ? trace.generations.filter((call) => call.reasoning || call.output_preview).map((call, index) => (
        <div key={`${call.purpose}-${index}`} style={{ marginTop: 8 }}>
          <Text size="xs" weight="semibold">{call.purpose} · {call.latency_ms} ms</Text>
          {call.user_preview ? <Text size="xs" tone="tertiary">{call.user_preview}</Text> : null}
          <Text size="xs">{call.reasoning || t("trace.noReasoning")}</Text>
          {call.output_preview ? (
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", maxHeight: 280, overflow: "auto", fontSize: 12 }}>{call.output_preview}</pre>
          ) : null}
        </div>
      )) : null}
      {developer ? (trace.searches ?? []).map((search) => (
        <div key={`${search.provider}-${search.query}`} style={{ marginTop: 8 }}>
          <Text size="xs" weight="semibold">{t("trace.search", { provider: search.provider })}</Text>
          <Text size="xs" tone="tertiary">{search.query}</Text>
          {search.results.map((row, index) => (
            <Text size="xs" key={`${row.url ?? index}`}>
              {row.kept === false ? "dropped" : "kept"} {row.relevance ?? ""} {row.title || row.url || row.error || ""}
              {row.snippet ? ` — ${row.snippet}` : ""}
            </Text>
          ))}
        </div>
      )) : null}
    </div>
  );
}

export function TraceList({ trace }: { trace: RunTrace }) {
  const { t } = useI18n();
  const max = Math.max(...trace.spans.map((span) => span.duration_ms ?? 0), 1);
  return (
    <div className="pf-trace">
      <div className="pf-row pf-row--between">
        <Text size="xs" weight="semibold">{t("trace.summary", { steps: trace.spans.length, calls: trace.tool_calls })}</Text>
        <Text size="xs" tone="tertiary" className="pf-mono">{trace.request_id}</Text>
      </div>
      {trace.reason ? (
        <Text size="xs" tone="secondary">{t("trace.reason", { reason: trace.reason })}</Text>
      ) : null}
      {trace.generations.map((call) => (
        <div key={`${call.purpose}-${call.latency_ms}`}>
          <Text size="xs" weight="semibold">{call.purpose}</Text>
          <Text size="xs" tone="tertiary">{call.model} · {call.latency_ms} ms</Text>
          {call.reasoning ? (
            <Text size="xs">{call.reasoning}</Text>
          ) : call.output_preview ? (
            <Text size="xs" tone="secondary">{t("trace.noReasoning")}</Text>
          ) : null}
          {call.output_preview ? (
            <Text size="xs" tone="tertiary">{call.output_preview}</Text>
          ) : null}
        </div>
      ))}
      {(trace.searches ?? []).map((search) => (
        <div key={`${search.provider}-${search.query}`}>
          <Text size="xs" weight="semibold">{t("trace.search", { provider: search.provider })}</Text>
          <Text size="xs" tone="tertiary">{search.query}</Text>
          {search.results.map((row, index) => (
            <Text size="xs" key={`${row.url ?? row.title ?? "row"}-${index}`}>
              {(row.kept === false ? "dropped" : "kept")} {row.relevance ?? ""} {row.title || row.url || row.error || ""}
              {row.snippet ? ` — ${row.snippet}` : ""}
            </Text>
          ))}
        </div>
      ))}
      {trace.spans.slice(0, 12).map((span, index) => (
        <div className="pf-trace__row" key={`${span.name}-${index}`}>
          <span style={{ width: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{span.name}</span>
          <span className="pf-trace__bar" style={{ width: `${Math.max(3, ((span.duration_ms ?? 0) / max) * 50)}%` }} />
          <span className="pf-mono">{span.duration_ms ?? "—"} ms</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ map -- */

/** Travel-mode names; `MODE` in maps/geo keeps the colours and icons. */
export const MODE_LABEL: Record<TravelMode, MessageKey> = {
  walk: "map.mode.walk",
  cycle: "map.optionCycle",
  metro: "map.mode.metro",
  drive: "map.mode.drive",
  flight: "map.mode.flight",
};

function modeOf(value: string): TravelMode {
  if (value === "cycle") return "cycle";
  if (value === "transit" || value === "metro") return "metro";
  if (value === "drive" || value === "taxi") return "drive";
  if (value === "flight") return "flight";
  return "walk";
}

function modeMinutes(km: number, mode: TravelMode): number {
  const speed = mode === "walk" ? 4.5 : mode === "cycle" ? 15 : mode === "metro" ? 22 : mode === "drive" ? 32 : 620;
  const overhead = mode === "walk" || mode === "cycle" ? 0 : mode === "metro" ? 8 : mode === "drive" ? 5 : 90;
  return Math.max(2, Math.round((km / speed) * 60 + overhead));
}

function localClock(zone: string, t: Translate): string {
  try {
    const time = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date());
    return t("workspace.localTime", { time, zone });
  } catch {
    return zone;
  }
}

type LegRef = { fromId: string; toId: string };

type LegChoice = {
  mode: string;
  minutes: number;
  km: number | null;
  path: [number, number][];
  steps: MapStep[];
  source: string;
};

const legKey = (fromId: string, toId: string) => `${fromId}|${toId}`;

const sameLeg = (left: LegRef | null, right: LegRef) =>
  left !== null && left.fromId === right.fromId && left.toId === right.toId;

function shiftTime(value: string, delta: number): string {
  const mins = minutesOf(value);
  if (mins === null || delta === 0) return value;
  const next = Math.max(0, Math.min(23 * 60 + 59, mins + delta));
  return clockText(next);
}

/** The picked mode replaces that hop. Later stops move by the minute difference. */
function applyLegChoices(
  day: DayPlan | undefined,
  legs: MapLeg[],
  choices: Record<string, LegChoice>,
): { day: DayPlan | undefined; legs: MapLeg[] } {
  const nextLegs = legs.map((leg) => {
    const choice = choices[legKey(leg.from_id, leg.to_id)];
    if (!choice) return leg;
    return {
      ...leg,
      mode: choice.mode,
      minutes: choice.minutes,
      km: choice.km,
      path: choice.path,
      steps: choice.steps,
      source: choice.source,
    };
  });
  if (!day) return { day, legs: nextLegs };
  const startOf = new Map(day.items.map((item) => [item.id, minutesOf(item.start) ?? 0]));
  const deltas = legs.flatMap((leg) => {
    const choice = choices[legKey(leg.from_id, leg.to_id)];
    if (!choice || leg.minutes === null) return [];
    const at = startOf.get(leg.to_id);
    const delta = choice.minutes - leg.minutes;
    return at === undefined || delta === 0 ? [] : [{ at, delta }];
  });
  if (!deltas.length) return { day, legs: nextLegs };
  return {
    day: {
      ...day,
      items: day.items.map((item) => {
        const start = minutesOf(item.start);
        if (start === null) return item;
        const delta = deltas.filter((row) => row.at <= start).reduce((sum, row) => sum + row.delta, 0);
        if (!delta) return item;
        return { ...item, start: shiftTime(item.start, delta), end: shiftTime(item.end, delta) };
      }),
    },
    legs: nextLegs,
  };
}

export function MapPanel({
  plan,
  activeDay,
  onActiveDay,
  selectedItemId,
  onSelectItem,
  updatedIds = [],
  changedStayIds,
}: {
  plan: TripPlan;
  activeDay: string;
  onActiveDay: (date: string) => void;
  selectedItemId: string | null;
  onSelectItem: (item: PlanItem | null) => void;
  updatedIds?: string[];
  /** The stays (by id) the last change touched. */
  changedStayIds?: ReadonlySet<string>;
}) {
  const { t } = useI18n();
  const [showRoute, setShowRoute] = useState(true);
  // Every city at once, instead of one day.
  const [overview, setOverview] = useState(false);
  const [clock, setClock] = useState(9 * 60);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(false);
  const [legTarget, setLegTarget] = useState<LegRef | null>(null);
  const [legOptions, setLegOptions] = useState<LegOptions | null>(null);
  const [legLoading, setLegLoading] = useState(false);
  const [legError, setLegError] = useState<string | null>(null);
  const [hoverLeg, setHoverLeg] = useState<LegRef | null>(null);
  const [pinnedLeg, setPinnedLeg] = useState<LegRef | null>(null);
  const [choices, setChoices] = useState<Record<string, LegChoice>>({});

  const openLegOptions = (fromId: string, toId: string) => {
    setPinnedLeg({ fromId, toId });
    setLegTarget({ fromId, toId });
    setLegOptions(null);
    setLegError(null);
    setLegLoading(true);
    getLegOptions(plan.session_id, fromId, toId)
      .then((data) => setLegOptions(data))
      .catch(() => setLegError(t("map.optionsError")))
      .finally(() => setLegLoading(false));
  };
  const day = plan.days.find((entry) => entry.date === activeDay) ?? plan.days[0];
  // The city the day is in: its centre, and the stay of that night.
  const scope = dayScope(plan, day?.date ?? plan.dates.start);
  const multi = segmentsOf(plan).length > 1;
  const [legs, setLegs] = useState<MapLeg[]>([]);
  const [anchors, setAnchors] = useState<MapAnchor[]>([]);
  useEffect(() => {
    if (!day?.date) return;
    let cancelled = false;
    void getLegs(plan.session_id, day.date)
      .then((next) => {
        if (!cancelled) {
          setLegs(next.legs);
          setAnchors(next.anchors);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLegs([]);
          setAnchors([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [plan.session_id, plan.trip_id, plan.version, day?.date]);
  useEffect(() => {
    setChoices({});
    setPinnedLeg(null);
    setHoverLeg(null);
  }, [plan.trip_id, plan.version, day?.date]);
  const hotel = scope.stay ?? undefined;
  const hotelChanged = Boolean(hotel && changedStayIds?.has(hotel.id));
  const travel = multi && !overview ? travelLine(plan, day?.date ?? plan.dates.start) : null;
  const wholeTrip = multi && overview ? overviewStops(plan) : null;
  const hotelItem = useMemo(() => (hotel ? hotelScopeItem(hotel) : null), [hotel]);
  const shown = useMemo(() => applyLegChoices(day, legs, choices), [day, legs, choices]);
  const shownDay = shown.day;
  const shownLegs = shown.legs;
  const routePoints = useMemo(
    () => dayRoute(shownDay?.items ?? [], hotelItem),
    [shownDay, hotelItem],
  );
  const points = routePoints;

  /** The replay chain is the same route: leave the hotel, visit the day, come back. */
  const timelinePoints = routePoints;

  const nameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of routePoints) map.set(item.id, item.name);
    for (const anchor of anchors) map.set(anchor.id, anchor.name);
    if (hotelItem) map.set(hotelItem.id, hotelItem.name);
    return map;
  }, [routePoints, hotelItem, anchors]);
  const selected =
    day?.items.find((item) => item.id === selectedItemId) ??
    (hotel && hotel.id === selectedItemId ? hotelScopeItem(hotel) : null);
  const updatedSet = useMemo(() => new Set(updatedIds), [updatedIds]);

  const firstStartOfDay = useMemo(() => {
    const starts = (day?.items ?? [])
      .map((item) => minutesOf(item.start))
      .filter((value): value is number => value !== null);
    return starts.length ? Math.max(CLOCK_MIN, Math.min(...starts)) : 9 * 60;
  }, [day]);

  const activeItem = useMemo(() => {
    const items = shownDay?.items ?? [];
    return (
      items.find((item) => {
        const start = minutesOf(item.start);
        const end = minutesOf(item.end);
        return start !== null && start <= clock && (end === null || end > clock);
      }) ?? null
    );
  }, [shownDay, clock]);

  const currentItem = useMemo(() => {
    const items = shownDay?.items ?? [];
    return (
      activeItem ??
      [...items].reverse().find((item) => {
        const start = minutesOf(item.start);
        return start !== null && start <= clock;
      }) ??
      null
    );
  }, [shownDay, clock, activeItem]);

  const tracker = useMemo<{
    position: [number, number];
    mode: TravelMode;
    moving: boolean;
    label: string;
    fromId: string | null;
    toId: string | null;
    progress: number;
  } | null>(() => {
    const items = timelinePoints;
    if (!items.length) return null;

    const leg = (from: PlanItem, to: PlanItem) => {
      const real = shownLegs.find((entry) => entry.from_id === from.id && entry.to_id === to.id);
      if (real) {
        const mode = modeOf(real.mode);
        return {
          mode,
          label: t("map.legReal", { name: to.name, minutes: real.minutes ?? "?", mode: t(MODE_LABEL[mode]).toLowerCase() }),
          km: real.km ?? kmBetween(from.geo!, to.geo!),
        };
      }
      const km = kmBetween(from.geo!, to.geo!);
      const mode = modeForDistance(km);
      return { mode, label: t("map.legEstimate", { name: to.name, minutes: modeMinutes(km, mode) }), km };
    };

    const timeline = timelineAt(items, clock);
    if (timeline.active?.geo) {
      const index = items.findIndex((item) => item.id === timeline.active?.id);
      const next = items[index + 1];
      const previous = items[index - 1];
      const target = next ?? previous;
      const route = target ? leg(timeline.active, target) : null;
      return {
        position: [timeline.active.geo.lat, timeline.active.geo.lng],
        mode: route?.mode ?? "walk",
        moving: false,
        label: t("map.atPlace", { name: timeline.active.name }),
        fromId: timeline.active.id,
        toId: timeline.active.id,
        progress: 0,
      };
    }

    if (timeline.from?.geo && timeline.to?.geo) {
      const route = leg(timeline.from, timeline.to);
      return {
        position: [
          timeline.from.geo.lat + (timeline.to.geo.lat - timeline.from.geo.lat) * timeline.progress,
          timeline.from.geo.lng + (timeline.to.geo.lng - timeline.from.geo.lng) * timeline.progress,
        ],
        mode: route.mode,
        moving: true,
        label: route.label,
        fromId: timeline.from.id,
        toId: timeline.to.id,
        progress: timeline.progress,
      };
    }
    const anchor = timeline.from ?? timeline.to;
    if (!anchor?.geo) return null;
    const target =
      timeline.to && timeline.to.id !== anchor.id
        ? timeline.to
        : timeline.from && timeline.from.id !== anchor.id
          ? timeline.from
          : null;
    const route = target ? leg(anchor, target) : null;
    return {
      position: [anchor.geo.lat, anchor.geo.lng],
      mode: route?.mode ?? "walk",
      moving: false,
      label: t("map.atPlace", { name: anchor.name }),
      fromId: anchor.id,
      toId: anchor.id,
      progress: 0,
    };
  }, [timelinePoints, shownLegs, clock, activeItem, t]);

  const dayLegs = useMemo(() => {
    if (shownLegs.length) {
      return shownLegs.map((entry) => ({
        from: { id: entry.from_id, name: nameById.get(entry.from_id) ?? t("map.theStay") },
        to: { id: entry.to_id, name: nameById.get(entry.to_id) ?? t("map.theStay") },
        mode: modeOf(entry.mode),
        km: entry.km ?? 0,
        minutes: entry.minutes ?? 0,
        real: entry.source !== "estimate",
        longWalk: modeOf(entry.mode) === "walk" && (entry.minutes ?? 0) > 30,
      }));
    }
    const items = [...routePoints]
      .filter((item) => item.geo)
      .sort((a, b) => (minutesOf(a.start) ?? 0) - (minutesOf(b.start) ?? 0));
    return items.slice(1).map((to, index) => {
      const from = items[index];
      const km = kmBetween(from.geo!, to.geo!);
      const mode = modeForDistance(km);
      return {
        from: { id: from.id, name: from.name },
        to: { id: to.id, name: to.name },
        mode,
        km,
        minutes: modeMinutes(km, mode),
        real: false,
        longWalk: mode === "walk" && modeMinutes(km, mode) > 30,
      };
    });
  }, [shownLegs, nameById, routePoints, t]);

  const legendModes = useMemo(
    () => [...new Set(dayLegs.map((entry) => entry.mode))],
    [dayLegs],
  );
  const specialModes = useMemo(() => {
    const found = new Set<string>();
    for (const leg of shownLegs) {
      for (const step of leg.steps ?? []) {
        if (step.mode in SEGMENT_COLOR) found.add(step.mode);
      }
    }
    return [...found];
  }, [shownLegs]);

  const autoAdvance = useRef(false);

  useEffect(() => {
    setClock(firstStartOfDay);
    // Rolling into the next day during playback must not stop the playback.
    if (autoAdvance.current) autoAdvance.current = false;
    else setPlaying(false);
  }, [day?.date, plan.trip_id, firstStartOfDay]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setClock((value) => Math.min(CLOCK_MAX, value + 5));
    }, 275 / speed);
    return () => window.clearInterval(timer);
  }, [playing, speed]);

  // When the day is done, keep the replay going on the next day (old behaviour).
  const advancing = useRef(false);

  useEffect(() => {
    if (!playing) return;
    // The commit right after a day switch still carries the previous day's clock;
    // skip it or the stale value stops the playback.
    if (advancing.current) {
      advancing.current = false;
      return;
    }
    const lastEnd = (shownDay?.items ?? []).reduce((latest, item) => {
      const end = minutesOf(item.end);
      return end === null ? latest : Math.max(latest, end);
    }, 0);
    const stopAt = Math.min(lastEnd > 0 ? lastEnd + 15 : CLOCK_MAX, CLOCK_MAX);
    if (clock < stopAt) return;
    const index = plan.days.findIndex((entry) => entry.date === day?.date);
    const next = plan.days[index + 1];
    if (!next) {
      setPlaying(false);
      return;
    }
    const nextStarts = (next.items ?? [])
      .map((item) => minutesOf(item.start))
      .filter((value): value is number => value !== null);
    advancing.current = true;
    autoAdvance.current = true;
    setClock(nextStarts.length ? Math.max(CLOCK_MIN, Math.min(...nextStarts)) : 9 * 60);
    onActiveDay(next.date);
  }, [clock, playing, day, shownDay, plan.days, onActiveDay]);

  return (
    <section className="pf-card pf-map" aria-label={t("map.label")}>
      <TripMap
        plan={plan}
        day={shownDay}
        points={points}
        routePoints={routePoints}
        hotel={hotel}
        selectedItemId={selectedItemId}
        onSelectItem={onSelectItem}
        showRoute={showRoute}
        onToggleRoute={() => setShowRoute((value) => !value)}
        clock={clock}
        currentItemId={activeItem?.id ?? null}
        currentName={currentItem?.name ?? null}
        speed={speed}
        token={tracker}
        follow={follow && !pinnedLeg && !hoverLeg}
        updatedIds={updatedSet}
        hotelChanged={hotelChanged}
        legs={shownLegs}
        anchors={anchors}
        overview={wholeTrip}
        travel={travel}
        focusFromId={(pinnedLeg ?? hoverLeg)?.fromId ?? null}
        focusToId={(pinnedLeg ?? hoverLeg)?.toId ?? null}
      />
      <div className="pf-map__foot">
        <div className="pf-row pf-row--between">
          <div className="pf-stack" style={{ gap: 0 }}>
            <Text size="sm" weight="semibold">{day?.title ?? t("map.selectDay")}</Text>
            <Text size="xs" tone="tertiary">
              {t("map.counts", {
                sights: points.filter((item) => item.type !== "meal").length,
                meals: points.filter((item) => item.type === "meal").length,
              })}{" "}
              · {overview && multi ? routeLabel(plan) : scope.segment.destination.name}
              {travel ? ` · ${travel.label} (${travel.mode})` : ""}
            </Text>
          </div>
          <div className="pf-row" style={{ gap: 4 }}>
            {multi ? (
              <button
                type="button"
                className="pf-chip"
                data-active={overview}
                aria-pressed={overview}
                onClick={() => {
                  setOverview((value) => !value);
                  setPlaying(false);
                }}
              >
                {t("map.wholeTrip")}
              </button>
            ) : null}
            {plan.days.map((entry) => (
              <button
                key={entry.date}
                type="button"
                className="pf-chip"
                data-active={entry.date === activeDay}
                onClick={() => {
                  setOverview(false);
                  onActiveDay(entry.date);
                  setPlaying(false);
                }}
              >
                {new Date(`${entry.date}T12:00:00`).getDate()}
              </button>
            ))}
          </div>
        </div>
        <div className="pf-route-legend" aria-label={t("map.legend")}>
          <span className="pf-route-legend__item">
            <i className="pf-route-legend__line pf-route-legend__line--done" /> {t("map.travelled")}
          </span>
          <span className="pf-route-legend__item">
            <i className="pf-route-legend__line pf-route-legend__line--ahead" /> {t("map.ahead")}
          </span>
          {legendModes.map((mode) => (
            <span className="pf-route-legend__item" key={mode}>
              <i className="pf-route-legend__dot" style={{ background: MODE[mode].color }} />
              {t(MODE_LABEL[mode])}
            </span>
          ))}
          {specialModes.map((mode) => (
            <span className="pf-route-legend__item" key={mode}>
              <i
                className="pf-route-legend__line"
                style={{
                  borderColor: SEGMENT_COLOR[mode as keyof typeof SEGMENT_COLOR],
                  borderTopWidth: 3,
                }}
              />
              {t(mode === "ferry" ? "map.ferry" : mode === "cable" ? "map.cableCar" : "map.tram")}
            </span>
          ))}
        </div>
        {specialModes.length ? <Text size="xs" tone="tertiary">{t("map.specialNote")}</Text> : null}
        {dayLegs.length ? (
          <div className="pf-route-legs">
            {dayLegs.map((entry, index) => (
              <button
                type="button"
                className="pf-route-leg"
                key={`${entry.from.id}-${entry.to.id}-${index}`}
                title={t("map.optionsTitle")}
                data-active={sameLeg(pinnedLeg ?? hoverLeg, { fromId: entry.from.id, toId: entry.to.id })}
                onMouseEnter={() => {
                  if (!pinnedLeg) setHoverLeg({ fromId: entry.from.id, toId: entry.to.id });
                }}
                onMouseLeave={() => setHoverLeg(null)}
                onClick={() => {
                  const next = { fromId: entry.from.id, toId: entry.to.id };
                  // A second click on the pinned hop releases it and the day route returns.
                  if (sameLeg(pinnedLeg, next)) {
                    setPinnedLeg(null);
                    setLegTarget(null);
                    return;
                  }
                  openLegOptions(next.fromId, next.toId);
                }}
              >
                <span className="pf-route-leg__mode" style={{ color: MODE[entry.mode].color }}>
                  {t(MODE_LABEL[entry.mode])}
                </span>
                <span className="pf-route-leg__name">
                  {entry.from.name} → {entry.to.name}
                </span>
                <span className="pf-route-leg__time">
                  {entry.real ? t("map.minutes", { minutes: entry.minutes }) : t("map.minutesEstimate", { minutes: entry.minutes })}
                </span>
                {entry.longWalk ? (
                  <span className="pf-route-leg__warn">{t("map.longWalk")}</span>
                ) : null}
              </button>
            ))}
          </div>
        ) : null}
        {dayLegs.some((entry) => entry.longWalk) ? (
          <Text size="xs" tone="tertiary">{t("map.longWalkNote")}</Text>
        ) : null}
        <div className="pf-map__timebar">
          <IconButton
            icon={playing ? "pause" : "play"}
            label={playing ? t("map.pause") : t("map.play")}
            onClick={() => {
              if (clock >= CLOCK_MAX) setClock(firstStartOfDay);
              setPlaying((value) => !value);
            }}
          />
          <div className="pf-map__speeds" role="group" aria-label={t("map.speed")}>
            {[1, 2, 4].map((value) => (
              <button
                key={value}
                type="button"
                className="pf-map__speed"
                data-active={speed === value}
                aria-pressed={speed === value}
                onClick={() => setSpeed(value)}
              >
                {value}×
              </button>
            ))}
          </div>
          <button
            type="button"
            className="pf-map__follow"
            data-active={follow}
            aria-pressed={follow}
            aria-label={follow ? t("map.unpinLabel") : t("map.pinLabel")}
            onClick={() => setFollow((value) => !value)}
          >
            {follow ? t("map.pinned") : t("map.pin")}
          </button>
          <div className="pf-map__time-main">
            <div className="pf-map__time-row">
              <strong className="pf-map__time">{clockText(clock)}</strong>
              <span className="pf-map__now">
                {currentItem
                  ? `${currentItem.start}–${currentItem.end} · ${currentItem.name}`
                  : t("map.beforeFirstStop")}
              </span>
            </div>
            {tracker ? (
              <div className="pf-map__legrow">
                {tracker.moving ? (
                  <span className="pf-map__mode" data-mode={tracker.mode}>
                    <Icon name={MODE[tracker.mode].icon} size={11} />
                    {t(MODE_LABEL[tracker.mode])}
                  </span>
                ) : null}
                <span className="pf-map__leglabel">{tracker.label}</span>
              </div>
            ) : null}
            <div className="pf-map__timeline">
              <input
                className="pf-slider"
                type="range"
                aria-label={t("map.timeOfDay")}
                min={CLOCK_MIN}
                max={CLOCK_MAX}
                step={CLOCK_STEP}
                value={clock}
                style={{
                  "--clock-progress": `${((clock - CLOCK_MIN) / (CLOCK_MAX - CLOCK_MIN)) * 100}%`,
                } as CSSProperties}
                onChange={(event) => {
                  setPlaying(false);
                  setClock(Number(event.target.value));
                }}
              />
              <div className="pf-map__ticks">
                {points.map((item) => {
                  const start = minutesOf(item.start);
                  if (start === null) return null;
                  const left = ((start - CLOCK_MIN) / (CLOCK_MAX - CLOCK_MIN)) * 100;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className="pf-map__tick"
                      style={{ left: `${Math.max(0, Math.min(100, left))}%` }}
                      title={`${item.start} · ${item.name}`}
                      aria-label={t("map.jumpTo", { time: item.start, name: item.name })}
                      onClick={() => {
                        setPlaying(false);
                        setClock(start);
                        onSelectItem(item);
                      }}
                    />
                  );
                })}
              </div>
            </div>
          </div>
        </div>
        <PlaceCard item={selected} weather={day?.weather ?? null} onClose={() => onSelectItem(null)} />
      </div>
      <Dialog
        open={Boolean(legTarget)}
        onClose={() => setLegTarget(null)}
        title={t("map.optionsTitle")}
      >
        <TravelOptions
          data={legOptions}
          loading={legLoading}
          error={legError}
          selected={(() => {
            if (!legTarget) return null;
            const picked =
              choices[legKey(legTarget.fromId, legTarget.toId)]?.mode ??
              shownLegs.find((leg) => leg.from_id === legTarget.fromId && leg.to_id === legTarget.toId)?.mode;
            if (picked === "metro") return "transit";
            if (picked === "taxi") return "drive";
            return picked ?? null;
          })()}
          onSelect={(option) => {
            if (!legTarget || option.minutes === null) return;
            const key = legKey(legTarget.fromId, legTarget.toId);
            const baseline = legs.find((leg) => leg.from_id === legTarget.fromId && leg.to_id === legTarget.toId);
            const previous = choices[key]?.minutes ?? baseline?.minutes ?? option.minutes;
            const step = option.minutes - previous;
            const arrival = minutesOf(day?.items.find((item) => item.id === legTarget.toId)?.start);
            setChoices((current) => ({
              ...current,
              [key]: {
                mode: option.mode,
                minutes: option.minutes ?? 0,
                km: option.km,
                path: option.path ?? [],
                steps: option.steps ?? [],
                source: option.source,
              },
            }));
            if (step !== 0 && arrival !== null && clock >= arrival) {
              setPlaying(false);
              setClock((value) => Math.min(CLOCK_MAX, Math.max(CLOCK_MIN, value + step)));
            }
          }}
        />
      </Dialog>
    </section>
  );
}

/* ------------------------------------------------------------- trending -- */

export function TrendingRail({
  items,
  trace,
  loading = false,
  error = null,
  onTry,
  onRefresh,
  query = "",
  onQueryChange,
}: {
  items: TrendingPlace[];
  trace?: TrendingTrace | null;
  loading?: boolean;
  error?: string | null;
  onTry?: (prompt: string) => void;
  onRefresh?: () => void;
  query?: string;
  onQueryChange?: (value: string) => void;
}) {
  const { t } = useI18n();
  const [asking, setAsking] = useState<string | null>(null);

  // Sent to the planner as the traveller's request, so it stays in English.
  const promptFor = (item: TrendingPlace) => {
    const places = item.places.slice(0, 3);
    const base = `Plan a ${item.days}-day trip to ${item.destination}`;
    return places.length ? `${base}. Include ${places.join(", ")}.` : `${base}.`;
  };

  return (
    <aside className="pf-card pf-trending" aria-label={t("trending.label")}>
      <div className="pf-row pf-row--between" style={{ alignItems: "flex-start" }}>
        <div className="pf-stack" style={{ gap: 2 }}>
          <Text size="xs" tone="tertiary" weight="semibold" style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
            {t("trending.eyebrow")}
          </Text>
          <Heading level={4}>{t("trending.title")}</Heading>
        </div>
        {onRefresh ? (
          <IconButton icon="refresh" label={t("trending.refresh")} onClick={onRefresh} />
        ) : null}
      </div>
      <Text size="xs" tone="tertiary">
        {t("trending.privacy")}
      </Text>
      {onQueryChange ? (
        <SearchField
          value={query}
          onChange={onQueryChange}
          placeholder={t("trending.searchPlaceholder")}
          label={t("trending.search")}
        />
      ) : null}

      {loading ? <Text size="sm" tone="secondary">{t("trending.loading")}</Text> : null}
      {error ? (
        <div className="pf-trending__empty">
          <Text size="sm" tone="secondary">{error}</Text>
          {onRefresh ? <Button size="sm" variant="ghost" onClick={onRefresh}>{t("common.tryAgain")}</Button> : null}
        </div>
      ) : null}
      {!loading && !error && !items.length ? (
        <div className="pf-trending__empty">
          <Icon name="compass" size={18} />
          <Text size="sm" tone="secondary">
            {t("trending.empty")}
          </Text>
        </div>
      ) : null}

      <div className="pf-trending__list">
        {items.map((item) => {
          const open = asking === item.destination;
          return (
            <article className="pf-trending__card" key={`${item.destination}-${item.year ?? ""}`}>
              <div className="pf-row pf-row--between">
                <strong>{item.destination}</strong>
                <Badge tone="brand">{t(item.plans === 1 ? "trending.plansOne" : "trending.plans", { count: item.plans })}</Badge>
              </div>
              <span className="pf-trending__meta">
                {t(item.days === 1 ? "workspace.daysOne" : "workspace.days", { count: item.days })}{item.year ? ` · ${item.year}` : ""}
              </span>
              {item.places.length ? (
                <div className="pf-trending__places">
                  {item.places.slice(0, 4).map((place) => (
                    <span key={place}>{place}</span>
                  ))}
                </div>
              ) : null}
              {item.match?.length ? (
                <div className="pf-trending__match">
                  <span>{t("trending.matches")}</span>
                  {item.match.map((tag) => (
                    <Badge key={tag} tone="info">{tag}</Badge>
                  ))}
                </div>
              ) : null}
              {open ? (
                <div className="pf-trending__confirm">
                  <Text size="xs" tone="secondary">
                    {t("trending.confirm")}
                  </Text>
                  <div className="pf-row">
                    <Button
                      size="sm"
                      variant="primary"
                      iconRight="arrowRight"
                      onClick={() => {
                        setAsking(null);
                        onTry?.(promptFor(item));
                      }}
                    >
                      {t("trending.start")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>
                      {t("common.cancel")}
                    </Button>
                  </div>
                </div>
              ) : (
                <Button size="sm" variant="ghost" iconRight="arrowRight" onClick={() => setAsking(item.destination)}>
                  {t("trending.try")}
                </Button>
              )}
            </article>
          );
        })}
      </div>

      {trace?.note ? (
        <details className="pf-trending__trace">
          <summary>{t("trending.ranked")}</summary>
          <Text size="xs" tone="tertiary">{trace.note}</Text>
        </details>
      ) : null}
    </aside>
  );
}

/* ---------------------------------------------------------------- saved -- */

export function SavedTripsView({
  trips,
  onOpen,
}: {
  trips: { id: string; destination: string; rating: number; stars: number | null; summary: string; created_at: string; days: number | null }[];
  onOpen?: (id: string) => void;
}) {
  const { t } = useI18n();
  return (
    <main className="pf-saved">
      <PageHeader
        eyebrow={t("saved.eyebrow")}
        title={t("saved.title")}
        description={t("saved.body")}
      />
      {trips.length === 0 ? (
        <div className="pf-card">
          <EmptyState
            icon="book"
            title={t("saved.empty")}
            body={t("saved.emptyBody")}
          />
        </div>
      ) : (
        <div className="pf-saved__grid">
          {trips.map((trip) => (
            <button type="button" className="pf-card pf-saved-card" key={trip.id} onClick={() => onOpen?.(trip.id)}>
              <div className="pf-row pf-row--between">
                <Heading level={4}>{trip.destination}</Heading>
                <Badge tone="success" dot>{t("feedback.markedUseful")}</Badge>
              </div>
              <Text size="sm" tone="secondary">{trip.summary}</Text>
              <div className="pf-row pf-row--between">
                <Text size="xs" tone="tertiary">{trip.days ? t(trip.days === 1 ? "workspace.daysOne" : "workspace.days", { count: trip.days }) : "—"} · {new Date(trip.created_at).toLocaleDateString("en-GB")}</Text>
                {trip.stars ? <Badge tone="warning">{`${trip.stars}★`}</Badge> : null}
              </div>
            </button>
          ))}
        </div>
      )}
    </main>
  );
}

/* ----------------------------------------------------------- evaluation -- */

function scoreTone(key: string, value: number): "success" | "warning" | "danger" | "neutral" {
  const counted = key.endsWith("_ms") || key.includes("latency") || key.endsWith("_tokens") || key.endsWith("_calls") || key.includes("token");
  if (counted) return "neutral";
  if (value >= 0.999) return "success";
  if (value > 0) return "warning";
  return "danger";
}

function scoreLabel(key: string, value: number): string {
  const name = key.replaceAll("_", " ");
  if (key.endsWith("_ms") || key.includes("latency")) return `${name} ${Math.round(value)} ms`;
  if (key.endsWith("_tokens") || key.endsWith("_calls") || key.includes("token")) return `${name} ${Math.round(value)}`;
  if (value >= 0 && value <= 1) return `${name} ${Math.round(value * 100)}%`;
  return `${name} ${value}`;
}

function CaseTrace({ item }: { item: EvalCaseResult }) {
  const { t } = useI18n();
  const checks = item.plan_checks;
  const steps = checks?.trajectory ?? [];
  const calls = Object.entries(checks?.tool_log?.calls ?? {});
  const tools = steps.length
    ? []
    : calls.length
      ? calls.map(([name, count]) => `${name} × ${count}`)
      : item.tool_summary;
  const scores = Object.entries(item.scores ?? {});
  return (
    <div className="pf-stack pf-stack--md">
      <span className="pf-row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Badge tone={item.passed ? "success" : "danger"} dot>
          {item.passed ? t("eval.passed") : item.failure_class || t("eval.failed")}
        </Badge>
        {item.latency_ms ? <Badge tone="neutral">{item.latency_ms} ms</Badge> : null}
      </span>
      <dl className="pf-eval-trace">
        <div>
          <dt><Text size="xs" weight="semibold" tone="tertiary">{t("eval.input")}</Text></dt>
          <dd><Text size="sm">{item.input}</Text></dd>
        </div>
        <div>
          <dt><Text size="xs" weight="semibold" tone="tertiary">{t("eval.output")}</Text></dt>
          <dd><Text size="sm">{item.output_summary || t("eval.notLinked")}</Text></dd>
        </div>
        <div>
          <dt><Text size="xs" weight="semibold" tone="tertiary">{t("eval.notes")}</Text></dt>
          <dd><Text size="sm">{item.judge_reason || t("eval.notLinked")}</Text></dd>
        </div>
      </dl>
      {!item.passed ? <SituationReading input={item.input} output={item.output_summary} /> : null}
      {scores.length ? (
        <div className="pf-stack">
          <Text size="xs" weight="semibold" tone="tertiary">{t("eval.components")}</Text>
          <div className="pf-row" style={{ gap: 6, flexWrap: "wrap" }}>
            {scores.map(([key, value]) => (
              <Badge key={key} tone={scoreTone(key, value)}>{scoreLabel(key, value)}</Badge>
            ))}
          </div>
        </div>
      ) : null}
      {checks?.gaps?.length ? (
        <Text size="xs" tone="tertiary">{t("eval.gaps")}: {checks.gaps.join(", ")}</Text>
      ) : null}
      {checks?.constraints?.length ? (
        <div className="pf-stack">
          <Text size="xs" weight="semibold" tone="tertiary">
            {t("eval.checks")} · {checks.constraints_met ?? 0}/{checks.constraints_total ?? checks.constraints.length}
          </Text>
          {checks.constraints.map((row) => (
            <Text key={`${row.kind}-${row.text}`} size="xs">
              {row.met ? t("eval.met") : t("eval.unmet")}: {row.text}{row.evidence ? ` — ${row.evidence}` : ""}
            </Text>
          ))}
        </div>
      ) : null}
      {checks?.disruption ? (
        <Text size="xs">
          {t("eval.recovery")}: {checks.disruption.kind} · {checks.disruption.handled ? t("eval.handled") : t("eval.notHandled")}
          {checks.disruption.outcomes
            ? ` · ${Object.entries(checks.disruption.outcomes).map(([key, value]) => `${key} ${value ? t("eval.met") : t("eval.unmet")}`).join(", ")}`
            : ""}
        </Text>
      ) : null}
      {checks?.grounding && typeof checks.grounding.items === "number" ? (
        <Text size="xs" tone="tertiary">
          {t("eval.groundingLine", { grounded: checks.grounding.grounded ?? 0, items: checks.grounding.items })}
        </Text>
      ) : null}
      <div className="pf-stack">
        <Text size="xs" weight="semibold" tone="tertiary">{t("eval.tools")}</Text>
        {steps.length ? (
          <div className="pf-stack">
            {steps.map((step, index) => (
              <Text key={`${step.tool}-${index}`} size="xs">
                {step.phase} · {step.tool} · {step.status}
                {step.error ? ` · ${step.error}` : ""}
                {step.returned?.length ? ` · ${step.returned.join("; ")}` : ""}
                {step.returned_count && step.returned && step.returned_count > step.returned.length
                  ? ` (+${step.returned_count - step.returned.length})`
                  : ""}
              </Text>
            ))}
          </div>
        ) : tools.length ? (
          <div className="pf-row" style={{ gap: 6, flexWrap: "wrap" }}>
            {tools.map((tool) => <Badge key={tool} tone="neutral">{tool}</Badge>)}
          </div>
        ) : <Text size="xs" tone="tertiary">{t("eval.noTools")}</Text>}
        {(checks?.tool_log?.errors ?? []).map((error) => (
          <Text key={error} size="xs">{error}</Text>
        ))}
      </div>
      {typeof item.judge_score === "number" ? (
        <div className="pf-row" style={{ gap: 6, flexWrap: "wrap" }}>
          <Badge tone={item.judge_score >= 4 ? "success" : item.judge_score >= 3 ? "warning" : "danger"}>
            {t("eval.judgeScore")} {item.judge_score.toFixed(1)} / 5
          </Badge>
          {(item.judge_flags ?? []).map((flag) => <Badge key={flag} tone="neutral">{flag}</Badge>)}
        </div>
      ) : null}
      <Text size="xs" tone="tertiary" className="pf-mono">{t("eval.trace", { id: item.trace_id || t("eval.notLinked") })}</Text>
    </div>
  );
}

export function EvalDashboard({
  datasets,
  runs,
  latest,
  onRun,
  running,
  error,
  onExport,
  onSelectRun,
  opened = {},
  previews = {},
  detail = null,
}: {
  datasets: EvalDataset[];
  runs: EvalRunSummary[];
  latest: EvalRun | null;
  onRun: (dataset: string, tag: string) => void;
  running: boolean;
  error: string | null;
  onExport?: (runId: string) => void;
  onSelectRun?: (runId: string) => void;
  /** Newest scored run for each suite. */
  opened?: Record<string, EvalRun>;
  /** Frozen cases for suites that have not been scored. */
  previews?: Record<string, EvalPreview[]>;
  /** A run the comparison table opened, which may be older than `opened`. */
  detail?: EvalRun | null;
}) {
  const { t } = useI18n();
  const [dataset, setDataset] = useState(latest?.dataset_id ?? "hongkong");
  const [tag, setTag] = useState("baseline-v1");
  const [caseId, setCaseId] = useState<string | null>(null);
  const [caseQuery, setCaseQuery] = useState("");
  const [caseFilter, setCaseFilter] = useState<"all" | "passed" | "failed">("all");
  const [suite, setSuite] = useState("all");
  const catalog = Object.keys(opened).length ? opened : latest ? { [latest.dataset_id]: latest } : {};
  const runFor = (id: string) => (detail?.dataset_id === id ? detail : catalog[id] ?? null);
  const metricRun = suite === "all" ? null : runFor(suite);
  const metrics = metricRun?.metrics ?? [];
  const primaryMetric = metrics.find((metric) => metric.unit === "%")?.key ?? "constraint_satisfaction";
  const snapshot = (metricRun?.model_snapshot ?? {}) as { writer?: string; router?: string; judge?: string };
  const series = metricRun ? runs.filter((run) => run.dataset_id === metricRun.dataset_id) : runs;
  const needle = caseQuery.trim().toLowerCase();
  const groups = datasets
    .map((item) => {
      const run = runFor(item.id);
      const scored = (run?.cases ?? []).map((row) => ({
        key: row.id,
        suiteId: item.id,
        suiteName: item.name,
        scored: true,
        result: row,
        preview: undefined as EvalPreview | undefined,
      }));
      const defined = scored.length
        ? scored
        : (previews[item.id] ?? []).map((row) => ({
            key: `${item.id}:${row.case_id}`,
            suiteId: item.id,
            suiteName: item.name,
            scored: false,
            result: null,
            preview: row,
          }));
      return { id: item.id, name: item.name, run, rows: defined };
    })
    .filter((group) => group.rows.length > 0 && (suite === "all" || group.id === suite));
  const visible = groups
    .map((group) => ({
      ...group,
      rows: group.rows.filter((row) => {
        const passed = row.result?.passed ?? false;
        const caseIdText = row.result?.case_id ?? row.preview?.case_id ?? "";
        const input = row.result?.input ?? row.preview?.input ?? "";
        const output = row.result?.output_summary ?? row.preview?.expected ?? "";
        if (row.scored && caseFilter === "passed" && !passed) return false;
        if (row.scored && caseFilter === "failed" && passed) return false;
        if (!row.scored && caseFilter !== "all") return false;
        if (!needle) return true;
        return caseIdText.toLowerCase().includes(needle) || input.toLowerCase().includes(needle) || output.toLowerCase().includes(needle);
      }),
    }))
    .filter((group) => group.rows.length > 0);
  const failed = visible.reduce((count, group) => count + group.rows.filter((row) => row.result && !row.result.passed).length, 0);
  const selectedCase = visible.flatMap((group) => group.rows).find((row) => row.key === caseId)?.result ?? null;
  const selectedPreview = visible.flatMap((group) => group.rows).find((row) => row.key === caseId)?.preview ?? null;
  useEffect(() => {
    if (detail?.dataset_id) setSuite(detail.dataset_id);
  }, [detail?.id, detail?.dataset_id]);

  return (
    <main className="pf-eval">
      <PageHeader
        eyebrow={t("tab.evaluation")}
        title={t("eval.title")}
        description={t("eval.body")}
        action={
          <div className="pf-row">
            {metricRun ? <Button variant="secondary" size="sm" iconLeft="download" onClick={() => onExport?.(metricRun.id)}>{t("eval.export")}</Button> : null}
            <Badge tone={metricRun || runs.length ? "success" : "warning"} dot>
              {metricRun ? t("eval.runComplete") : runs.length ? t(runs.length === 1 ? "eval.runsOne" : "eval.runs", { count: runs.length }) : t("eval.noRuns")}
            </Badge>
          </div>
        }
      />

      {error ? <div className="pf-banner"><Icon name="alert" size={14} />{error}</div> : null}
      {error ? <WriterDiagnosis text={error} /> : null}

      <div className="pf-card pf-eval__panel">
        <div className="pf-eval__panel-head">
          <div className="pf-stack" style={{ gap: 2 }}>
            <Text size="sm" weight="semibold">{t("eval.experiment")}</Text>
            <Text size="xs" tone="tertiary">{t("eval.experimentBody")}</Text>
          </div>
          <Badge tone="neutral">{t(runs.length === 1 ? "eval.runsOne" : "eval.runs", { count: runs.length })}</Badge>
        </div>
        <div className="pf-run-form">
          <label className="pf-field">
            <span className="pf-field__label">{t("eval.dataset")}</span>
            <Select value={dataset} onChange={(event) => setDataset(event.target.value)}>
              {datasets.map((item) => (
                <option key={item.id} value={item.id}>{item.name} · {t(item.item_count === 1 ? "eval.casesOne" : "eval.cases", { count: item.item_count })}</option>
              ))}
            </Select>
          </label>
          <label className="pf-field">
            <span className="pf-field__label">{t("eval.tag")}</span>
            <Input value={tag} onChange={(event) => setTag(event.target.value)} placeholder="router-tune-v2" />
          </label>
          <Button variant="primary" iconLeft="play" loading={running} onClick={() => onRun(dataset, tag)}>
            {t("eval.run")}
          </Button>
        </div>
      </div>

      {metricRun ? (
        <>
          <div className="pf-eval__metrics">
            {metrics.map((metric) => (
              <MetricCard key={metric.key} metric={metric} />
            ))}
          </div>
          <Text size="xs" tone="tertiary">
            {t("eval.modelLine", {
              writer: snapshot.writer || t("eval.notLinked"),
              router: snapshot.router || t("eval.notLinked"),
              judge: snapshot.judge || t("eval.notLinked"),
              latency: metricRun.latency_ms,
              tokens: metricRun.token_usage,
            })}
          </Text>

          <div className="pf-eval__grid">
            <div className="pf-card pf-eval__panel">
              <div className="pf-eval__panel-head">
                <Text size="sm" weight="semibold">{t("eval.comparison")}</Text>
                <Badge tone="neutral">{metricRun.dataset_id} v{metricRun.dataset_version}</Badge>
              </div>
              <ExperimentTable runs={series} activeId={metricRun.id} onSelect={onSelectRun} />
            </div>
            <div className="pf-card pf-eval__panel">
              <div className="pf-eval__panel-head">
                <Text size="sm" weight="semibold">{t("eval.trend")}</Text>
                <Badge tone="neutral">{primaryMetric.replaceAll("_", " ")}</Badge>
              </div>
              <TrendChart runs={series} metricKey={primaryMetric} />
            </div>
          </div>
        </>
      ) : null}

          <div className="pf-card pf-eval__panel">
            <div className="pf-eval__panel-head">
              <Text size="sm" weight="semibold">{t("eval.casesTitle")}</Text>
              <Badge tone={failed ? "danger" : "success"}>{t("eval.failedCount", { count: failed })}</Badge>
            </div>
            <div className="pf-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Button variant={suite === "all" ? "primary" : "secondary"} size="sm" onClick={() => setSuite("all")}>{t("eval.suiteAll")}</Button>
              {datasets.map((item) => {
                const run = runFor(item.id);
                const passed = run ? run.cases.filter((row) => row.passed).length : null;
                const total = run ? run.cases.length : item.item_count;
                return (
                  <Button
                    key={item.id}
                    variant={suite === item.id ? "primary" : "secondary"}
                    size="sm"
                    onClick={() => {
                      setSuite(item.id);
                      setDataset(item.id);
                    }}
                  >
                    {item.name} {passed === null ? total : `${passed}/${total}`}
                  </Button>
                );
              })}
            </div>
            <div className="pf-row" style={{ gap: 8, flexWrap: "wrap" }}>
              {(["all", "passed", "failed"] as const).map((filter) => (
                <Button key={filter} variant={caseFilter === filter ? "primary" : "secondary"} size="sm" onClick={() => setCaseFilter(filter)}>
                  {t(filter === "all" ? "eval.filterAll" : filter === "passed" ? "eval.filterPassed" : "eval.filterFailed")}
                </Button>
              ))}
              <Input value={caseQuery} onChange={(event) => setCaseQuery(event.target.value)} placeholder={t("eval.find")} aria-label={t("eval.find")} />
            </div>
            {visible.length === 0 ? (
              <EmptyState
                icon={caseFilter === "failed" && !needle ? "check" : "search"}
                title={caseFilter === "failed" && !needle ? t("eval.noFailures") : t("eval.noMatches")}
                body={needle ? t("eval.find") : caseFilter === "failed" ? t("eval.noFailuresBody") : undefined}
              />
            ) : (
              <div>
                {visible.map((group) => (
                  <div key={group.id}>
                    {suite === "all" ? (
                      <div className="pf-eval__panel-head">
                        <Text size="sm" weight="semibold">{group.name}</Text>
                        <Badge tone={group.run ? "neutral" : "warning"}>
                          {group.run
                            ? `${group.run.cases.filter((row) => row.passed).length}/${group.run.cases.length}`
                            : t("eval.notScored")}
                        </Badge>
                      </div>
                    ) : null}
                    {!group.run ? <Text size="xs" tone="tertiary">{t("eval.notScoredBody")}</Text> : null}
                    {group.rows.map((row) => {
                      const item = row.result;
                      const preview = row.preview;
                      const label = item?.case_id ?? preview?.case_id ?? "";
                      const input = item?.input ?? preview?.input ?? "";
                      return (
                        <button key={row.key} type="button" className="pf-eval-case" onClick={() => setCaseId(row.key)}>
                          <span className="pf-row pf-row--between">
                            <Text as="span" size="sm" weight="semibold">{label}</Text>
                            <span className="pf-row" style={{ gap: 6 }}>
                              <Badge tone="neutral">{group.name}</Badge>
                              {item ? (
                                <Badge tone={item.passed ? "success" : "danger"}>{item.passed ? t("eval.passed") : item.failure_class || t("eval.failed")}</Badge>
                              ) : (
                                <Badge tone="warning">{t("eval.notScored")}</Badge>
                              )}
                            </span>
                          </span>
                          <Text as="span" size="xs" tone="secondary">{t("eval.input")}: {input}</Text>
                          <Text as="span" size="xs" tone="tertiary">
                            {item
                              ? `${t("eval.output")}: ${item.output_summary || t("eval.notLinked")}${item.latency_ms ? ` · ${item.latency_ms} ms` : ""}`
                              : t("eval.expectedLine", { value: preview?.expected || t("eval.notLinked") })}
                          </Text>
                          {item?.scores && Object.keys(item.scores).length ? (
                            <span className="pf-row" style={{ gap: 6, flexWrap: "wrap" }}>
                              {Object.entries(item.scores).slice(0, 4).map(([key, value]) => (
                                <Badge key={key} tone={scoreTone(key, value)}>{scoreLabel(key, value)}</Badge>
                              ))}
                            </span>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </div>

      <Drawer
        open={Boolean(selectedCase || selectedPreview)}
        onClose={() => setCaseId(null)}
        title={selectedCase
          ? t("eval.caseTitle", { id: selectedCase.case_id })
          : selectedPreview
            ? t("eval.caseTitle", { id: selectedPreview.case_id })
            : t("eval.case")}
      >
        {selectedCase ? <CaseTrace item={selectedCase} /> : selectedPreview ? (
          <div className="pf-stack pf-stack--md">
            <Badge tone="warning">{t("eval.notScored")}</Badge>
            <Text size="sm">{selectedPreview.input}</Text>
            <Text size="sm">{t("eval.expectedLine", { value: selectedPreview.expected || t("eval.notLinked") })}</Text>
            <Text size="xs" tone="tertiary">{t("eval.notScoredBody")}</Text>
          </div>
        ) : null}
      </Drawer>
    </main>
  );
}

/* ------------------------------------------------------------- settings -- */

/** What the server keeps for one profile (PREFERENCES_MAX and PREFERENCE_MAX_LENGTH in backend/app/interface/api.py). */
const MAX_PREFERENCES = 20;
const MAX_PREFERENCE_LENGTH = 80;

function videoEmbedUrl(note: VideoNote): string {
  if (note.source === "youtube" && note.video_id) {
    return `https://www.youtube-nocookie.com/embed/${note.video_id}`;
  }
  if (note.source === "bilibili" && note.video_id) {
    return `https://player.bilibili.com/player.html?bvid=${note.video_id}&page=1&autoplay=0`;
  }
  return "";
}

export function VideoNotes({ notes }: { notes?: VideoNote[] }) {
  const { t } = useI18n();
  const [active, setActive] = useState<VideoNote | null>(null);
  if (!notes || notes.length === 0) return null;
  const embed = active ? videoEmbedUrl(active) : "";
  const activeHref = safeHref(active?.url);
  return (
    <section className="pf-card pf-videos" aria-label={t("videos.label")}>
      <div className="pf-videos__head">
        <Heading level={4}>{t("videos.title")}</Heading>
        <Text size="xs" tone="tertiary">
          {t("videos.body")}
        </Text>
      </div>
      <div className="pf-videos__grid">
        {notes.map((note) => (
          <button
            type="button"
            className="pf-video"
            key={note.url}
            onClick={() => setActive(note)}
            aria-label={t("videos.play", { title: note.title })}
          >
            <span className="pf-video__thumb">
              {note.thumbnail_url ? (
                <img src={note.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
              ) : (
                <span className="pf-video__blank" />
              )}
              <span className="pf-video__play" aria-hidden="true">▶</span>
            </span>
            <span className="pf-video__title">{note.title}</span>
            <span className="pf-video__source">
              {note.source === "youtube" ? "YouTube" : note.source === "bilibili" ? "Bilibili" : note.source}
            </span>
          </button>
        ))}
      </div>
      <Dialog open={Boolean(active)} onClose={() => setActive(null)} title={active?.title ?? t("videos.video")}>
        {active ? (
          <div className="pf-video__frame">
            {embed ? (
              <iframe
                src={embed}
                title={active.title}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            ) : (
              <Text size="sm">{t("videos.noEmbed")}</Text>
            )}
            {activeHref ? (
              <a className="pf-video__open" href={activeHref} target="_blank" rel="noreferrer">
                {t("videos.openOn", { source: active.source })}
              </a>
            ) : null}
          </div>
        ) : null}
      </Dialog>
    </section>
  );
}

export function GuideNotes({
  items,
  loading,
}: {
  items: GuideChunk[];
  loading?: boolean;
}) {
  const { t } = useI18n();
  return (
    <section className="pf-card pf-guides" aria-label={t("guides.label")}>
      <div className="pf-row pf-row--between">
        <span className="pf-guides__eyebrow">{t("guides.eyebrow")}</span>
        <Badge tone="neutral" dot>{items[0]?.retrieval?.mode ?? t("guides.softContext")}</Badge>
      </div>
      {loading ? (
        <Text size="xs" tone="tertiary">{t("guides.loading")}</Text>
      ) : items.length ? (
        items.map((item) => (
          <article className="pf-guide" key={item.id}>
            <div className="pf-guide__head">
              <strong>{item.section}</strong>
              {safeHref(item.url) ? (
                <a href={safeHref(item.url)} target="_blank" rel="noreferrer" className="pf-guide__source">
                  {item.title} <Icon name="external" size={10} />
                </a>
              ) : (
                <span className="pf-guide__source">{item.title}</span>
              )}
            </div>
            <Text size="xs" tone="secondary">{item.text}</Text>
            <span className="pf-guide__meta">
              {item.stale ? t("guides.stale") : t("guides.verified", { when: item.last_verified ?? t("guides.recently") })}
              {item.license ? ` · ${item.license}` : ""}
            </span>
          </article>
        ))
      ) : (
        <Text size="xs" tone="tertiary">{t("guides.empty")}</Text>
      )}
      <Text size="xs" tone="tertiary" className="pf-guides__note">
        {t("guides.note")}
      </Text>
    </section>
  );
}

export function AuthDialog({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (mode: "login" | "register", email: string, password: string, displayName: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The dialog stays mounted while it is closed, so it forgets what was typed: the next person to open it
  // on a shared computer must not find the last one's address, name or password, or the register mode.
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    if (open) return;
    setMode("login");
    setEmail("");
    setPassword("");
    setDisplayName("");
    setError(null);
  }, [open]);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await onSubmit(mode, email.trim(), password, displayName.trim());
      setPassword("");
    } catch (cause) {
      // A reply that arrives after the dialog was closed has no one to tell, and must not wait for the next opening.
      if (!openRef.current) return;
      // By status, not by the server's wording: its sentences are in English, and an answer about a
      // field that is too short comes as a list, not a sentence.
      const status = cause instanceof ApiError ? cause.status : 0;
      setError(
        status === 401
          ? t("auth.mismatch")
          : status === 409
            ? t("auth.exists")
            : status === 422
              ? t("auth.invalid")
              : failureText(cause, t, "auth.failed"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={mode === "login" ? t("auth.login") : t("auth.register")}>
      <div className="pf-auth">
        <Text size="sm" tone="secondary">
          {t("auth.intro")}
        </Text>
        {mode === "register" ? (
          <Field label={t("auth.name")} htmlFor="pf-auth-name">
            <Input id="pf-auth-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Alex" />
          </Field>
        ) : null}
        <Field label={t("auth.email")} htmlFor="pf-auth-email">
          <Input
            id="pf-auth-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
        </Field>
        <Field label={t("auth.password")} hint={t("auth.passwordHint")} htmlFor="pf-auth-password">
          <Input
            id="pf-auth-password"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !isImeKey(event)) void submit();
            }}
          />
        </Field>
        {error ? <div className="pf-auth__error">{error}</div> : null}
        <Button variant="primary" block loading={busy} onClick={() => void submit()} disabled={!email || password.length < 8}>
          {mode === "login" ? t("auth.login") : t("auth.register")}
        </Button>
        <button
          type="button"
          className="pf-auth__switch"
          onClick={() => {
            setMode((value) => (value === "login" ? "register" : "login"));
            setError(null);
          }}
        >
          {mode === "login" ? t("auth.toRegister") : t("auth.toLogin")}
        </button>
      </div>
    </Dialog>
  );
}

/** Formats an ISO time for the reader's locale; an unparseable value is shown as it came. */
function formatExpiry(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
  } catch {
    return date.toLocaleString();
  }
}

export function ShareDialog({
  open,
  onClose,
  url,
  expiresAt,
  loading = false,
  copied = false,
  revoked = false,
  revoking = false,
  error,
  onCopy,
  onRevoke,
}: {
  open: boolean;
  onClose: () => void;
  /** The full link, or null while it is being created and after it is turned off. */
  url: string | null;
  expiresAt?: string | null;
  loading?: boolean;
  copied?: boolean;
  revoked?: boolean;
  revoking?: boolean;
  error?: string | null;
  onCopy?: () => void;
  onRevoke?: () => void;
}) {
  const { t, locale } = useI18n();
  return (
    <Dialog open={open} onClose={onClose} title={t("share.title")}>
      <div className="pf-share">
        {url ? (
          <>
            <Text size="sm" tone="secondary">
              {t("share.body")}
            </Text>
            <div className="pf-share__link">
              <Input
                readOnly
                value={url}
                aria-label={t("share.link")}
                onFocus={(event) => event.currentTarget.select()}
              />
              <Button size="sm" variant="secondary" onClick={onCopy}>
                {copied ? t("share.copied") : t("share.copy")}
              </Button>
            </div>
            <div className="pf-share__foot">
              {expiresAt ? (
                <Text size="xs" tone="tertiary">
                  {t("share.expires", { date: formatExpiry(expiresAt, locale) })}
                </Text>
              ) : (
                <span />
              )}
              <Button size="sm" variant="danger" loading={revoking} onClick={onRevoke}>
                {t("share.turnOff")}
              </Button>
            </div>
          </>
        ) : loading ? (
          <Text size="sm" tone="secondary">{t("share.creating")}</Text>
        ) : revoked ? (
          <Text size="sm" tone="secondary">
            {t("share.revoked")}
          </Text>
        ) : null}
        {error ? <div className="pf-auth__error">{error}</div> : null}
      </div>
    </Dialog>
  );
}

export type SettingsGroup = "general" | "handoff" | "instructions" | "keys" | "reviewer" | "orchestrator" | "policy" | "redaction" | "freshness" | "system";

/** The signed-in or guest profile: remembered preferences, trip counts, and a way to clear them. */
export function TravellerProfileCard({
  profile,
  signedIn = false,
  onClearMemory,
  onSavePreferences,
  error = null,
}: {
  profile?: ClientProfile | null;
  signedIn?: boolean;
  onClearMemory?: () => void;
  onSavePreferences?: (preferences: string[]) => void;
  error?: string | null;
}) {
  const { t } = useI18n();
  const [preferences, setPreferences] = useState<string[]>(profile?.preferences ?? []);
  const [prefInput, setPrefInput] = useState("");
  useEffect(() => {
    setPreferences(profile?.preferences ?? []);
  }, [profile]);

  const addPreference = () => {
    setPreferences((current) => addTags(current, prefInput, MAX_PREFERENCES, MAX_PREFERENCE_LENGTH));
    setPrefInput("");
  };
  const removePreference = (value: string) => {
    setPreferences((current) => current.filter((entry) => entry !== value));
  };

  return (
    <div className="pf-card pf-eval__panel">
      {error ? <Banner>{error}</Banner> : null}
      <div className="pf-row pf-row--between">
        <Text size="sm" weight="semibold">{t("settings.profile")}</Text>
        <Badge tone={signedIn ? "success" : "neutral"} dot>{signedIn ? t("settings.account") : t("settings.anonymous")}</Badge>
      </div>
      <Text size="xs" tone="tertiary">
        {signedIn ? t("settings.signedIn") : t("settings.guest")}
      </Text>
      <div className="pf-form-grid">
        <Field
          label={t("settings.preferences")}
          hint={t("settings.preferencesLimit", { max: MAX_PREFERENCES, length: MAX_PREFERENCE_LENGTH })}
          wide
          htmlFor="pf-profile-preferences"
        >
          <div className="pf-preference-tags">
            {preferences.map((preference) => (
              <span className="pf-preference-tag" key={preference}>
                {preference}
                <button
                  type="button"
                  aria-label={t("common.remove", { value: preference })}
                  onClick={() => removePreference(preference)}
                >
                  <Icon name="close" size={11} />
                </button>
              </span>
            ))}
            <input
              id="pf-profile-preferences"
              className="pf-preference-tags__input"
              value={prefInput}
              aria-label={t("settings.addPreference")}
              readOnly={preferences.length >= MAX_PREFERENCES}
              placeholder={
                preferences.length >= MAX_PREFERENCES
                  ? t("settings.preferencesFull")
                  : preferences.length
                    ? t("settings.addAnother")
                    : t("settings.preferencesExample")
              }
              onChange={(event) => setPrefInput(event.target.value)}
              onKeyDown={(event) => {
                if ((event.key === "Enter" || event.key === ",") && !isImeKey(event)) {
                  event.preventDefault();
                  addPreference();
                }
                if (event.key === "Backspace" && !prefInput && preferences.length) {
                  setPreferences((current) => current.slice(0, -1));
                }
              }}
            />
          </div>
        </Field>
      </div>
      <div className="pf-row pf-row--between">
        <Text size="xs" tone="tertiary">
          {profile
            ? t("settings.profileCounts", {
                chats: profile.sessions,
                trips: profile.saved_trips,
                destinations: profile.destinations.length,
              })
            : t("settings.loadingProfile")}
        </Text>
        <div className="pf-row" style={{ gap: 6 }}>
          {onSavePreferences ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                const next = addTags(preferences, prefInput, MAX_PREFERENCES, MAX_PREFERENCE_LENGTH);
                addPreference();
                onSavePreferences(next);
              }}
            >
              {t("settings.savePreferences")}
            </Button>
          ) : null}
          {onClearMemory ? (
            <Button size="sm" variant="ghost" onClick={onClearMemory}>
              {t("settings.clearMemory")}
            </Button>
          ) : null}
        </div>
      </div>
      {profile?.destinations.length ? (
        <Text size="xs" tone="tertiary">
          {t("settings.recent", { destinations: profile.destinations.join(" · ") })}
        </Text>
      ) : null}
    </div>
  );
}

const REDACTION_SAMPLE =
  "Plan 4 days in Kyoto from Hong Kong for 2 people, leaving 5 December, budget 8000 HKD. My HKID is Y335 776(5). Call me on 9123 4567 or chan@example.com. The card is 4111 1111 1111 1111.";

function RedactionSettings({
  saved,
  canEdit,
  onSave,
}: {
  saved: RedactionFlags;
  canEdit: boolean;
  onSave?: (body: RedactionFlags) => Promise<void>;
}) {
  const { t } = useI18n();
  const [flags, setFlags] = useState<RedactionFlags>(saved);
  const [sample, setSample] = useState(REDACTION_SAMPLE);
  const [person, setPerson] = useState("");
  const [machine, setMachine] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => setFlags(saved), [saved]);
  const rows: { key: keyof RedactionFlags["patterns"]; title: string; hint: string }[] = [
    { key: "hkid", title: t("settings.redaction.hkid"), hint: t("settings.redaction.hkidHint") },
    { key: "hk_phone", title: t("settings.redaction.phone"), hint: t("settings.redaction.phoneHint") },
    { key: "email", title: t("settings.redaction.email"), hint: t("settings.redaction.emailHint") },
    { key: "card", title: t("settings.redaction.card"), hint: t("settings.redaction.cardHint") },
  ];
  const patterns = flags.patterns ?? DEFAULT_PATTERNS;
  return (
    <div className="pf-card pf-eval__panel">
      <Text size="sm" weight="semibold">{t("settings.redaction.title")}</Text>
      <Text size="xs" tone="tertiary">{t("settings.redaction.body")}</Text>
      {rows.map((row) => (
        <div className="pf-redact__row" key={row.key}>
          <div className="pf-redact__copy">
            <Text size="sm" weight="semibold">{row.title}</Text>
            <Text size="xs" tone="tertiary">{row.hint}</Text>
            <label className="pf-small pf-muted" htmlFor={`pf-redact-${row.key}`}>{t("settings.redaction.pattern")}</label>
            <textarea
              id={`pf-redact-${row.key}`}
              className="pf-input pf-redact__pattern"
              value={patterns[row.key]}
              disabled={!canEdit}
              rows={2}
              spellCheck={false}
              onChange={(event) => setFlags((current) => ({
                ...current,
                patterns: { ...(current.patterns ?? DEFAULT_PATTERNS), [row.key]: event.target.value },
              }))}
            />
          </div>
          <Switch
            label={flags[row.key] ? t("settings.redaction.on") : t("settings.redaction.off")}
            checked={flags[row.key]}
            disabled={!canEdit}
            onChange={(checked) => setFlags((current) => ({ ...current, [row.key]: checked }))}
          />
        </div>
      ))}
      {canEdit ? (
        <div className="pf-admin-tune__actions">
          <Button variant="primary" onClick={() => {
            setError("");
            void onSave?.(flags)?.catch((cause: unknown) => setError(cause instanceof Error ? cause.message : t("settings.redaction.patternInvalid")));
          }}>{t("settings.redaction.save")}</Button>
        </div>
      ) : null}
      <Divider />
      <Text size="sm" weight="semibold">{t("settings.redaction.previewTitle")}</Text>
      <Text size="xs" tone="tertiary">{t("settings.redaction.previewBody")}</Text>
      <textarea
        className="pf-input pf-redact__sample"
        value={sample}
        onChange={(event) => setSample(event.target.value)}
        rows={3}
        aria-label={t("settings.redaction.previewTitle")}
      />
      <div className="pf-admin-tune__actions">
        <Button
          onClick={() => {
            setBusy(true);
            setError("");
            void previewRedaction({
              text: sample,
              hkid: flags.hkid,
              hk_phone: flags.hk_phone,
              email: flags.email,
              card: flags.card,
              hkid_pattern: patterns.hkid,
              hk_phone_pattern: patterns.hk_phone,
              email_pattern: patterns.email,
              card_pattern: patterns.card,
            })
              .then((result) => {
                setPerson(result.person);
                setMachine(result.machine);
              })
              .catch(() => setError(t("settings.redaction.previewFailed")))
              .finally(() => setBusy(false));
          }}
          disabled={busy}
        >
          {t("settings.redaction.preview")}
        </Button>
      </div>
      {error ? <Text size="xs" tone="tertiary">{error}</Text> : null}
      {person ? (
        <div className="pf-redact__out">
          <Text size="xs" weight="semibold">{t("settings.redaction.person")}</Text>
          <pre className="pf-decision__prompt">{person}</pre>
          <Text size="xs" weight="semibold">{t("settings.redaction.machine")}</Text>
          <pre className="pf-decision__prompt">{machine}</pre>
          <Text size="xs" tone="tertiary">{t("settings.redaction.previewNote")}</Text>
        </div>
      ) : null}
    </div>
  );
}

export function SettingsView({
  config,
  injections,
  modelKeys,
  serverKeys,
  error = null,
  admin,
  canEditPolicy = false,
  onSavePolicy,
  onResetPolicy,
  onSaveGate,
  onSaveRedaction,
  onSaveOrchestrator,
  providers = [],
}: {
  providers?: LlmProvider[];
  /** The "Model & API keys" card's data and handlers; the card is left out without it. */
  modelKeys?: Omit<ModelKeysProps, "title" | "body">;
  /** The operator's own key card, for an admin token holder. */
  serverKeys?: Omit<ModelKeysProps, "title" | "body">;
  config: {
    thresholds: Record<string, number | string>;
    freshness_hours: Record<string, number>;
    writer: { available: boolean; model: string; base_url?: string; temperature?: number; max_tokens?: number; reasoning?: string };
    gate?: "jev" | "plain" | "reason";
    poe_ready?: boolean;
    poe?: { key_set: boolean; key_saved: boolean; key_tail: string };
    models?: { plain?: string; reason?: string };
    writer_model?: string;
    reviewer?: ReviewerConfig;
    orchestrator?: {
      jev: { model: string; url: string; key_set: boolean; key_saved: boolean; key_tail: string };
      fast: { model: string; url: string; key_set: boolean; key_saved: boolean; key_tail: string };
    };
    providers: Record<string, string[] | string>;
    locales: string[];
    redaction?: RedactionFlags;
  } | null;
  injections?: { category: string; severity: string; count: number }[];
  /** Why the last load or save failed. It shows above the form, which stays so that nothing typed is lost. */
  error?: string | null;
  /** Operator cards. Receives the filter so each card can show or hide with the group. */
  admin?: (show: (group: SettingsGroup, text: string) => boolean) => ReactNode;
  /** An operator may edit the thresholds and freshness windows. */
  canEditPolicy?: boolean;
  onSavePolicy?: (body: { thresholds: Record<string, number>; freshness_hours: Record<string, number> }) => Promise<void>;
  onResetPolicy?: () => Promise<void>;
  /** Saves which model chooses the path. */
  onSaveGate?: (gate: "jev" | "plain" | "reason") => Promise<void>;
  onSaveRedaction?: (body: RedactionFlags) => Promise<void>;
  /** Saves one orchestrator model's address and key. */
  onSaveOrchestrator?: (body: {
    jev?: { model: string; url: string; api_key?: string; clear_key?: boolean };
    fast?: { model: string; url: string; api_key?: string; clear_key?: boolean };
    plain_model?: string;
    reason_model?: string;
    poe?: { api_key?: string; clear_key?: boolean };
    plain?: {
      provider: string;
      model: string;
      base_url: string;
      api_key?: string;
      clear_key?: boolean;
      temperature: number;
      max_tokens: number;
      reasoning: string;
    };
    reason?: {
      provider: string;
      model: string;
      base_url: string;
      api_key?: string;
      clear_key?: boolean;
      temperature: number;
      max_tokens: number;
      reasoning: string;
    };
    reviewer?: ReviewerBody;
    writer_call?: { temperature: number; max_tokens: number; reasoning: string };
  }) => Promise<void>;
}) {
  const { t } = useI18n();
  const [helpOpen, setHelpOpen] = useState(false);
  const [draftThresholds, setDraftThresholds] = useState<Record<string, string>>({});
  const [draftFreshness, setDraftFreshness] = useState<Record<string, string>>({});
  const [policyError, setPolicyError] = useState<string | null>(null);
  useEffect(() => {
    if (!config) return;
    setDraftThresholds(Object.fromEntries(Object.entries(config.thresholds).map(([key, value]) => [key, String(value)])));
    setDraftFreshness(Object.fromEntries(Object.entries(config.freshness_hours).map(([key, value]) => [key, String(value)])));
  }, [config]);
  const savePolicy = async () => {
    if (!onSavePolicy) return;
    setPolicyError(null);
    const thresholds = Object.fromEntries(Object.entries(draftThresholds).map(([key, value]) => [key, Number(value)]));
    const freshness_hours = Object.fromEntries(Object.entries(draftFreshness).map(([key, value]) => [key, Number(value)]));
    if (Object.values(thresholds).some((value) => Number.isNaN(value)) || Object.values(freshness_hours).some((value) => Number.isNaN(value))) {
      setPolicyError(t("settings.policyInvalid"));
      return;
    }
    try {
      await onSavePolicy({ thresholds, freshness_hours });
    } catch (cause) {
      setPolicyError(String(cause));
    }
  };
  const [section, setSection] = useState<SettingsGroup>("general");
  const [settingQuery, setSettingQuery] = useState("");
  // Read-only until the person focuses it. A password field lower on the page makes the browser
  // drop a saved username into the first text box, which then hides every card.
  const [findReady, setFindReady] = useState(false);
  const show = (group: SettingsGroup, text: string) => {
    const needle = settingQuery.trim().toLowerCase();
    if (!needle) return section === group;
    return text.toLowerCase().includes(needle);
  };
  const groupLabel = (group: SettingsGroup) => {
    if (group === "handoff") return t("settings.group.handoff");
    if (group === "instructions") return t("settings.group.instructions");
    if (group === "keys") return t("settings.group.keys");
    if (group === "reviewer") return t("settings.group.reviewer");
    if (group === "orchestrator") return t("settings.group.orchestrator");
    if (group === "policy") return t("settings.group.policy");
    if (group === "redaction") return t("settings.group.redaction");
    if (group === "freshness") return t("settings.group.freshness");
    if (group === "system") return t("settings.group.system");
    return t("settings.group.general");
  };
  const clusters = (
    [
      ["general", ...(admin ? (["handoff", "instructions"] as const) : [])],
      [
        ...(config ? (["orchestrator"] as const) : []),
        ...(modelKeys ? (["keys"] as const) : []),
        ...(config ? (["reviewer"] as const) : []),
      ],
      ...(config ? [["policy", "redaction", "freshness", "system"] as const] : []),
    ] as SettingsGroup[][]
  ).filter((cluster) => cluster.length > 0);
  return (
    <main className="pf-settings">
      <PageHeader
        eyebrow={t("settings.eyebrow")}
        title={t("settings.title")}
        description={t("settings.body")}
        action={<Button variant="secondary" size="sm" iconLeft="book" onClick={() => setHelpOpen(true)}>{t("settings.howItWorks")}</Button>}
      />
      {error ? <Banner>{error}</Banner> : null}
      <div className="pf-settings__browse">
        <input
          className="pf-input pf-settings__find"
          type="search"
          name="pf-settings-find"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          readOnly={!findReady}
          value={settingQuery}
          onFocus={() => setFindReady(true)}
          onBlur={() => setFindReady(false)}
          onChange={(event) => {
            if (event.currentTarget.readOnly) {
              event.currentTarget.value = settingQuery;
              return;
            }
            setSettingQuery(event.target.value);
          }}
          placeholder={t("settings.find")}
          aria-label={t("settings.find")}
        />
        <div className="pf-settings__nav" role="tablist" aria-label={t("settings.title")}>
          {clusters.map((cluster, index) => (
            <Fragment key={cluster[0]}>
              {index > 0 ? <span className="pf-settings__split" aria-hidden="true" /> : null}
              <div className="pf-settings__cluster">
                {cluster.map((group) => (
                  <button
                    key={group}
                    type="button"
                    role="tab"
                    aria-selected={!settingQuery && section === group}
                    data-active={!settingQuery && section === group}
                    onClick={() => {
                      setSection(group);
                      setSettingQuery("");
                    }}
                  >
                    {groupLabel(group)}
                  </button>
                ))}
              </div>
            </Fragment>
          ))}
        </div>
      </div>
      {show("general", `${t("settings.developer")} ${t("settings.developerBody")}`) ? (
      <div className="pf-card pf-eval__panel">
        <div className="pf-row pf-row--between">
          <Text size="sm" weight="semibold">{t("settings.developer")}</Text>
          <DeveloperToggle />
        </div>
        <Text size="xs" tone="tertiary">{t("settings.developerBody")}</Text>
      </div>
      ) : null}
      {admin ? admin(show) : null}
      {/* The keys do not depend on the server's configuration either. */}
      {show("keys", `${t("settings.group.keys")} ${t("settings.writer.callBody")} ${t("settings.orch.clearKey")} ${t("settings.orch.temperature")}`) && modelKeys ? (
        <WriterSettings
          providerId={modelKeys.keys.find((key) => key.active)?.provider || "poe"}
          model={modelKeys.keys.find((key) => key.active)?.model || config?.writer?.model || ""}
          baseUrl={modelKeys.keys.find((key) => key.active)?.base_url || ""}
          temperature={config?.writer?.temperature ?? 0.2}
          maxTokens={config?.writer?.max_tokens ?? 4096}
          reasoning={config?.writer?.reasoning || "none"}
          activeKey={modelKeys.keys.find((key) => key.active) ?? null}
          usage={modelKeys.status}
          providers={providers}
          canEdit
          onSaveCall={async (body) => { await onSaveOrchestrator?.({ writer_call: body }); }}
          onSaveKey={async (body) => {
            const saved = await modelKeys.onSave(body);
            return saved !== null;
          }}
          onUseEnvironment={async () => {
            await modelKeys.onChoose(null);
            if (serverKeys) await serverKeys.onChoose(null);
          }}
        />
      ) : null}
      {config ? (
        <>
          {show("orchestrator", `${t("settings.orch")} ${t("settings.orchBody")} ${t("settings.orch.poeTitle")} ${t("settings.gate.jev")} ${t("settings.gate.plain")} ${t("settings.gate.reason")}`) ? (
            <OrchestratorSettings
              config={config}
              providers={providers}
              canEdit={canEditPolicy}
              onSaveGate={onSaveGate}
              onSaveSide={onSaveOrchestrator}
            />
          ) : null}
          {show("reviewer", `${t("settings.group.reviewer")} ${t("settings.reviewer.body")} ${t("settings.reviewer.rounds")} ${t("settings.reviewer.modelHint")}`) ? (
            <ReviewerSettings
              reviewer={config.reviewer ?? {
                provider: "poe",
                model: "",
                base_url: "",
                temperature: 0,
                max_tokens: 4096,
                reasoning: "none",
                rounds: Number(config.thresholds.rule_passes ?? 3),
                key_set: false,
                key_saved: false,
                key_tail: "",
                key_source: "none",
              }}
              providers={providers}
              canEdit={canEditPolicy}
              onSave={async (body) => { await onSaveOrchestrator?.({ reviewer: body }); }}
            />
          ) : null}
          {show("policy", `${t("settings.thresholds")} ${t("settings.threshold.router_confidence")} ${t("settings.threshold.memory_similarity")} ${t("settings.threshold.preplan_budget_seconds")} ${t("settings.threshold.max_repairs")} ${t("settings.threshold.conversation_turns")} ${t("settings.threshold.reviewer_timeout_seconds")} ${t("settings.threshold.turn_deadline_seconds")} ${t("settings.threshold.llm_timeout_seconds")}`) ? (
          <div className="pf-card pf-eval__panel">
            <Text size="sm" weight="semibold">{t("settings.thresholds")}</Text>
            {canEditPolicy ? (
              <div className="pf-form-grid">
                {Object.keys(config.thresholds).filter((key) => key !== "rule_passes").map((key) => (
                  <Field
                    key={key}
                    wide
                    htmlFor={`pf-threshold-${key}`}
                    label={t(`settings.threshold.${key}` as "settings.threshold.router_confidence")}
                    hint={t(`settings.thresholdHint.${key}` as "settings.thresholdHint.router_confidence")}
                  >
                    <Input id={`pf-threshold-${key}`} value={draftThresholds[key] ?? ""} onChange={(event) => setDraftThresholds((current) => ({ ...current, [key]: event.target.value }))} inputMode="decimal" />
                  </Field>
                ))}
              </div>
            ) : (
              Object.entries(config.thresholds).filter(([key]) => key !== "rule_passes").map(([key, value]) => (
                <div className="pf-row pf-row--between" key={key}>
                  <span className="pf-small pf-muted">{t(`settings.threshold.${key}` as "settings.threshold.router_confidence")}</span>
                  <span className="pf-mono pf-small">{String(value)}</span>
                </div>
              ))
            )}
          </div>
          ) : null}
          {show("redaction", `${t("settings.redaction.title")} ${t("settings.redaction.body")} ${t("settings.redaction.previewTitle")}`) ? (
            <RedactionSettings
              saved={config.redaction ?? { hkid: true, hk_phone: true, email: true, card: true, patterns: { ...DEFAULT_PATTERNS } }}
              canEdit={canEditPolicy}
              onSave={async (body) => {
                await onSaveRedaction?.(body);
                setRedaction(body);
              }}
            />
          ) : null}
          {show("freshness", `${t("settings.freshness")} ${t("settings.freshnessBody")} ${t("settings.fresh.weather")} ${t("settings.fresh.tickets")} ${t("settings.fresh.hotels")} ${t("settings.fresh.attractions")}`) ? (
          <div className="pf-card pf-eval__panel">
            <Text size="sm" weight="semibold">{t("settings.freshness")}</Text>
            <Text size="xs" tone="tertiary">{t("settings.freshnessBody")}</Text>
            {canEditPolicy ? (
              <div className="pf-form-grid">
                {Object.keys(config.freshness_hours).map((key) => (
                  <Field
                    key={key}
                    wide
                    htmlFor={`pf-fresh-${key}`}
                    label={t(`settings.fresh.${key}` as "settings.fresh.weather")}
                    hint={t(`settings.freshHint.${key}` as "settings.freshHint.weather")}
                  >
                    <Input id={`pf-fresh-${key}`} value={draftFreshness[key] ?? ""} onChange={(event) => setDraftFreshness((current) => ({ ...current, [key]: event.target.value }))} inputMode="numeric" />
                  </Field>
                ))}
              </div>
            ) : (
              Object.entries(config.freshness_hours).map(([key, value]) => (
                <div className="pf-row pf-row--between" key={key}>
                  <span className="pf-small pf-muted">{t(`settings.fresh.${key}` as "settings.fresh.weather")}</span>
                  <span className="pf-mono pf-small">{t("settings.hours", { count: value })}</span>
                </div>
              ))
            )}
            {canEditPolicy ? (
              <div className="pf-admin-tune__actions">
                {policyError ? <Text size="xs" tone="tertiary">{policyError}</Text> : null}
                <Button variant="primary" onClick={() => void savePolicy()}>{t("settings.policySave")}</Button>
                <Button onClick={() => void onResetPolicy?.()}>{t("settings.policyReset")}</Button>
              </div>
            ) : null}
          </div>
          ) : null}
          {show("system", `${t("settings.providers")} ${t("settings.injections")} ${t("settings.writer")} ${t("settings.serp.title")} ${t("settings.serp.body")}`) ? (
          <>
          <div className="pf-card pf-eval__panel">
            {/* With the keys card the writer is its status line; without it (no keys API) the old line stays. */}
            {modelKeys ? null : (
              <>
                <div className="pf-row pf-row--between">
                  <Text size="sm" weight="semibold">{t("settings.writer")}</Text>
                  <Badge tone={config.writer.available ? "success" : "warning"} dot>{config.writer.available ? t("settings.available") : t("settings.offline")}</Badge>
                </div>
                <Text size="sm" tone="secondary" className="pf-mono">{config.writer.model}</Text>
                <Divider />
              </>
            )}
            <Text size="sm" weight="semibold">{t("settings.providers")}</Text>
            {Object.entries(config.providers).map(([capability, chains]) => (
              <div className="pf-row pf-row--between" key={capability}>
                <span className="pf-small pf-muted">{capability}</span>
                <span className="pf-small" style={{ textAlign: "right" }}>
                  {Array.isArray(chains) ? chains.join(" → ") || t("settings.none") : String(chains)}
                </span>
              </div>
            ))}
          </div>
          <SerpApiRotation canEdit={canEditPolicy} />
          <div className="pf-card pf-eval__panel">
            <Text size="sm" weight="semibold">{t("settings.injections")}</Text>
            <Text size="xs" tone="tertiary">{t("settings.injectionsBody")}</Text>
            {(injections ?? []).length ? (
              injections?.map((event) => (
                <div className="pf-row pf-row--between" key={event.category}>
                  <span className="pf-small">{event.category}</span>
                  <Badge tone={event.severity === "high" ? "danger" : "warning"}>{event.count}</Badge>
                </div>
              ))
            ) : (
              <Text size="xs" tone="tertiary">{t("settings.noInjections")}</Text>
            )}
          </div>
          </>
          ) : null}
        </>
      ) : (
        <div className="pf-card"><EmptyState title={t("settings.unavailable")} body={t("settings.unavailableBody")} /></div>
      )}
      <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} title={t("help.title")}>
        <div className="pf-stack pf-stack--md">
          <Text size="sm">{t("settings.help1")}</Text>
          <Text size="sm">{t("settings.help2")}</Text>
          <Text size="sm">{t("settings.help3")}</Text>
          <Text size="sm">{t("settings.help4")}</Text>
        </div>
      </Dialog>
    </main>
  );
}

/* -------------------------------------------------------------- utility -- */

export function LoadingScreen({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="pf-empty" style={{ minHeight: "60vh" }}>
      <span className="pf-spinner" data-size="lg" />
      <Text size="sm" tone="tertiary">{label ?? t("common.loadingApp")}</Text>
    </div>
  );
}

export function Banner({ tone = "danger", children }: { tone?: "danger" | "info"; children: ReactNode }) {
  return (
    <div className="pf-banner" data-tone={tone} role={tone === "danger" ? "alert" : "status"}>
      <Icon name={tone === "danger" ? "alert" : "info"} size={14} />
      <span>{children}</span>
    </div>
  );
}

export { IntentBadge, StatusPill, AgentChip, EmptyState, PageHeader, Divider };
export type { AgentStatus };
