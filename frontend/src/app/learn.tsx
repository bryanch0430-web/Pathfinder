import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Button, EmptyState, Text } from "../design-system";
import { type StopCommit } from "../design-system/directEdit";
import { PlanStudio } from "../design-system/planStudio";
import type { TripPlan } from "../design-system/types";
import { Icon } from "../design-system/icons";
import { useI18n } from "../lib/i18n";
import { maskTyped } from "../lib/maskPii";
import {
  getDesk,
  getDeskChat,
  applyDeskLayout,
  getDeskItinerary,
  getDeskRevisions,
  undoDeskPlan,
  getDeskPlan,
  holdDeskChat,
  releaseDeskChat,
  saveDeskBrief,
  saveDeskNote,
  editDeskPlan,
  saveDeskPlan,
  sayOnDesk,
  type DeskChat,
  type DeskMessage,
  type DeskPlan,
  type DeskStop,
} from "../lib/api";

function tripWhen(chat: DeskChat): string {
  if (!chat.start) return "";
  return chat.end && chat.end !== chat.start ? `${chat.start} – ${chat.end}` : chat.start;
}

function monthCells(year: number, month: number): { date: Date; inMonth: boolean }[] {
  const first = new Date(year, month, 1);
  const pad = (first.getDay() + 6) % 7;
  const start = new Date(year, month, 1 - pad);
  const count = new Date(year, month + 1, 0).getDate();
  const total = Math.ceil((pad + count) / 7) * 7;
  return Array.from({ length: total }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return { date, inMonth: date.getMonth() === month };
  });
}

function isoOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

const DAY_START = 8 * 60;
const DAY_END = 22 * 60;
const HOUR_PX = 72;

