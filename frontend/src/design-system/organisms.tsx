import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { TripMap } from "./maps/TripMap";
import { kmBetween, MODE, modeForDistance, type TravelMode } from "./maps/geo";
import { CLOCK_MAX, CLOCK_MIN, CLOCK_STEP, clockText, minutesOf } from "./maps/time";
import { Avatar, Badge, Button, Dialog, Divider, Drawer, Heading, IconButton, Input, Select, Text } from "./atoms";
import { AgentChip, EmptyState, IntentBadge, StatusPill, type AgentStatus } from "./elements";
import { timelineAt } from "../lib/mapTimeline";
import { Icon } from "./icons";
import {
  AgentActivity,
  BudgetPanel,
  ChatBubble,
  ClarifyCard,
  ConstraintList,
  DaySection,
  DayStrip,
  ExperimentTable,
  GapList,
  MetricCard,
  MiniStat,
  Field,
  PageHeader,
  PlaceCard,
  PlanFeedback,
  StatusSummary,
  TrendChart,
  TripContextForm,
  SearchField,
} from "./molecules";
import type {
  AgentState,
  AuthUser,
  ChatMessage,
  ClarifyPayload,
  ClientProfile,
  EvalDataset,
  EvalRun,
  EvalRunSummary,
  FxSnapshot,
  GuideChunk,
  PlanItem,
  TripContext,
  TripPlan,
  RunTrace,
  TrendingPlace,
  TrendingTrace,
  VideoNote,
} from "./types";
import { hotelScopeItem } from "./scope";
import { getLegs, type MapLeg } from "../lib/api";
import { safeHref } from "../lib/safeHref";

/* --------------------------------------------------------------- header -- */

export type AppTab = "plan" | "saved" | "evaluation" | "settings";

const TABS: { id: AppTab; label: string; icon: "compass" | "book" | "route" | "shield" }[] = [
  { id: "plan", label: "Plan", icon: "compass" },
  { id: "saved", label: "My trips", icon: "book" },
  { id: "evaluation", label: "Evaluation", icon: "route" },
  { id: "settings", label: "Settings", icon: "shield" },
];

export function AppHeader({
  activeTab,
  onTabChange,
  locale,
  onLocaleChange,
  onHelp,
  onNewTrip,
  onExport,
  onShare,
  user,
  onAuth,
  onLogout,
}: {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  locale: string;
  onLocaleChange: (locale: string) => void;
  onHelp?: () => void;
  onNewTrip?: () => void;
  onExport?: () => void;
  onShare?: () => void;
  user?: AuthUser | null;
  onAuth?: () => void;
  onLogout?: () => void;
}) {
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!accountOpen) return;
    const close = (event: PointerEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [accountOpen]);

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
        <div className="pf-tabs" role="tablist" aria-label="Sections">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              className="pf-tab"
              data-active={activeTab === tab.id}
              aria-selected={activeTab === tab.id}
              onClick={() => onTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="pf-header__actions">
          <select className="pf-lang" aria-label="Language" value={locale} onChange={(event) => onLocaleChange(event.target.value)}>
            <option value="en">EN</option>
            <option value="zh-Hans">简</option>
            <option value="zh-Hant">繁</option>
          </select>
          {onExport ? (
            <Button size="sm" variant="ghost" iconLeft="download" onClick={onExport}>
              Export
            </Button>
          ) : null}
          {onShare ? (
            <Button size="sm" variant="ghost" iconLeft="external" onClick={onShare}>
              Share
            </Button>
          ) : null}
          {onNewTrip ? (
            <Button size="sm" variant="secondary" iconLeft="plus" onClick={onNewTrip}>
              New trip
            </Button>
          ) : null}
          <IconButton icon="book" label="Help and glossary" onClick={onHelp} />
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
                    <Icon name="book" size={14} /> My trips
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
                    <Icon name="shield" size={14} /> Settings
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
                    <Icon name="logout" size={14} /> Log out
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <Button size="sm" variant="secondary" iconLeft="user" onClick={onAuth}>
              Log in
            </Button>
          )}
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
  const [open, setOpen] = useState(initialOpen);
  const title = plan?.destination.name || context.destination || "Set the trip details";
  const dates = plan
    ? `${plan.dates.start} → ${plan.dates.end}`
    : context.start_date && context.end_date
      ? `${context.start_date} → ${context.end_date}`
      : "Dates not set";
  const party = plan?.party_size ?? context.party_size;
  const budget = plan?.budget
    ? `${plan.budget.currency} ${plan.budget.amount.toLocaleString()}`
    : context.budget
      ? `${context.currency ?? ""} ${context.budget.toLocaleString()}`
      : "No budget";
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
          <span className="pf-trip-bar__kicker">Trip details · tap to edit</span>
          <span className="pf-trip-bar__title">{title}</span>
          <span className="pf-trip-bar__meta">
            {dates} · {party ? `${party} travellers` : "party not set"} · {budget}
          </span>
        </span>
        <span className="pf-trip-bar__action">
          Edit <Icon name="chevronRight" size={13} />
        </span>
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Trip details">
        <div className="pf-trip-drawer">
          <Text size="sm" tone="secondary">
            Use the assistant directly, or set the hard facts here. The form is optional; the
            assistant can extract exactly the same fields from one message.
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

const QUICK_PROMPTS = ["Plan my trip", "Make it cheaper", "What is the weather on day 2?", "Add a museum"];