function clockMinutes(value: string): number | null {
  const match = value.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function clockLabel(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function DayTimeline({
  day,
  handId,
  onPick,
}: {
  day: { date: string; title: string; items: { id: string; name: string; type: string; start: string; end: string; address?: string }[] };
  handId: string | null;
  onPick: (id: string) => void;
}) {
  const hours = [];
  for (let minute = DAY_START; minute < DAY_END; minute += 60) hours.push(minute);
  return (
    <article>
      <header className="pf-ops__studio-head">
        <h2>{day.title || day.date}</h2>
        <p>{day.date}</p>
      </header>
      <div className="pf-ops__daycal" style={{ height: ((DAY_END - DAY_START) / 60) * HOUR_PX }}>
        {hours.map((minute) => (
          <div className="pf-ops__daycal-hour" key={minute} style={{ top: ((minute - DAY_START) / 60) * HOUR_PX }}>
            <span>{clockLabel(minute)}</span>
          </div>
        ))}
        {day.items.map((item) => {
          const start = clockMinutes(item.start);
          const end = clockMinutes(item.end);
          if (start === null || end === null || end <= start) return null;
          const top = ((Math.max(start, DAY_START) - DAY_START) / 60) * HOUR_PX;
          const height = Math.max(44, ((Math.min(end, DAY_END) - Math.max(start, DAY_START)) / 60) * HOUR_PX - 4);
          return (
            <button
              type="button"
              key={item.id}
              className="pf-ops__daycal-block"
              data-active={handId === item.id || undefined}
              style={{ top, height }}
              onClick={() => onPick(item.id)}
            >
              <strong>{item.name}</strong>
              <span>{item.start}–{item.end}</span>
              {item.address ? <small>{item.address}</small> : null}
            </button>
          );
        })}
      </div>
    </article>
  );
}

function StudioCalendar({
  days,
  locale,
  onOpen,
}: {
  days: { date: string; title: string; items: { name: string }[] }[];
  locale: string;
  onOpen: (date: string) => void;
}) {
  const tag = locale === "zh-Hant" || locale === "zh-Hans" ? locale : "en";
  const byDate = new Map(days.map((day) => [day.date, day]));
  const months = [...new Set(days.map((day) => day.date.slice(0, 7)))];
  const weekdays = Array.from({ length: 7 }, (_, index) =>
    new Intl.DateTimeFormat(tag, { weekday: "short" }).format(new Date(2024, 0, 1 + index)),
  );
  return (
    <div className="pf-ops__cal">
      {months.map((key) => {
        const [year, month] = key.split("-").map(Number);
        const cells = monthCells(year, month - 1);
        const title = new Intl.DateTimeFormat(tag, { month: "long", year: "numeric" }).format(new Date(year, month - 1, 1));
        return (
          <section key={key} aria-label={title}>
            <h2>{title}</h2>
            <div className="pf-ops__cal-grid" role="grid">
              {weekdays.map((name, index) => <span className="pf-ops__cal-dow" key={`${key}-${index}`}>{name}</span>)}
              {cells.map((cell) => {
                const iso = isoOf(cell.date);
                const trip = cell.inMonth ? byDate.get(iso) : undefined;
                if (!cell.inMonth) return <span key={`${key}-${iso}`} className="pf-ops__cal-pad" />;
                if (!trip) {
                  return (
                    <span key={iso} className="pf-ops__cal-day">
                      <b>{cell.date.getDate()}</b>
                    </span>
                  );
                }
                return (
                  <button type="button" key={iso} className="pf-ops__cal-day" data-trip="true" onClick={() => onOpen(iso)}>
                    <b>{cell.date.getDate()}</b>
                    <strong>{trip.title || trip.items[0]?.name}</strong>
                    {trip.items.slice(0, 2).map((item, index) => <small key={`${item.name}-${index}`}>{item.name}</small>)}
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function publishPlan(sessionId: string, version: number) {
  try {
    localStorage.setItem("pathfinder.plan-sync", `${sessionId}:${version}:${Date.now()}`);
  } catch {
    // The open trip still reloads on its own timer.
  }
  // A storage event does not fire in the window that wrote it. The traveller's plan is in this window.
  window.dispatchEvent(new CustomEvent("pf-plan-sync", { detail: { sessionId, version } }));
}

function publishHold(sessionId: string, held: boolean, agentName = "") {
  window.dispatchEvent(new CustomEvent("pf-desk-hold", { detail: { sessionId, held, agentName } }));
}

function findStop(plan: TripPlan, id: string) {
  for (let day = 0; day < plan.days.length; day += 1) {
    const item = plan.days[day].items.find((entry) => entry.id === id);
    if (item) return { item, day };
  }
  return null;
}

function clockText(since: string | undefined, now: number): string {
  const start = since ? Date.parse(since) : Number.NaN;
  if (Number.isNaN(start)) return "";
  const seconds = Math.max(0, Math.floor((now - start) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d`;
}

function ChatClock({ since }: { since?: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <p className="pf-desk__clock" data-role="clock">
      <span>{clockText(since, now)}</span>
    </p>
  );
}

/** The session window starts nearly as large as the viewport, then stays draggable/resizable. */
function deskStartPlace() {
  const vw = typeof window === "undefined" ? 1440 : window.innerWidth;
  const vh = typeof window === "undefined" ? 900 : window.innerHeight;
  const w = Math.max(720, Math.min(1400, vw - 72));
  const h = Math.max(520, Math.min(900, vh - 132));
  return { x: Math.max(12, Math.round((vw - w) / 2)), y: Math.max(56, Math.round((vh - h) / 2)), w, h };
}

function clampDeskPlace(place: { x: number; y: number; w: number; h: number }) {
  const vw = typeof window === "undefined" ? 1440 : window.innerWidth;
  const vh = typeof window === "undefined" ? 900 : window.innerHeight;
  const w = Math.max(640, Math.min(place.w, Math.max(640, vw - 24)));
  const h = Math.max(460, Math.min(place.h, Math.max(460, vh - 80)));
  return { x: Math.max(8, Math.min(place.x, vw - w - 8)), y: Math.max(48, Math.min(place.y, vh - h - 8)), w, h };
}

/** A contact desk, separate from the planner: a queue, the conversation, and the trip beside it. */
export function DeskPage({ onBack, onPopOut }: { onBack: () => void; onPopOut?: () => void }) {
  const { t } = useI18n();
  const [chats, setChats] = useState<DeskChat[]>([]);
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState<"all" | "stuck">("all");
  const [pane, setPane] = useState<"queue" | "history">("queue");
  const [navOpen, setNavOpen] = useState(() => {
    try {
      return localStorage.getItem("pathfinder.desk-nav") !== "closed";
    } catch {
      return true;
    }
  });
  const [place, setPlace] = useState(deskStartPlace);
  const mainRef = useRef<HTMLElement>(null);
  const dragRef = useRef<{ kind: "move" | "size"; x: number; y: number; place: { x: number; y: number; w: number; h: number } } | null>(null);
  const [ready, setReady] = useState(() => {
    try {
      return localStorage.getItem("pathfinder.desk-ready") === "ready";
    } catch {
      return false;
    }
  });
  const [active, setActive] = useState<string | null>(null);
  const [held, setHeld] = useState(false);
  const [messages, setMessages] = useState<DeskMessage[]>([]);
  const [caseNote, setCaseNote] = useState("");
  const [draft, setDraft] = useState("");
  const [brief, setBrief] = useState({
    destination: "",
    origin: "",
    start: "",
    end: "",
    party: "",
    budget: "",
    currency: "HKD",
    preferences: "",
  });
  const [running, setRunning] = useState(false);
  const [plannerBusy, setPlannerBusy] = useState(false);
  const [plannerNote, setPlannerNote] = useState("");
  const [turnNote, setTurnNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);
  const [handId, setHandId] = useState<string | null>(null);
  const [plan, setPlan] = useState<DeskPlan | null>(null);
  const [tripPlan, setTripPlan] = useState<TripPlan | null>(null);
  const [undoVersions, setUndoVersions] = useState<number[]>([]);
  const undoSeed = useRef("");
  const [stops, setStops] = useState<DeskStop[]>([]);
  const [error, setError] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fit = () => setPlace((current) => clampDeskPlace(current));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  const loadList = () => {
    void getDesk().then((data) => setChats(data.chats)).catch((cause) => setError(String(cause)));
  };
  const loadChat = (id: string, withNote = false) => {
    void getDeskChat(id).then((data) => {
      setMessages(data.messages);
      setHeld(data.held);
      if (withNote) {
        setCaseNote(data.note ?? "");
        const contact = data.contact;
        setBrief({
          destination: contact.destination || "",
          origin: contact.origin || "",
          start: contact.start || "",
          end: contact.end || "",
          party: contact.party ? String(contact.party) : "",
          budget: contact.budget != null ? String(contact.budget) : "",
          currency: contact.currency || "HKD",
          preferences: (contact.preferences ?? []).join(", "),
        });
      }
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
  const loadTrip = (id: string, announce = false) => {
    void getDeskItinerary(id).then((data) => {
      setPlannerBusy(Boolean(data.planner_busy));
      setPlannerNote(data.planner_note || "");
      setTripPlan((current) => {
        if (!data.plan) return null;
        if (current && current.version === data.plan.version) return current;
        return data.plan;
      });
      if (announce && !data.plan) {
        setError(t("desk.studioEmpty"));
        setStudioOpen(false);
      }
    }).catch((cause) => {
      if (!announce) return;
      setError(cause instanceof Error ? cause.message : String(cause));
      setStudioOpen(false);
    });
  };
  useEffect(() => {
    if (!active || !held) return;
    loadTrip(active);
  }, [active, held]);
  useEffect(() => {
    if (!studioOpen || !active) return;
    window.dispatchEvent(new CustomEvent("pf-desk-studio", { detail: { open: true } }));
    loadTrip(active, true);
    const timer = window.setInterval(() => loadTrip(active), 4000);
    return () => {
      window.clearInterval(timer);
      window.dispatchEvent(new CustomEvent("pf-desk-studio", { detail: { open: false } }));
    };
  }, [studioOpen, active]);
  useEffect(() => {
    if (!studioOpen || !active || !tripPlan || undoSeed.current === active) return;
    undoSeed.current = active;
    const current = tripPlan.version;
    void getDeskRevisions(active).then((data) => {
      const older = data.versions
        .filter((row) => row.version < current && !String(row.change_summary || "").startsWith("Undid"))
        .map((row) => row.version);
      older.sort((left, right) => right - left);
      setUndoVersions(older);
    }).catch(() => undefined);
  }, [studioOpen, active, tripPlan]);
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
  const tagText = (stuck: boolean) => (stuck ? t("desk.stuck").toUpperCase() : "");
  const matches = (chat: DeskChat) => {
    const tagged = tag === "all" || (tag === "stuck" && Boolean(chat.stuck));
    return tagged && (
      !needle
      || chat.session_id.toLowerCase().includes(needle)
      || (chat.destination || "").toLowerCase().includes(needle)
      || (chat.preview || "").toLowerCase().includes(needle)
    );
  };
  const open = (chat: DeskChat) => !chat.finished && matches(chat);
  const queued = chats.filter((chat) => open(chat) && chat.held && !(chat.agent_name || "").trim());
  const handling = chats.filter((chat) => open(chat) && chat.held && Boolean((chat.agent_name || "").trim()));
  const answering = chats.filter(
    (chat) => open(chat) && !chat.finished && !chat.held && chat.state !== "completed"
  );
  const records = chats.filter(
    (chat) => matches(chat) && (Boolean(chat.finished) || chat.state === "completed")
  );
  const setPresence = (next: boolean) => {
    setReady(next);
    try {
      localStorage.setItem("pathfinder.desk-ready", next ? "ready" : "away");
    } catch {
      // The choice still applies for this visit.
    }
  };

  const submitBrief = () => {
    if (!active || !held || running) return;
    const party = Number(brief.party);
    const budget = Number(brief.budget);
    if (!brief.destination.trim() || !brief.origin.trim() || !brief.start || !brief.end || !party || Number.isNaN(budget)) {
      setError(t("desk.briefHint"));
      return;
    }
    setRunning(true);
    setError(null);
    setNotice(null);
    void saveDeskBrief(active, {
      destination: brief.destination.trim(),
      origin: brief.origin.trim(),
      start_date: brief.start,
      end_date: brief.end,
      party_size: party,
      budget,
      currency: brief.currency || "HKD",
      preferences: brief.preferences.split(",").map((item) => item.trim()).filter(Boolean),
    })
      .then((data) => {
        setNotice(t("desk.saved"));
        if (data.plan) {
          setPlan(data.plan);
          setStops(data.plan.days.flatMap((day) => day.items.map((item) => ({ ...item }))));
        }
        loadChat(active);
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
      .then(() => { setHandId(null); loadPlan(active); loadChat(active); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setRunning(false));
  };
  const rememberVersion = (before: number) => {
    setUndoVersions((stack) => [before, ...stack.filter((version) => version !== before)]);
  };
  const commitStop = (change: StopCommit): Promise<void> => {
    if (!active || !tripPlan || running) return Promise.resolve();
    const { id, previousName, name, start, end, day, lookup } = change;
    if (!/^\d{2}:\d{2}$/.test(start) || !/^\d{2}:\d{2}$/.test(end) || end <= start || !name) {
      setError(t("desk.handHint"));
      return Promise.resolve();
    }
    const picked = findStop(tripPlan, id);
    if (!picked) return Promise.resolve();
    const before = tripPlan.version;
    const changedName = name !== previousName;
    const changedTime = start !== picked.item.start.slice(0, 5) || end !== picked.item.end.slice(0, 5) || day !== picked.day + 1;
    if (!changedName && !changedTime) return Promise.resolve();
    setRunning(true);
    setError(null);
    if (changedName && lookup) setTurnNote(t("sheet.plannerModify"));
    const finish = (plan: TripPlan) => {
      rememberVersion(before);
      setTripPlan(plan);
      publishPlan(active, plan.version);
      loadPlan(active);
      loadChat(active);
    };
    if (changedName && lookup) {
      const placeDay = day;
      return editDeskPlan(active, `Replace ${previousName} with ${name}. Put it on day ${placeDay} from ${start} to ${end}.`, id)
        .then(async (data) => {
          setNotice(data.reply || t("desk.runDone"));
          const fresh = await getDeskItinerary(active);
          const next = fresh.plan;
          if (!next) return;
          const match = next.days.flatMap((day, index) => day.items.map((item) => ({ item, day: index }))).find((entry) => entry.item.name === name);
          if (match && (match.item.start.slice(0, 5) !== start || match.item.end.slice(0, 5) !== end || match.day + 1 !== day)) {
            const laid = await applyDeskLayout(active, [{ id: match.item.id, day, start, end, name }], next.version);
            finish(laid.plan);
            return;
          }
          finish(next);
        })
        .catch((cause) => {
          setError(cause instanceof Error ? cause.message : String(cause));
          throw cause;
        })
        .finally(() => { setRunning(false); setTurnNote(""); });
    }
    const writeTime = changedTime || changedName
      ? applyDeskLayout(active, [{ id, day, start, end, name }], before)
      : Promise.resolve(null);
    return writeTime
      .then(async (laid) => {
        if (laid) { finish(laid.plan); return; }
        const fresh = await getDeskItinerary(active);
        if (fresh.plan) finish(fresh.plan);
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : String(cause));
        throw cause;
      })
      .finally(() => setRunning(false));
  };

  const take = () => {
    if (!active) return;
    if (!held) setPresence(true);
    const run = held ? releaseDeskChat : holdDeskChat;
    void run(active).then((data) => {
      publishHold(active, !held, data.agent_name || "");
      loadChat(active);
      loadList();
    });
  };

  const accept = (sessionId: string) => {
    setPresence(true);
    void holdDeskChat(sessionId).then((data) => {
      setActive(sessionId);
      publishHold(sessionId, true, data.agent_name || "");
      loadChat(sessionId);
      loadList();
    });
  };

  const toggleNav = () => {
    setNavOpen((open) => {
      const next = !open;
      try {
        localStorage.setItem("pathfinder.desk-nav", next ? "open" : "closed");
      } catch {
        // The choice still applies for this visit.
      }
      return next;
    });
  };

  const clampPlace = (next: { x: number; y: number; w: number; h: number }) => {
    const box = mainRef.current?.getBoundingClientRect();
    const width = box?.width ?? 960;
    const height = box?.height ?? 640;
    const w = Math.min(Math.max(next.w, 520), Math.max(520, width - 16));
    const h = Math.min(Math.max(next.h, 320), Math.max(320, height - 16));
    return {
      w,
      h,
      x: Math.min(Math.max(next.x, 8), Math.max(8, width - w - 8)),
      y: Math.min(Math.max(next.y, 8), Math.max(8, height - h - 8)),
    };
  };

  const beginDrag = (kind: "move" | "size") => (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    if (kind === "move" && (event.target as HTMLElement).closest("button, a, input, select, textarea")) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { kind, x: event.clientX, y: event.clientY, place };
  };

  const followDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    setPlace(clampPlace(drag.kind === "move"
      ? { ...drag.place, x: drag.place.x + dx, y: drag.place.y + dy }
      : { ...drag.place, w: drag.place.w + dx, h: drag.place.h + dy }));
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const tile = (chat: DeskChat, waitingTile: boolean) => (
    <div
      key={chat.session_id}
      role="button"
      tabIndex={0}
      className="pf-ops__tile"
      data-waiting={waitingTile || undefined}
      data-stuck={chat.stuck || undefined}
      data-active={chat.session_id === active || undefined}
      onClick={() => setActive(chat.session_id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setActive(chat.session_id);
        }
      }}
    >
      <strong
        className="pf-ops__sid"
        title={`${chat.destination || chat.name || t("desk.untitled")}${tripWhen(chat) ? ` · ${tripWhen(chat)}` : ""}`}
      >
        {chat.destination || chat.name || t("desk.untitled")}
      </strong>
      <span className="pf-ops__time">
        {tripWhen(chat) ? `${tripWhen(chat)} · ${clockText(chat.created_at || chat.updated_at, now)}` : clockText(chat.created_at || chat.updated_at, now)}
      </span>
      <span className="pf-ops__marks">
        {tagText(Boolean(chat.stuck)) ? (
          <span className="pf-ops__mark" data-mark="stuck">{tagText(Boolean(chat.stuck))}</span>
        ) : null}
        {waitingTile ? (
          <button type="button" className="pf-ops__accept" onClick={(event) => { event.stopPropagation(); accept(chat.session_id); }}>
            {t("desk.accept")}
          </button>
        ) : null}
      </span>
    </div>
  );

  const lane = (title: string, items: DeskChat[], waitingTile: boolean) => (
    <div className="pf-ops__lane">
      <div className="pf-ops__lane-card">
        <span className="pf-ops__lane-icon"><Icon name={waitingTile ? "sparkles" : "users"} size={16} /></span>
        <span>{title}</span>
      </div>
      <div className="pf-ops__tiles">
        {items.length === 0 ? <p className="pf-ops__empty">{t("desk.noConnected")}</p> : items.map((chat) => tile(chat, waitingTile))}
      </div>
    </div>
  );

  return (
    <main className="pf-ops">
      <header className="pf-ops__bar">
        <strong className="pf-ops__brand">Pathfinder</strong>
        <div className="pf-ops__status">
          <span className="pf-ops__dot" data-ready={ready || undefined} />
          <span className="pf-ops__status-label">{t("desk.presence")}</span>
          <button type="button" className="pf-ops__status-btn" onClick={() => setPresence(!ready)}>
            {ready ? t("desk.ready") : t("desk.away")}
          </button>
          <button type="button" className="pf-ops__back" onClick={onBack}>{t("desk.back")}</button>
          {onPopOut ? (
            <button type="button" className="pf-ops__back" onClick={onPopOut}>{t("desk.popOut")}</button>
          ) : null}
        </div>
      </header>
      <div className="pf-ops__frame">
        <nav className="pf-ops__nav" data-collapsed={!navOpen || undefined} aria-label={t("desk.title")}>
          <button type="button" data-active={pane === "queue" || undefined} onClick={() => setPane("queue")} title={t("desk.navQueue")}>
            <Icon name="users" size={16} />
            <span>{t("desk.navQueue")}</span>
          </button>
          <button type="button" data-active={pane === "history" || undefined} onClick={() => setPane("history")} title={t("desk.navHistory")}>
            <Icon name="clock" size={16} />
            <span>{t("desk.navHistory")}</span>
          </button>
          <button
            type="button"
            className="pf-ops__nav-toggle"
            aria-label={navOpen ? t("desk.collapseMenu") : t("desk.expandMenu")}
            data-open={navOpen || undefined}
            onClick={toggleNav}
          >
            <Icon name="chevronRight" size={14} />
            <span>{navOpen ? t("desk.collapseMenu") : t("desk.expandMenu")}</span>
          </button>
        </nav>
        <section className="pf-ops__main" ref={mainRef}>
          {error ? <p className="pf-ops__alert" role="alert">{error}</p> : null}
          <div className="pf-ops__scroll">
          <div className="pf-ops__filters">
            <input
              className="pf-ops__filter"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("desk.search")}
              aria-label={t("desk.search")}
            />
            <div className="pf-ops__tags" role="radiogroup" aria-label={t("desk.filter")}>
              {(["all", "stuck"] as const).map((choice) => (
                <button
                  key={choice}
                  type="button"
                  className="pf-ops__tag"
                  role="radio"
                  aria-checked={tag === choice}
                  data-on={tag === choice || undefined}
                  onClick={() => setTag(choice)}
                >
                  {choice === "all" ? t("desk.filterAll") : tagText(true)}
                </button>
              ))}
            </div>
          </div>
          <div className="pf-ops__board">
            <h1>{pane === "queue" ? t("desk.navQueue") : t("desk.navHistory")}</h1>
            <p>
              {pane === "queue" ? `${t("desk.queueLead")} ${t("desk.autoLead")}` : `${records.length}`}
            </p>
            {pane === "queue" ? (
              <>
                {lane(t("desk.mineQueue"), handling, false)}
                {lane(t("desk.queue"), queued, true)}
                {lane(t("desk.navAuto"), answering, false)}
              </>
            ) : (
              lane(t("desk.navHistory"), records, false)
            )}
          </div>
          </div>
          {active && current ? (
            <section
              className="pf-win pf-ops__session"
              aria-label={current.destination || t("desk.untitled")}
              style={{ translate: `${place.x}px ${place.y}px`, width: place.w, height: place.h }}
            >
              <header
                className="pf-win__bar"
                onPointerDown={beginDrag("move")}
                onPointerMove={followDrag}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
              >
                <span className="pf-win__grip" aria-hidden="true"><i /></span>
                <h2>{current.destination || t("desk.untitled")}</h2>
                <button type="button" className="pf-win__close" aria-label={t("panels.close")} onClick={() => setActive(null)}>
                  <Icon name="close" size={16} />
                </button>
              </header>
              <div className="pf-win__body pf-ops__work">
        <section className="pf-center__thread">
          {active && current ? (
            <>
              <div className="pf-desk__bar">
                <div>
                  <Text size="sm" weight="semibold">{current.destination || t("desk.untitled")}</Text>
                  <Text size="xs" tone="tertiary">{tripWhen(current) || current.session_id.slice(0, 8)}</Text>
                </div>
                {current.finished ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      const file = {
                        downloaded_at: new Date().toISOString(),
                        session_id: current.session_id,
                        messages,
                      };
                      const blob = new Blob([JSON.stringify(file, null, 2)], { type: "application/json" });
                      const link = document.createElement("a");
                      link.href = URL.createObjectURL(blob);
                      link.download = `${current.session_id}-log.json`;
                      link.click();
                      URL.revokeObjectURL(link.href);
                    }}
                  >
                    {t("settings.downloadLog")}
                  </Button>
                ) : (
                  <Button variant={held ? "secondary" : "primary"} onClick={take}>{held ? t("desk.release") : t("desk.hold")}</Button>
                )}
              </div>
              <div className="pf-desk__messages" ref={threadRef}>
                <ChatClock since={current.created_at || current.updated_at} />
                {messages.length === 0 ? <Text tone="tertiary">{t("desk.empty")}</Text> : null}
                {messages.map((message) => (
                  <p key={message.id} data-role={message.role} data-live={message.live_agent || undefined}>
                    <span className="pf-desk__who">
                      {message.operator || message.live_agent ? t("desk.person") : message.role === "user" ? t("desk.traveller") : t("desk.planner")}
                    </span>
                    {message.role === "user" ? maskTyped(message.content) : message.content}
                  </p>
                ))}
              </div>
              {current.finished ? (
                <p className="pf-center__lock">{t("desk.finishedOnly")}</p>
              ) : held ? (
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
                <p className="pf-center__lock">{t("desk.replyLocked")}</p>
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
            </dl>
          ) : (
            <Text size="sm" tone="tertiary">{t("desk.pick")}</Text>
          )}
          {current && notice ? <Text size="xs">{notice}</Text> : null}
          {current && held ? (
            <div className="pf-desk__tools">
              <Text size="sm" weight="semibold">{t("desk.brief")}</Text>
              <Text size="xs" tone="tertiary">{t("desk.briefHint")}</Text>
              <label className="pf-desk__field"><span>{t("desk.trip")}</span><input value={brief.destination} disabled={running} onChange={(event) => setBrief({ ...brief, destination: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("desk.from")}</span><input value={brief.origin} disabled={running} onChange={(event) => setBrief({ ...brief, origin: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("desk.dates")}</span><input type="date" value={brief.start} disabled={running} onChange={(event) => setBrief({ ...brief, start: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("desk.dates")}</span><input type="date" value={brief.end} disabled={running} onChange={(event) => setBrief({ ...brief, end: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("workspace.party")}</span><input type="number" min={1} max={20} value={brief.party} disabled={running} onChange={(event) => setBrief({ ...brief, party: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("desk.budget")}</span><input type="number" min={0} value={brief.budget} disabled={running} onChange={(event) => setBrief({ ...brief, budget: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("form.currency")}</span><input value={brief.currency} disabled={running} onChange={(event) => setBrief({ ...brief, currency: event.target.value })} /></label>
              <label className="pf-desk__field"><span>{t("desk.preferences")}</span><input value={brief.preferences} disabled={running} onChange={(event) => setBrief({ ...brief, preferences: event.target.value })} placeholder={t("settings.preferencesExample")} /></label>
              <div className="pf-row" style={{ gap: 6 }}>
                <Button size="sm" disabled={running} onClick={() => submitBrief()}>{t("desk.saveDetails")}</Button>
              </div>
              <Button size="sm" variant="primary" disabled={!plan || !stops.length} onClick={() => setStudioOpen(true)}>
                {t("desk.openStudio")}
              </Button>
              {!plan ? <Text size="xs" tone="tertiary">{t("desk.studioEmpty")}</Text> : null}
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
        </aside>
              </div>
              <span
                className="pf-win__size"
                aria-hidden="true"
                onPointerDown={beginDrag("size")}
                onPointerMove={followDrag}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
              />
            </section>
          ) : null}
        </section>
      </div>
      {studioOpen && tripPlan ? (
        <PlanStudio
          plan={tripPlan}
          title={current?.destination || t("desk.studioTitle")}
          onBack={() => setStudioOpen(false)}
          backLabel={t("desk.studioBack")}
          note={t("desk.samePlan")}
          layer="desk"
          locked={!held}
          lockNote={held ? "" : t("desk.takeToEdit")}
          paused={Boolean(turnNote) || plannerBusy}
          pausedNote={turnNote || plannerNote}
          canApply={held && !running && !plannerBusy}
          saving={running}
          canUndo={undoVersions.length > 0}
          onCommitStop={commitStop}
          actions={(
            <Button size="sm" disabled={!held || !active || running} onClick={() => {
              if (!active) return;
              void releaseDeskChat(active)
                .then(() => sayOnDesk(active, "This plan is a draft you can edit now."))
                .then(() => {
                  setHeld(false);
                  setStudioOpen(false);
                  setNotice(t("desk.released"));
                  publishHold(active, false);
                  loadChat(active);
                  loadList();
                })
                .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
            }}>{t("desk.releaseDraft")}</Button>
          )}
          onUndo={() => {
            if (!active || !undoVersions.length || running) return;
            const version = undoVersions[0];
            setRunning(true);
            setError(null);
            void undoDeskPlan(active, version)
              .then((data) => {
                setTripPlan(data.plan);
                publishPlan(active, data.plan.version);
                setUndoVersions((stack) => stack.slice(1));
                loadPlan(active);
                loadChat(active);
              })
              .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
              .finally(() => setRunning(false));
          }}
          onApply={async (items) => {
            if (!active) return;
            const before = tripPlan.version;
            setRunning(true);
            setError(null);
            try {
              const data = await applyDeskLayout(active, items, before);
              setUndoVersions((stack) => [before, ...stack.filter((version) => version !== before)]);
              setTripPlan(data.plan);
              publishPlan(active, data.plan.version);
              loadPlan(active);
              loadChat(active);
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : String(cause));
              throw cause;
            } finally {
              setRunning(false);
            }
          }}
        />
      ) : null}
    </main>
  );
}