export function ChatPanel({
  messages,
  onSend,
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
}: {
  messages: ChatMessage[];
  onSend: (message: string) => void;
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
}) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastContent = messages[messages.length - 1]?.content ?? "";
  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, lastContent, busy, clarify?.question]);
  const submit = () => {
    const value = draft.trim();
    if (!value || busy) return;
    setDraft("");
    onSend(value);
  };
  const running = Object.values(agents).filter((agent) => agent.status === "running").length;
  return (
    <section className="pf-card pf-chat" aria-label="Chat">
      <div className="pf-chat__head">
        <div className="pf-row">
          <span className="pf-logo__mark" style={{ width: 26, height: 26, background: "var(--p30-tone-brand-primary)" }}>
            <Icon name="sparkles" size={14} />
          </span>
          <div className="pf-stack" style={{ gap: 0 }}>
            <Text size="sm" weight="semibold">
              Trip assistant
            </Text>
            <Text size="xs" tone="tertiary">
              One path per message
            </Text>
          </div>
        </div>
        <div className="pf-row" style={{ gap: 4 }}>
          <Badge tone={busy ? "busy" : "success"} dot>
            {busy ? "Working" : "Ready"}
          </Badge>
          {onClear && messages.length > 0 ? (
            <IconButton
              icon="trash"
              label="Clear chat"
              onClick={() => {
                if (window.confirm("Clear this chat? The itinerary and saved memory are kept.")) onClear();
              }}
            />
          ) : null}
        </div>
      </div>
      <div className="pf-chat__messages pf-scroll" ref={scrollRef}>
        {messages.length === 0 ? (
          <EmptyState
            icon="compass"
            title="Where are we going?"
            body="Tell me the destination, dates, travellers and budget. I will gather live sources and build the itinerary."
          />
        ) : (
          messages.map((message) => (
            <ChatBubble
              key={message.id}
              role={message.role === "user" ? "user" : "assistant"}
              intent={message.intent ?? undefined}
              time={new Date(message.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
              above={
                typeof message.meta?.scope === "string" ? (
                  <span className="pf-scope-chip">{String(message.meta.scope)}</span>
                ) : null
              }
              meta={
                message.meta && typeof message.meta.version === "number" ? (
                  <span>v{String(message.meta.version)}</span>
                ) : null
              }
            >
              {message.content}
            </ChatBubble>
          ))
        )}
        {error ? (
          <div className="pf-banner">
            <Icon name="alert" size={14} />
            <div className="pf-stack" style={{ gap: 4 }}>
              <span>{error}</span>
              {onRetry ? (
                <Button size="sm" variant="ghost" onClick={onRetry} iconLeft="refresh">
                  Retry
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
        {clarify ? (
          <ClarifyCard
            payload={clarify}
            values={clarifyValues}
            onChange={onClarifyChange}
            onSubmit={onClarifySubmit}
            submitting={busy}
          />
        ) : null}
        {busy && (running > 0 || progress > 0) ? (
          <AgentActivity agents={agents} progress={progress} message={progressMessage || "Working on your trip"} />
        ) : null}
      </div>
      <div className="pf-chat__composer">
        {scope ? (
          <span className="pf-scope-chip">
            <Icon name="map" size={12} />
            About: {scope}
            <button type="button" aria-label="Clear selected stop" onClick={onClearScope} style={{ border: 0, background: "transparent", padding: 0 }}>
              <Icon name="close" size={12} />
            </button>
          </span>
        ) : null}
        <div className="pf-quick">
          {QUICK_PROMPTS.map((prompt) => (
            <button key={prompt} type="button" className="pf-chip" disabled={busy} onClick={() => onSend(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
        <div className="pf-composer">
          <textarea
            value={draft}
            rows={2}
            placeholder={scope ? "Ask about this stop, or ask for a change…" : "Ask for a change, or a quick question…"}
            aria-label="Message"
            onChange={(event) => setDraft(event.target.value)}
            onInput={(event) => {
              const el = event.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
          />
          <Button variant="primary" data-round="true" aria-label="Send message" disabled={busy || !draft.trim()} onClick={submit}>
            <Icon name="send" size={16} />
          </Button>
        </div>
        <div className="pf-row pf-row--between pf-small pf-muted">
          <span>Enter to send · Shift + Enter for a new line</span>
          <span className="pf-row" style={{ gap: 5 }} title="Your message is handled as data, not as instructions">
            <Icon name="shield" size={12} />
          </span>
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
}: {
  title: string;
  icon: "wallet" | "check" | "ticket" | "alert" | "info";
  badge?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="pf-collapsible">
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

export function TripWorkspace({
  plan,
  versions,
  activeVersion,
  onVersionChange,
  selectedItemId,
  onSelectItem,
  onUseful,
  onNotUseful,
  feedbackStars,
  feedbackVerdict,
  feedbackAdded = [],
  trace,
  onRefresh,
  onFocusDay,
  updatedIds,
  hotelChanged = false,
}: {
  plan: TripPlan;
  versions: { version: number; change_summary: string; created_at: string }[];
  activeVersion: number;
  onVersionChange: (version: number) => void;
  selectedItemId: string | null;
  onSelectItem: (item: PlanItem | null) => void;
  onUseful: (stars: number) => void;
  onNotUseful: () => void;
  feedbackStars: number | null;
  feedbackVerdict: "useful" | "not_useful" | null;
  feedbackAdded?: string[];
  onStars?: (stars: number) => void;
  trace?: RunTrace | null;
  onRefresh?: () => void;
  onFocusDay?: (date: string) => void;
  updatedIds?: string[];
  hotelChanged?: boolean;
}) {
  const [activeDay, setActiveDay] = useState(plan.days[0]?.date ?? "");
  const dayRefs = useRef<Record<string, HTMLElement | null>>({});
  const hotel = plan.hotels[0];
  const bookingHref = safeHref(hotel?.booking_url);
  const unknownGaps = plan.data_quality.gaps.length;
  const firstUpdated = updatedIds?.[0] ?? "";

  const scrollToDay = (date: string) => {
    const node = dayRefs.current[date];
    if (!node) return;
    if (window.matchMedia("(max-width: 899px)").matches) {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const scroller = node.closest(".pf-workspace__body") as HTMLElement | null;
    if (scroller) {
      const top =
        node.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 8;
      scroller.scrollTo({ top, behavior: "smooth" });
    }
  };

  const jump = (date: string) => {
    setActiveDay(date);
    onFocusDay?.(date);
    scrollToDay(date);
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

  return (
    <section className="pf-card pf-workspace" aria-label="Itinerary">
      <div className="pf-workspace__head">
        <div className="pf-workspace__title">
          <div className="pf-stack" style={{ gap: 3 }}>
            <div className="pf-row">
              <Heading level={3}>{plan.destination.name}</Heading>
              <Badge tone="brand">v{plan.version}</Badge>
              {updatedIds?.length ? (
                <Badge tone="success" dot>{updatedIds.length} updated</Badge>
              ) : hotelChanged ? (
                <Badge tone="success" dot>Hotel updated</Badge>
              ) : null}
            </div>
            {plan.destination.timezone ? (
              <Text size="xs" tone="tertiary" title={plan.destination.timezone}>
                {localClock(plan.destination.timezone)}
              </Text>
            ) : null}
            <Text size="sm" tone="secondary">
              {plan.dates.start} → {plan.dates.end} · {plan.party_size} travellers
              {plan.origin ? ` · from ${plan.origin}` : ""}
            </Text>
          </div>
          <div className="pf-stack" style={{ gap: 6, alignItems: "flex-end" }}>
            <PlanFeedback
              verdict={feedbackVerdict}
              stars={feedbackStars}
              added={feedbackAdded}
              onUseful={onUseful}
              onNotUseful={onNotUseful}
            />
            <StatusSummary plan={plan} />
            <span className="pf-version">
              Version
              <select value={activeVersion} onChange={(event) => onVersionChange(Number(event.target.value))}>
                {versions.map((version) => (
                  <option key={version.version} value={version.version}>
                    v{version.version}
                  </option>
                ))}
              </select>
            </span>
          </div>
        </div>
        <div className="pf-workspace__meta">
          <MiniStat icon="calendar" label="Dates" value={`${plan.days.length} days`} />
          <MiniStat icon="users" label="Party" value={String(plan.party_size)} />
          {plan.budget ? <MiniStat icon="wallet" label="Budget" value={`${plan.cost.currency} ${plan.budget.amount.toLocaleString()}`} /> : null}
          {onRefresh ? <Button size="sm" variant="ghost" iconLeft="refresh" onClick={onRefresh}>Refresh data</Button> : null}
        </div>
        <DayStrip days={plan.days} active={activeDay} onSelect={jump} />
      </div>
      <div className="pf-workspace__body pf-scroll">
        {plan.days.map((day, index) => (
          <div key={day.date} ref={(node) => { dayRefs.current[day.date] = node; }}>
            <DaySection
              day={day}
              index={index}
              selectedItemId={selectedItemId}
              updatedIds={new Set(updatedIds ?? [])}
              onSelectItem={(item) => onSelectItem(item)}
            />
          </div>
        ))}

        {hotel ? (
          <div
            className="pf-hotel-card"
            data-updated={hotelChanged}
            data-selected={selectedItemId === hotel.id}
            role="button"
            tabIndex={0}
            title="Select this stay to ask about it"
            onClick={() => onSelectItem(selectedItemId === hotel.id ? null : hotelScopeItem(hotel))}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              onSelectItem(selectedItemId === hotel.id ? null : hotelScopeItem(hotel));
            }}
          >
            <span className="pf-hotel-card__thumb"><Icon name="hotel" size={20} /></span>
            <div className="pf-stack pf-grow" style={{ gap: 2 }}>
              <Text size="sm" weight="semibold">
                {hotel.name}
                {hotelChanged ? <Badge tone="success" dot>Updated</Badge> : null}
              </Text>
              <Text size="xs" tone="tertiary">
                {hotel.area || "Area not returned"} · {hotel.rating ? `${hotel.rating} / 5` : "No rating"}
                {hotel.distance_to_centre_km !== null ? ` · ${hotel.distance_to_centre_km} km to centre` : ""}
              </Text>
              <Text size="xs" tone="secondary">
                {hotel.price_per_night
                  ? `${hotel.price_per_night.currency} ${hotel.price_per_night.amount.toLocaleString()} per night · ${hotel.price_kind}`
                  : "No rate returned"}
              </Text>
            </div>
            <div className="pf-stack" style={{ alignItems: "flex-end", gap: 4 }}>
              <StatusPill status={hotel.data_status} />
              {bookingHref ? <a className="pf-small" href={bookingHref} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Check hotel</a> : null}
            </div>
          </div>
        ) : null}

        {plan.tickets.map((ticket) => (
          <div className="pf-ticket-card" key={ticket.id}>
            <span className="pf-ticket-card__thumb"><Icon name={ticket.mode === "flight" ? "plane" : "train"} size={20} /></span>
            <div className="pf-stack pf-grow" style={{ gap: 2 }}>
              <Text size="sm" weight="semibold">{ticket.name}</Text>
              <Text size="xs" tone="tertiary">
                {ticket.return_depart_at ? "Outbound · " : ""}
                {ticket.origin} → {ticket.destination}
                {ticket.depart_at ? ` · ${ticket.depart_at.replace("T", " ").slice(0, 16)}` : ""}
              </Text>
              {ticket.return_depart_at ? (
                <Text size="xs" tone="tertiary">
                  Return{ticket.return_airline ? ` · ${ticket.return_airline} ${ticket.return_flight_number ?? ""}` : ""}
                  {` · ${ticket.return_origin} → ${ticket.return_destination} · ${ticket.return_depart_at.replace("T", " ").slice(0, 16)}`}
                </Text>
              ) : null}
              <Text size="xs" tone="secondary">
                {ticket.price ? `${ticket.price.currency} ${ticket.price.amount.toLocaleString()} · ${ticket.price_kind}` : "No fare returned"}
              </Text>
            </div>
            <StatusPill status={ticket.data_status} />
          </div>
        ))}

        <div className="pf-stack" style={{ gap: 0, marginTop: 8 }}>
          <Collapsible title="Cost" icon="wallet" defaultOpen>
            <BudgetPanel cost={plan.cost} />
          </Collapsible>
          <Collapsible title="Constraint checks" icon="check" badge={<Badge tone={plan.constraints.some((c) => c.status === "fail") ? "danger" : "success"}>{plan.constraints.length}</Badge>}>
            <ConstraintList checks={plan.constraints} />
          </Collapsible>
          <Collapsible title="Reservations to make" icon="ticket" badge={<Badge tone="warning">{plan.reservations.length}</Badge>}>
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
              <Text size="xs" tone="tertiary">No reservations are required by the returned sources.</Text>
            )}
          </Collapsible>
          <Collapsible title="Disruptions" icon="alert" badge={<Badge tone={plan.disruptions.length ? "danger" : "neutral"}>{plan.disruptions.length}</Badge>}>
            {plan.disruptions.length ? (
              <div className="pf-stack">
                {plan.disruptions.map((disruption) => (
                  <div className="pf-gap" key={disruption.id}>
                    <Icon name="alert" size={13} />
                    <span>{disruption.summary} — {disruption.resolved ? "resolved" : "open"}</span>
                  </div>
                ))}
              </div>
            ) : (
              <Text size="xs" tone="tertiary">No disruption was reported for this trip.</Text>
            )}
          </Collapsible>
          <Collapsible title={`Data and sources (${unknownGaps})`} icon="info" badge={<Badge tone={unknownGaps ? "warning" : "success"} dot>{unknownGaps ? "gaps" : "complete"}</Badge>}>
            <GapList gaps={plan.data_quality.gaps} />
            <Divider />
            <div className="pf-row pf-row--wrap">
              {plan.data_quality.sources_used.map((source) => (
                <Badge key={source} tone="neutral">{source}</Badge>
              ))}
            </div>
            <Text size="xs" tone="tertiary">
              Search window: last {plan.data_quality.search_window_months} months. Generated {new Date(plan.data_quality.generated_at).toLocaleString("en-GB")}.
            </Text>
          </Collapsible>
          {trace ? (
            <Collapsible title="How the AI worked" icon="info">
              <TraceList trace={trace} />
            </Collapsible>
          ) : null}
        </div>
        <Divider />
      </div>
    </section>
  );
}

export function TraceList({ trace }: { trace: RunTrace }) {
  const max = Math.max(...trace.spans.map((span) => span.duration_ms ?? 0), 1);
  return (
    <div className="pf-trace">
      <div className="pf-row pf-row--between">
        <Text size="xs" weight="semibold">{trace.spans.length} steps · {trace.tool_calls} tool calls</Text>
        <Text size="xs" tone="tertiary" className="pf-mono">{trace.request_id}</Text>
      </div>
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

function modeMinutes(km: number, mode: TravelMode): number {
  const speed = mode === "walk" ? 4.5 : mode === "metro" ? 22 : mode === "drive" ? 32 : 620;
  const overhead = mode === "walk" ? 0 : mode === "metro" ? 8 : mode === "drive" ? 5 : 90;
  return Math.max(2, Math.round((km / speed) * 60 + overhead));
}

function localClock(zone: string): string {
  try {
    const time = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date());
    return `Local time ${time} · ${zone}`;
  } catch {
    return zone;
  }
}

export function MapPanel({
  plan,
  activeDay,
  onActiveDay,
  selectedItemId,
  onSelectItem,
  onOptimize,
  fx,
  updatedIds = [],
  hotelChanged = false,
}: {
  plan: TripPlan;
  activeDay: string;
  onActiveDay: (date: string) => void;
  selectedItemId: string | null;
  onSelectItem: (item: PlanItem | null) => void;
  onOptimize?: () => void;
  fx?: FxSnapshot | null;
  updatedIds?: string[];
  hotelChanged?: boolean;
}) {
  const [showRoute, setShowRoute] = useState(true);
  const [clock, setClock] = useState(9 * 60);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(true);
  const day = plan.days.find((entry) => entry.date === activeDay) ?? plan.days[0];
  const centre = plan.destination.geo;
  const routePoints = useMemo(
    () =>
      (day?.items ?? [])
        .filter((item) => item.geo)
        .filter((item) => !centre || kmBetween(centre, item.geo!) <= 400),
    [day, centre],
  );
  // Restaurants are part of the day: they get an "M" pin and a leg like any stop.
  const points = routePoints;
  const [legs, setLegs] = useState<MapLeg[]>([]);
  useEffect(() => {
    if (!day?.date) return;
    let cancelled = false;
    void getLegs(plan.session_id, day.date)
      .then((next) => {
        if (!cancelled) setLegs(next);
      })
      .catch(() => {
        if (!cancelled) setLegs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [plan.session_id, plan.trip_id, plan.version, day?.date]);
  const hotel = plan.hotels[0] && (!centre || !plan.hotels[0].geo || kmBetween(centre, plan.hotels[0].geo) <= 400)
    ? plan.hotels[0]
    : undefined;
  const hotelItem = useMemo(() => (hotel ? hotelScopeItem(hotel) : null), [hotel]);

  /** The replay chain: leave the hotel, visit the day, come back. */
  const timelinePoints = useMemo(() => {
    const items = [...routePoints].sort(
      (a, b) => (minutesOf(a.start) ?? 0) - (minutesOf(b.start) ?? 0),
    );
    if (!hotelItem || !items.length) return items;
    const firstStart = minutesOf(items[0].start) ?? 9 * 60;
    const lastEnd = items.reduce((latest, item) => {
      const end = minutesOf(item.end);
      return end === null ? latest : Math.max(latest, end);
    }, firstStart);
    const departStart = Math.max(CLOCK_MIN, firstStart - 30);
    const departEnd = Math.max(departStart + 10, firstStart - 15);
    const backStart = Math.min(CLOCK_MAX - 20, lastEnd + 10);
    const backEnd = Math.min(CLOCK_MAX, backStart + 15);
    const depart: PlanItem = { ...hotelItem, start: clockText(departStart), end: clockText(departEnd) };
    const back: PlanItem = { ...hotelItem, start: clockText(backStart), end: clockText(backEnd) };
    return [depart, ...items, back];
  }, [routePoints, hotelItem]);

  const nameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of routePoints) map.set(item.id, item.name);
    if (hotelItem) map.set(hotelItem.id, hotelItem.name);
    return map;
  }, [routePoints, hotelItem]);
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
    const items = day?.items ?? [];
    return (
      items.find((item) => {
        const start = minutesOf(item.start);
        const end = minutesOf(item.end);
        return start !== null && start <= clock && (end === null || end > clock);
      }) ?? null
    );
  }, [day, clock]);

  const currentItem = useMemo(() => {
    const items = day?.items ?? [];
    return (
      activeItem ??
      [...items].reverse().find((item) => {
        const start = minutesOf(item.start);
        return start !== null && start <= clock;
      }) ??
      null
    );
  }, [day, clock, activeItem]);

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
      const real = legs.find((entry) => entry.from_id === from.id && entry.to_id === to.id);
      if (real) {
        const mode: TravelMode =
          real.mode === "transit" ? "metro" : real.mode === "drive" ? "drive" : "walk";
        return {
          mode,
          label: `to ${to.name} · ${real.minutes ?? "?"} min ${MODE[mode].label.toLowerCase()}`,
          km: real.km ?? kmBetween(from.geo!, to.geo!),
        };
      }
      const km = kmBetween(from.geo!, to.geo!);
      const mode = modeForDistance(km);
      return { mode, label: `to ${to.name} · est ${modeMinutes(km, mode)} min`, km };
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
        label: route ? route.label : "At the last stop",
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
      label: route ? route.label : "At stop",
      fromId: anchor.id,
      toId: anchor.id,
      progress: 0,
    };
  }, [timelinePoints, legs, clock, activeItem]);

  const dayLegs = useMemo(() => {
    const modeOf = (value: string): TravelMode =>
      value === "transit" ? "metro" : value === "drive" ? "drive" : "walk";
    if (legs.length) {
      return legs.map((entry) => ({
        from: { id: entry.from_id, name: nameById.get(entry.from_id) ?? "the stay" },
        to: { id: entry.to_id, name: nameById.get(entry.to_id) ?? "the stay" },
        mode: modeOf(entry.mode),
        km: entry.km ?? 0,
        minutes: entry.minutes ?? 0,
        real: entry.source !== "estimate",
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
      };
    });
  }, [legs, nameById, routePoints]);

  const legendModes = useMemo(
    () => [...new Set(dayLegs.map((entry) => entry.mode))],
    [dayLegs],
  );

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
    const lastEnd = (day?.items ?? []).reduce((latest, item) => {
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
  }, [clock, playing, day, plan.days, onActiveDay]);

  return (
    <section className="pf-card pf-map" aria-label="Trip map">
      <TripMap
        plan={plan}
        day={day}
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
        follow={follow}
        updatedIds={updatedSet}
        hotelChanged={hotelChanged}
        legs={legs}
      />
      <div className="pf-map__foot">
        <div className="pf-row pf-row--between">
          <div className="pf-stack" style={{ gap: 0 }}>
            <Text size="sm" weight="semibold">{day?.title ?? "Select a day"}</Text>
            <Text size="xs" tone="tertiary">{points.filter((item) => item.type !== "meal").length} sights · {points.filter((item) => item.type === "meal").length} meals · {plan.destination.name}</Text>
          </div>
          <div className="pf-row" style={{ gap: 4 }}>
            {plan.days.map((entry) => (
              <button
                key={entry.date}
                type="button"
                className="pf-chip"
                data-active={entry.date === activeDay}
                onClick={() => {
                  onActiveDay(entry.date);
                  setPlaying(false);
                }}
              >
                {new Date(`${entry.date}T12:00:00`).getDate()}
              </button>
            ))}
          </div>
        </div>
        <div className="pf-route-legend" aria-label="Route legend">
          <span className="pf-route-legend__item">
            <i className="pf-route-legend__line pf-route-legend__line--done" /> travelled
          </span>
          <span className="pf-route-legend__item">
            <i className="pf-route-legend__line pf-route-legend__line--ahead" /> ahead
          </span>
          {legendModes.map((mode) => (
            <span className="pf-route-legend__item" key={mode}>
              <i className="pf-route-legend__dot" style={{ background: MODE[mode].color }} />
              {MODE[mode].label}
            </span>
          ))}
        </div>
        {dayLegs.length ? (
          <div className="pf-route-legs">
            {dayLegs.slice(0, 3).map((entry) => (
              <div className="pf-route-leg" key={`${entry.from.id}-${entry.to.id}`}>
                <span className="pf-route-leg__mode" style={{ color: MODE[entry.mode].color }}>
                  {MODE[entry.mode].label}
                </span>
                <span className="pf-route-leg__name">
                  {entry.from.name} → {entry.to.name}
                </span>
                <span className="pf-route-leg__time">
                  {entry.real ? `${entry.minutes} min` : `est ${entry.minutes} min`}
                </span>
              </div>
            ))}
            {dayLegs.length > 3 ? (
              <span className="pf-route-legs__more">+{dayLegs.length - 3} more legs</span>
            ) : null}
          </div>
        ) : null}
        <div className="pf-map__timebar">
          <IconButton
            icon={playing ? "pause" : "play"}
            label={playing ? "Pause the day" : "Play through the day"}
            onClick={() => {
              if (clock >= CLOCK_MAX) setClock(firstStartOfDay);
              setPlaying((value) => !value);
            }}
          />
          <div className="pf-map__speeds" role="group" aria-label="Playback speed">
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
            aria-label={follow ? "Unpin the map from the traveller" : "Pin the map on the traveller"}
            onClick={() => setFollow((value) => !value)}
          >
            {follow ? "Pinned" : "Pin"}
          </button>
          <div className="pf-map__time-main">
            <div className="pf-map__time-row">
              <strong className="pf-map__time">{clockText(clock)}</strong>
              <span className="pf-map__now">
                {currentItem
                  ? `${currentItem.start}–${currentItem.end} · ${currentItem.name}`
                  : "Before the first stop"}
              </span>
            </div>
            {tracker ? (
              <div className="pf-map__legrow">
                <span className="pf-map__mode" data-mode={tracker.mode}>
                  <Icon name={MODE[tracker.mode].icon} size={11} />
                  {MODE[tracker.mode].label}
                </span>
                <span className="pf-map__leglabel">{tracker.label}</span>
              </div>
            ) : null}
            <div className="pf-map__timeline">
              <input
                className="pf-slider"
                type="range"
                aria-label="Time of day"
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
                      aria-label={`Jump to ${item.start}, ${item.name}`}
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
        <BudgetPanel cost={plan.cost} fx={fx} onOptimize={onOptimize} />
      </div>
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
  const [asking, setAsking] = useState<string | null>(null);

  const promptFor = (item: TrendingPlace) => {
    const places = item.places.slice(0, 3);
    const base = `Plan a ${item.days}-day trip to ${item.destination}`;
    return places.length ? `${base}. Include ${places.join(", ")}.` : `${base}.`;
  };

  return (
    <aside className="pf-card pf-trending" aria-label="Trending trips">
      <div className="pf-row pf-row--between" style={{ alignItems: "flex-start" }}>
        <div className="pf-stack" style={{ gap: 2 }}>
          <Text size="xs" tone="tertiary" weight="semibold" style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Trending
          </Text>
          <Heading level={4}>What others planned</Heading>
        </div>
        {onRefresh ? (
          <IconButton icon="refresh" label="Refresh trending" onClick={onRefresh} />
        ) : null}
      </div>
      <Text size="xs" tone="tertiary">
        Trips marked useful by travellers. Only destination, length, year and sight names
        are shown; no budgets, party sizes or session ids.
      </Text>
      {onQueryChange ? (
        <SearchField
          value={query}
          onChange={onQueryChange}
          placeholder="Search destinations or sights"
          label="Search trending"
        />
      ) : null}

      {loading ? <Text size="sm" tone="secondary">Loading public plans…</Text> : null}
      {error ? (
        <div className="pf-trending__empty">
          <Text size="sm" tone="secondary">{error}</Text>
          {onRefresh ? <Button size="sm" variant="ghost" onClick={onRefresh}>Try again</Button> : null}
        </div>
      ) : null}
      {!loading && !error && !items.length ? (
        <div className="pf-trending__empty">
          <Icon name="compass" size={18} />
          <Text size="sm" tone="secondary">
            No public plans yet. Generate an itinerary and mark it useful to seed the rail.
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
                <Badge tone="brand">{item.plans} {item.plans === 1 ? "plan" : "plans"}</Badge>
              </div>
              <span className="pf-trending__meta">
                {item.days} days{item.year ? ` · ${item.year}` : ""}
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
                  <span>Matches your</span>
                  {item.match.map((tag) => (
                    <Badge key={tag} tone="info">{tag}</Badge>
                  ))}
                </div>
              ) : null}
              {open ? (
                <div className="pf-trending__confirm">
                  <Text size="xs" tone="secondary">
                    This sends a new trip request to the assistant. Your current plan is not
                    changed until you send it.
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
                      Start planning
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <Button size="sm" variant="ghost" iconRight="arrowRight" onClick={() => setAsking(item.destination)}>
                  Try this trip
                </Button>
              )}
            </article>
          );
        })}
      </div>

      {trace?.note ? (
        <details className="pf-trending__trace">
          <summary>How this is ranked</summary>
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
  return (
    <main className="pf-saved">
      <PageHeader
        eyebrow="Memory"
        title="My trips"
        description="Trips you marked useful. A similar new trip can use their shape, never their facts."
      />
      {trips.length === 0 ? (
        <div className="pf-card">
          <EmptyState
            icon="book"
            title="No saved trips yet"
            body="Mark an itinerary as useful from the Plan tab and it will appear here with its destination and rating."
          />
        </div>
      ) : (
        <div className="pf-saved__grid">
          {trips.map((trip) => (
            <button type="button" className="pf-card pf-saved-card" key={trip.id} onClick={() => onOpen?.(trip.id)}>
              <div className="pf-row pf-row--between">
                <Heading level={4}>{trip.destination}</Heading>
                <Badge tone="success" dot>Useful</Badge>
              </div>
              <Text size="sm" tone="secondary">{trip.summary}</Text>
              <div className="pf-row pf-row--between">
                <Text size="xs" tone="tertiary">{trip.days ? `${trip.days} days` : "—"} · {new Date(trip.created_at).toLocaleDateString("en-GB")}</Text>
                <Badge tone="warning">{trip.stars ? `${trip.stars}★` : `${Math.round(trip.rating * 5)}★`}</Badge>
              </div>
            </button>
          ))}
        </div>
      )}
    </main>
  );
}

/* ----------------------------------------------------------- evaluation -- */

export function EvalDashboard({
  datasets,
  runs,
  latest,
  onRun,
  running,
  error,
  onExport,
  onSelectRun,
}: {
  datasets: EvalDataset[];
  runs: EvalRunSummary[];
  latest: EvalRun | null;
  onRun: (dataset: string, tag: string) => void;
  running: boolean;
  error: string | null;
  onExport?: (runId: string) => void;
  onSelectRun?: (runId: string) => void;
}) {
  const [dataset, setDataset] = useState(latest?.dataset_id ?? "hongkong");
  const [tag, setTag] = useState("baseline-v1");
  const [caseId, setCaseId] = useState<string | null>(null);
  const selectedCase = latest?.cases.find((item) => item.id === caseId) ?? null;
  const metrics = latest?.metrics ?? [];
  const primaryMetric = metrics.find((metric) => metric.unit === "%")?.key ?? "constraint_satisfaction";
  const failed = latest?.cases.filter((item) => !item.passed) ?? [];
  const series = latest ? runs.filter((run) => run.dataset_id === latest.dataset_id) : runs;

  return (
    <main className="pf-eval">
      <PageHeader
        eyebrow="Evaluation"
        title="Measured, not asserted"
        description="Every run is frozen against a dataset, scored with deterministic scripts, and compared with the previous run."
        action={
          <div className="pf-row">
            {latest ? <Button variant="secondary" size="sm" iconLeft="download" onClick={() => onExport?.(latest.id)}>Export report</Button> : null}
            <Badge tone={latest ? "success" : "warning"} dot>{latest ? "Run complete" : "No runs yet"}</Badge>
          </div>
        }
      />

      {error ? <div className="pf-banner"><Icon name="alert" size={14} />{error}</div> : null}

      <div className="pf-card pf-eval__panel">
        <div className="pf-eval__panel-head">
          <div className="pf-stack" style={{ gap: 2 }}>
            <Text size="sm" weight="semibold">Run an experiment</Text>
            <Text size="xs" tone="tertiary">Frozen datasets only. The sandbox runs the real pipeline without live keys.</Text>
          </div>
          <Badge tone="neutral">{runs.length} runs</Badge>
        </div>
        <div className="pf-run-form">
          <label className="pf-field">
            <span className="pf-field__label">Dataset</span>
            <Select value={dataset} onChange={(event) => setDataset(event.target.value)}>
              {datasets.map((item) => (
                <option key={item.id} value={item.id}>{item.name} · {item.item_count} cases</option>
              ))}
            </Select>
          </label>
          <label className="pf-field">
            <span className="pf-field__label">Experiment tag</span>
            <Input value={tag} onChange={(event) => setTag(event.target.value)} placeholder="router-tune-v2" />
          </label>
          <Button variant="primary" iconLeft="play" loading={running} onClick={() => onRun(dataset, tag)}>
            Run evaluation
          </Button>
        </div>
      </div>

      {latest ? (
        <>
          <div className="pf-eval__metrics">
            {metrics.map((metric) => (
              <MetricCard key={metric.key} metric={metric} />
            ))}
          </div>

          <div className="pf-eval__grid">
            <div className="pf-card pf-eval__panel">
              <div className="pf-eval__panel-head">
                <Text size="sm" weight="semibold">Experiment comparison</Text>
                <Badge tone="neutral">{latest.dataset_id} v{latest.dataset_version}</Badge>
              </div>
              <ExperimentTable runs={runs} activeId={latest.id} onSelect={onSelectRun} />
            </div>
            <div className="pf-card pf-eval__panel">
              <div className="pf-eval__panel-head">
                <Text size="sm" weight="semibold">Trend</Text>
                <Badge tone="neutral">{primaryMetric.replaceAll("_", " ")}</Badge>
              </div>
              <TrendChart runs={series} metricKey={primaryMetric} />
            </div>
          </div>

          <div className="pf-eval__grid">
            <div className="pf-card pf-eval__panel">
              <div className="pf-eval__panel-head">
                <Text size="sm" weight="semibold">Failures and drill-down</Text>
                <Badge tone={failed.length ? "danger" : "success"}>{failed.length} failed</Badge>
              </div>
              {failed.length === 0 ? (
                <EmptyState icon="check" title="No failing cases" body="This run passed every case in the frozen split." />
              ) : (
                <div className="pf-stack">
                  {failed.map((item) => (
                    <button key={item.id} type="button" className="pf-saved-card pf-card" onClick={() => setCaseId(item.id)} style={{ boxShadow: "none" }}>
                      <div className="pf-row pf-row--between">
                        <Text size="sm" weight="semibold">{item.case_id}</Text>
                        <span className="pf-row" style={{ gap: 6 }}>
                          {typeof item.judge_score === "number" ? (
                            <Badge tone="neutral">judge {item.judge_score.toFixed(1)}</Badge>
                          ) : null}
                          <Badge tone="danger">{item.failure_class || "failed"}</Badge>
                        </span>
                      </div>
                      <Text size="xs" tone="secondary">{item.input}</Text>
                      <Text size="xs" tone="tertiary">{item.judge_reason}</Text>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="pf-card pf-eval__panel">
              <div className="pf-eval__panel-head">
                <Text size="sm" weight="semibold">Dataset split</Text>
                <Badge tone="neutral">frozen</Badge>
              </div>
              {datasets.map((item) => (
                <div className="pf-row pf-row--between" key={item.id}>
                  <span className="pf-row" style={{ gap: 7 }}>
                    <Icon name={item.frozen ? "lock" : "alert"} size={13} />
                    <span className="pf-small">{item.name}</span>
                  </span>
                  <span className="pf-row" style={{ gap: 8 }}>
                    <span className="pf-small pf-muted">{item.item_count} cases</span>
                    <Badge tone={item.frozen ? "success" : "warning"}>{item.frozen ? "frozen" : "draft"}</Badge>
                  </span>
                </div>
              ))}
              <Divider />
              <Text size="xs" tone="tertiary">
                Dev and held-out items are frozen before the first scored run. Failed cases carry the input, judge reason and tool summary.
              </Text>
            </div>
          </div>
        </>
      ) : (
        <div className="pf-card">
          <EmptyState icon="route" title="No evaluation runs recorded" body="Run the first evaluation to see metric cards, a comparison table and a failure drill-down." />
        </div>
      )}

      <Drawer open={Boolean(selectedCase)} onClose={() => setCaseId(null)} title={selectedCase ? `Case ${selectedCase.case_id}` : "Case"}>
        {selectedCase ? (
          <div className="pf-stack pf-stack--md">
            <Badge tone={selectedCase.passed ? "success" : "danger"} dot>{selectedCase.passed ? "passed" : selectedCase.failure_class || "failed"}</Badge>
            <div className="pf-stack">
              <Text size="xs" weight="semibold" tone="tertiary">Input</Text>
              <Text size="sm">{selectedCase.input}</Text>
            </div>
            <div className="pf-stack">
              <Text size="xs" weight="semibold" tone="tertiary">Output</Text>
              <Text size="sm">{selectedCase.output_summary}</Text>
            </div>
            <div className="pf-stack">
              <Text size="xs" weight="semibold" tone="tertiary">Judge reason</Text>
              <Text size="sm">{selectedCase.judge_reason}</Text>
            </div>
            {typeof selectedCase.judge_score === "number" ? (
              <div className="pf-stack">
                <Text size="xs" weight="semibold" tone="tertiary">Judge score (advisory)</Text>
                <div className="pf-row" style={{ gap: 6, flexWrap: "wrap" }}>
                  <Badge tone={selectedCase.judge_score >= 4 ? "success" : selectedCase.judge_score >= 3 ? "warning" : "danger"}>
                    {selectedCase.judge_score.toFixed(1)} / 5
                  </Badge>
                  {(selectedCase.judge_flags ?? []).map((flag) => (
                    <Badge key={flag} tone="neutral">{flag}</Badge>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="pf-stack">
              <Text size="xs" weight="semibold" tone="tertiary">Tool summary</Text>
              {selectedCase.tool_summary.map((tool) => <Badge key={tool} tone="neutral">{tool}</Badge>)}
            </div>
            <Text size="xs" tone="tertiary" className="pf-mono">trace {selectedCase.trace_id || "not linked"}</Text>
          </div>
        ) : null}
      </Drawer>
    </main>
  );
}

/* ------------------------------------------------------------- settings -- */

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
  const [active, setActive] = useState<VideoNote | null>(null);
  if (!notes || notes.length === 0) return null;
  const embed = active ? videoEmbedUrl(active) : "";
  const activeHref = safeHref(active?.url);
  return (
    <section className="pf-card pf-videos" aria-label="Video guides">
      <div className="pf-videos__head">
        <Heading level={4}>Watch before you go</Heading>
        <Text size="xs" tone="tertiary">
          Public videos from YouTube and Bilibili; they cannot change prices or hours.
        </Text>
      </div>
      <div className="pf-videos__grid">
        {notes.map((note) => (
          <button
            type="button"
            className="pf-video"
            key={note.url}
            onClick={() => setActive(note)}
            aria-label={`Play ${note.title}`}
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
      <Dialog open={Boolean(active)} onClose={() => setActive(null)} title={active?.title ?? "Video"}>
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
              <Text size="sm">This video does not allow an in-page player.</Text>
            )}
            {activeHref ? (
              <a className="pf-video__open" href={activeHref} target="_blank" rel="noreferrer">
                Open on {active.source}
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
  return (
    <section className="pf-card pf-guides" aria-label="Local guide notes">
      <div className="pf-row pf-row--between">
        <span className="pf-guides__eyebrow">LOCAL GUIDE · CITED</span>
        <Badge tone="neutral" dot>{items[0]?.retrieval?.mode ?? "soft context"}</Badge>
      </div>
      {loading ? (
        <Text size="xs" tone="tertiary">Retrieving guide notes…</Text>
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
              {item.stale ? "Guide may be outdated" : `Verified ${item.last_verified ?? "recently"}`}
              {item.license ? ` · ${item.license}` : ""}
            </span>
          </article>
        ))
      ) : (
        <Text size="xs" tone="tertiary">No guide note for this destination yet.</Text>
      )}
      <Text size="xs" tone="tertiary" className="pf-guides__note">
        Soft context only: never a price, opening hour or booking claim.
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
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await onSubmit(mode, email.trim(), password, displayName.trim());
      setPassword("");
    } catch (cause) {
      let message = cause instanceof Error ? cause.message : "Sign-in failed.";
      try {
        const parsed = JSON.parse(message) as { detail?: string };
        message = parsed.detail || message;
      } catch {
        // Plain-text errors stay as they are.
      }
      if (/email or password is incorrect/i.test(message)) {
        message = "That email and password don’t match. Please try again.";
      } else if (/already exists/i.test(message)) {
        message = "That email is already registered. Try logging in instead.";
      } else if (/valid email|password/i.test(message) && /422|field|least/i.test(message)) {
        message = "Check your email and use a password with at least 8 characters.";
      }
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={mode === "login" ? "Log in" : "Create account"}>
      <div className="pf-auth">
        <Text size="sm" tone="secondary">
          An account keeps your preferences and trips across browsers. You can keep planning
          as a guest without one.
        </Text>
        {mode === "register" ? (
          <Field label="Name" htmlFor="pf-auth-name">
            <Input id="pf-auth-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Alex" />
          </Field>
        ) : null}
        <Field label="Email" htmlFor="pf-auth-email">
          <Input
            id="pf-auth-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
        </Field>
        <Field label="Password" hint="At least 8 characters" htmlFor="pf-auth-password">
          <Input
            id="pf-auth-password"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
          />
        </Field>
        {error ? <div className="pf-auth__error">{error}</div> : null}
        <Button variant="primary" block loading={busy} onClick={() => void submit()} disabled={!email || password.length < 8}>
          {mode === "login" ? "Log in" : "Create account"}
        </Button>
        <button
          type="button"
          className="pf-auth__switch"
          onClick={() => {
            setMode((value) => (value === "login" ? "register" : "login"));
            setError(null);
          }}
        >
          {mode === "login" ? "New here? Create an account" : "Already have an account? Log in"}
        </button>
      </div>
    </Dialog>
  );
}

export function SettingsView({
  config,
  injections,
  profile,
  signedIn = false,
  onClearMemory,
  onSavePreferences,
}: {
  config: {
    thresholds: Record<string, number | string>;
    freshness_hours: Record<string, number>;
    writer: { available: boolean; model: string };
    providers: Record<string, string[] | string>;
    locales: string[];
  } | null;
  injections?: { category: string; severity: string; count: number }[];
  profile?: ClientProfile | null;
  signedIn?: boolean;
  onClearMemory?: () => void;
  onSavePreferences?: (preferences: string[]) => void;
}) {
  const [helpOpen, setHelpOpen] = useState(false);
  const [preferences, setPreferences] = useState<string[]>(profile?.preferences ?? []);
  const [prefInput, setPrefInput] = useState("");
  useEffect(() => {
    setPreferences(profile?.preferences ?? []);
  }, [profile]);

  const addPreference = () => {
    const next = prefInput.trim().replace(/,+$/, "");
    if (!next) return;
    setPreferences((current) => (current.includes(next) ? current : [...current, next]));
    setPrefInput("");
  };
  const removePreference = (value: string) => {
    setPreferences((current) => current.filter((entry) => entry !== value));
  };
  return (
    <main className="pf-settings">
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        description="Production thresholds are read-only in the app. Change them in the environment, restart, and record the diff."
        action={<Button variant="secondary" size="sm" iconLeft="book" onClick={() => setHelpOpen(true)}>How it works</Button>}
      />
      {config ? (
        <>
          <div className="pf-card pf-eval__panel">
            <div className="pf-row pf-row--between">
              <Text size="sm" weight="semibold">Traveller profile</Text>
              <Badge tone={signedIn ? "success" : "neutral"} dot>{signedIn ? "Account" : "Anonymous"}</Badge>
            </div>
            <Text size="xs" tone="tertiary">
              {signedIn
                ? "Signed in. Preferences and useful trips are stored against your account and can be cleared here."
                : "Stored in this browser under a random token — no account, email or password. Preferences are remembered from your trip form and used in later plans."}
            </Text>
            <div className="pf-form-grid">
              <Field label="Remembered preferences" wide htmlFor="pf-profile-preferences">
                <div className="pf-preference-tags">
                  {preferences.map((preference) => (
                    <span className="pf-preference-tag" key={preference}>
                      {preference}
                      <button
                        type="button"
                        aria-label={`Remove ${preference}`}
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
                    aria-label="Add a preference"
                    placeholder={preferences.length ? "Add another…" : "slow pace, vegetarian, museums"}
                    onChange={(event) => setPrefInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === ",") {
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
                {profile ? `${profile.sessions} chats · ${profile.saved_trips} useful trips · ${profile.destinations.length} destinations` : "Loading profile…"}
              </Text>
              <div className="pf-row" style={{ gap: 6 }}>
                {onSavePreferences ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      addPreference();
                      const pending = prefInput.trim();
                      const next =
                        pending && !preferences.includes(pending) ? [...preferences, pending] : preferences;
                      onSavePreferences(next);
                    }}
                  >
                    Save preferences
                  </Button>
                ) : null}
                {onClearMemory ? (
                  <Button size="sm" variant="ghost" onClick={onClearMemory}>
                    Clear memory
                  </Button>
                ) : null}
              </div>
            </div>
            {profile?.destinations.length ? (
              <Text size="xs" tone="tertiary">
                Recent destinations: {profile.destinations.join(" · ")}
              </Text>
            ) : null}
          </div>
          <div className="pf-card pf-eval__panel">
            <Text size="sm" weight="semibold">Policy thresholds</Text>
            {Object.entries(config.thresholds).map(([key, value]) => (
              <div className="pf-row pf-row--between" key={key}>
                <span className="pf-small pf-muted">{key.replaceAll("_", " ")}</span>
                <span className="pf-mono pf-small">{String(value)}</span>
              </div>
            ))}
          </div>
          <div className="pf-card pf-eval__panel">
            <Text size="sm" weight="semibold">Freshness windows</Text>
            {Object.entries(config.freshness_hours).map(([key, value]) => (
              <div className="pf-row pf-row--between" key={key}>
                <span className="pf-small pf-muted">{key}</span>
                <span className="pf-mono pf-small">{value} h</span>
              </div>
            ))}
          </div>
          <div className="pf-card pf-eval__panel">
            <div className="pf-row pf-row--between">
              <Text size="sm" weight="semibold">Writer</Text>
              <Badge tone={config.writer.available ? "success" : "warning"} dot>{config.writer.available ? "available" : "offline"}</Badge>
            </div>
            <Text size="sm" tone="secondary" className="pf-mono">{config.writer.model}</Text>
            <Divider />
            <Text size="sm" weight="semibold">Provider chains</Text>
            {Object.entries(config.providers).map(([capability, chains]) => (
              <div className="pf-row pf-row--between" key={capability}>
                <span className="pf-small pf-muted">{capability}</span>
                <span className="pf-small" style={{ textAlign: "right" }}>
                  {Array.isArray(chains) ? chains.join(" → ") || "none" : String(chains)}
                </span>
              </div>
            ))}
          </div>
          <div className="pf-card pf-eval__panel">
            <Text size="sm" weight="semibold">Aggregate injection log</Text>
            <Text size="xs" tone="tertiary">Counts and categories only. No conversation text is stored in this log.</Text>
            {(injections ?? []).length ? (
              injections?.map((event) => (
                <div className="pf-row pf-row--between" key={event.category}>
                  <span className="pf-small">{event.category}</span>
                  <Badge tone={event.severity === "high" ? "danger" : "warning"}>{event.count}</Badge>
                </div>
              ))
            ) : (
              <Text size="xs" tone="tertiary">No injection attempts recorded.</Text>
            )}
          </div>
        </>
      ) : (
        <div className="pf-card"><EmptyState title="Configuration unavailable" body="The backend did not return /api/runtime." /></div>
      )}
      <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} title="How Pathfinder works">
        <div className="pf-stack pf-stack--md">
          <Text size="sm">One message takes one path. A typed System 1 decision chooses plan, modify or ask; anything unclear is clarified instead of guessed.</Text>
          <Text size="sm">A new plan runs four agents in parallel — attractions, hotels, weather and tickets — under a time budget. The planner may select and order verified candidates, never invent one.</Text>
          <Text size="sm">An edit reruns only the affected agents, preserves locked and confirmed stops, and increments the version. A quick question answers from the current plan first, then at most one allowlisted tool.</Text>
          <Text size="sm">Every item carries a source and a freshness status. Missing data stays visibly unavailable.</Text>
        </div>
      </Dialog>
    </main>
  );
}

/* -------------------------------------------------------------- utility -- */

export function LoadingScreen({ label = "Loading Pathfinder" }: { label?: string }) {
  return (
    <div className="pf-empty" style={{ minHeight: "60vh" }}>
      <span className="pf-spinner" data-size="lg" />
      <Text size="sm" tone="tertiary">{label}</Text>
    </div>
  );
}

export function Banner({ tone = "danger", children }: { tone?: "danger" | "info"; children: ReactNode }) {
  return (
    <div className="pf-banner" data-tone={tone}>
      <Icon name={tone === "danger" ? "alert" : "info"} size={14} />
      <span>{children}</span>
    </div>
  );
}

export { IntentBadge, StatusPill, AgentChip, EmptyState, PageHeader, Divider };
export type { AgentStatus };
