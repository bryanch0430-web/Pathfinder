import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { Button, Text } from "./atoms";
import { BannerClose } from "./molecules";
import type { TripPlan } from "./types";
import { useI18n } from "../lib/i18n";

/** One stop in the grid. `day` is 0-based. Times are minutes from midnight. */
interface DraftStop {
  id: string;
  name: string;
  type: string;
  day: number;
  start: number;
  end: number;
  address: string;
  fixed: boolean;
}

export interface LayoutMove {
  id: string;
  day: number;
  start: string;
  end: string;
  name?: string;
}

export interface StopCommit {
  id: string;
  previousName: string;
  name: string;
  start: string;
  end: string;
  /** 1-based day, matching the layout save. */
  day: number;
  lookup: boolean;
}

const PX = 0.7;

function minutesOf(value: string): number {
  const [hour, minute] = value.split(":").map(Number);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return 0;
  return hour * 60 + minute;
}

function clock(minutes: number): string {
  const clamped = Math.max(0, Math.min(23 * 60 + 59, Math.round(minutes)));
  return `${String(Math.floor(clamped / 60)).padStart(2, "0")}:${String(clamped % 60).padStart(2, "0")}`;
}

function snap(minutes: number): number {
  return Math.max(0, Math.min(23 * 60 + 45, Math.round(minutes / 15) * 15));
}

export interface TimeClash {
  label: string;
  a: string;
  b: string;
  ids: [string, string];
}

/** Stops on one day whose times cross, including a travel row. The plan is still saved. */
export function clashesOf(
  groups: { label: string; items: { id: string; name: string; start: string; end: string }[] }[],
): TimeClash[] {
  const found: TimeClash[] = [];
  for (const group of groups) {
    const items = [...group.items].sort((left, right) => left.start.localeCompare(right.start) || left.end.localeCompare(right.end));
    for (let i = 0; i < items.length; i += 1) {
      for (let j = i + 1; j < items.length; j += 1) {
        if (items[j].start >= items[i].end) break;
        found.push({ label: group.label, a: items[i].name, b: items[j].name, ids: [items[i].id, items[j].id] });
      }
    }
  }
  return found;
}

export function clashIds(clashes: TimeClash[]): Set<string> {
  return new Set(clashes.flatMap((clash) => clash.ids));
}

export function ClashNote({ clashes }: { clashes: TimeClash[] }) {
  const { t } = useI18n();
  const shown = clashes.slice(0, 4);
  const signature = clashes.map((clash) => `${clash.label}:${clash.a}:${clash.b}`).join("|");
  const [closed, setClosed] = useState("");
  if (!clashes.length || closed === signature) return null;
  return (
    <div className="pf-banner" data-tone="warning" role="status">
      <span>
        {shown.map((clash) => t("warnings.overlap", { a: clash.a, b: clash.b, day: clash.label })).join(" ")}
        {clashes.length > shown.length ? ` +${clashes.length - shown.length}` : ""} {t("warnings.kept")}
      </span>
      <BannerClose onClose={() => setClosed(signature)} />
    </div>
  );
}

function readDraft(plan: TripPlan): DraftStop[] {
  return plan.days.flatMap((day, index) =>
    day.items.map((item) => ({
      id: item.id,
      name: item.name,
      type: item.type,
      day: index,
      start: minutesOf(item.start),
      end: minutesOf(item.end),
      address: item.address,
      fixed: item.type === "transit" || item.locked || item.confirmed,
    })),
  );
}

function lanesOf(items: { id: string; start: number; end: number }[]) {
  const sorted = [...items].sort((left, right) => left.start - right.start || left.end - right.end);
  const laneEnds: number[] = [];
  const laneOf = new Map<string, number>();
  for (const item of sorted) {
    let lane = laneEnds.findIndex((end) => end <= item.start);
    if (lane < 0) {
      lane = laneEnds.length;
      laneEnds.push(item.end);
    } else {
      laneEnds[lane] = item.end;
    }
    laneOf.set(item.id, lane);
  }
  const colsOf = new Map<string, number>();
  for (const item of sorted) {
    const mates = sorted.filter((other) => other.start < item.end && item.start < other.end);
    colsOf.set(item.id, Math.max(1, ...mates.map((other) => (laneOf.get(other.id) ?? 0) + 1)));
  }
  return { laneOf, colsOf };
}

function sameLayout(plan: TripPlan, draft: DraftStop[]): boolean {
  const saved = readDraft(plan);
  if (saved.length !== draft.length) return false;
  return saved.every((item, index) => {
    const other = draft[index];
    return other.id === item.id && other.day === item.day && other.start === item.start && other.end === item.end;
  });
}

export function DirectSheet({
  plan,
  activeDay,
  onlyDay = null,
  canApply,
  saving,
  canUndo = false,
  onUndo,
  onDraftSteps,
  onApply,
  onFocusDay,
  onOpenStop,
  openStopId = null,
  locked = false,
  lockNote = "",
}: {
  plan: TripPlan;
  activeDay: string;
  /** When set, only this date is on the grid. Empty shows every day. */
  onlyDay?: string | null;
  canApply: boolean;
  saving: boolean;
  canUndo?: boolean;
  onUndo?: () => void;
  onDraftSteps?: (steps: number) => void;
  onApply?: (items: LayoutMove[]) => Promise<void>;
  onFocusDay?: (date: string) => void;
  onOpenStop?: (id: string) => void;
  openStopId?: string | null;
  locked?: boolean;
  lockNote?: string;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<DraftStop[]>(() => readDraft(plan));
  const [version, setVersion] = useState(plan.version);
  const [steps, setSteps] = useState(0);
  const history = useRef<DraftStop[][]>([]);
  const dragId = useRef<string | null>(null);
  const dragged = useRef(false);
  const ghostRef = useRef<HTMLDivElement>(null);
  const ghostAt = useRef<{ id: string; day: number; start: number } | null>(null);
  const [tip, setTip] = useState<{ name: string; when: string; address: string; x: number; y: number } | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const headRef = useRef<HTMLDivElement | null>(null);
  const px = onlyDay ? 1.45 : PX;
  if (plan.version !== version) {
    setVersion(plan.version);
    setDraft(readDraft(plan));
    history.current = [];
    setSteps(0);
  }
  useEffect(() => { onDraftSteps?.(steps); }, [steps, onDraftSteps]);
  const visibleDays = plan.days
    .map((day, index) => ({ day, index }))
    .filter(({ day }) => !onlyDay || day.date === onlyDay);
  const range = useMemo(() => {
    let start = 8 * 60;
    let end = 21 * 60;
    const rows = onlyDay ? draft.filter((item) => plan.days[item.day]?.date === onlyDay) : draft;
    for (const item of rows) {
      start = Math.min(start, Math.floor(item.start / 60) * 60);
      end = Math.max(end, Math.ceil(item.end / 60) * 60);
    }
    end = Math.min(24 * 60, Math.max(end, start + 60));
    return { start, end };
  }, [draft, onlyDay, plan.days]);
  const span = range.end - range.start;
  const dirty = !sameLayout(plan, draft);
  const stay = plan.hotels[0]?.name;
  const clashes = clashesOf(
    plan.days.map((day, index) => ({
      label: `${t("day.number", { n: index + 1 })} · ${day.date}`,
      items: draft
        .filter((item) => item.day === index)
        .map((item) => ({ id: item.id, name: item.name, start: clock(item.start), end: clock(item.end) })),
    })),
  );
  const overlapping = clashIds(clashes);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const onScroll = () => {
      if (headRef.current) headRef.current.scrollLeft = scroller.scrollLeft;
      if (!onFocusDay) return;
      const edge = scroller.getBoundingClientRect().left + 56;
      let next: string | null = null;
      for (const day of plan.days) {
        const node = document.getElementById(`pf-sheet-day-${day.date}`);
        if (node && node.getBoundingClientRect().left <= edge) next = day.date;
      }
      if (next) onFocusDay(next);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [plan, onFocusDay]);

  const remember = () => {
    history.current.push(draft.map((row) => ({ ...row })));
    setSteps(history.current.length);
  };
  const undo = () => {
    const previous = history.current.pop();
    setSteps(history.current.length);
    if (previous) setDraft(previous);
    else onUndo?.();
  };
  const retimed = (id: string, start: number, end: number, day = -1) => {
    remember();
    setDraft((rows) =>
      rows.map((row) => {
        if (row.id !== id || row.fixed) return row;
        const nextStart = snap(start);
        const nextEnd = Math.max(nextStart + 15, end);
        return { ...row, day: day < 0 ? row.day : day, start: nextStart, end: nextEnd };
      }),
    );
  };

  const hideGhost = () => {
    ghostAt.current = null;
    const node = ghostRef.current;
    if (node) node.hidden = true;
  };
  const preview = (day: number, event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    if (locked) return;
    const id = dragId.current;
    const row = id ? draft.find((item) => item.id === id) : undefined;
    const node = ghostRef.current;
    if (!id || !row || row.fixed || !node) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const start = snap(range.start + (event.clientY - rect.top) / px);
    const duration = Math.max(15, row.end - row.start);
    ghostAt.current = { id, day, start };
    node.hidden = false;
    node.style.top = `${rect.top + (start - range.start) * px}px`;
    node.style.left = `${rect.left + 4}px`;
    node.style.width = `${Math.max(48, rect.width - 8)}px`;
    node.style.height = `${Math.max(28, duration * px)}px`;
    node.textContent = `${row.name}  ${clock(start)}–${clock(start + duration)}`;
  };
  const dropOn = (day: number, event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (locked) return;
    const id = dragId.current;
    const seen = ghostAt.current;
    dragId.current = null;
    hideGhost();
    setTip(null);
    if (!id) return;
    const row = draft.find((item) => item.id === id);
    if (!row || row.fixed) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const start = seen && seen.id === id && seen.day === day
      ? seen.start
      : snap(range.start + (event.clientY - rect.top) / px);
    retimed(id, start, start + (row.end - row.start), day);
  };

  const apply = () => {
    if (!onApply || !dirty || saving) return;
    const items = draft
      .filter((item) => !item.fixed)
      .map((item) => ({ id: item.id, day: item.day + 1, start: clock(item.start), end: clock(item.end) }));
    void onApply(items);
  };

  return (
    <div className="pf-sheet" data-span={onlyDay ? "day" : "trip"}>
      <div className="pf-sheet__bar">
        <Text size="xs" tone="secondary">{stay ? `${stay} · ${t("sheet.hint")}` : t("sheet.hint")}</Text>
        <span className="pf-row" style={{ gap: 6 }}>
          <Button size="sm" disabled={saving || (steps === 0 && !canUndo)} onClick={undo}>{t("sheet.undo")}</Button>
          <Button size="sm" variant="primary" disabled={locked || !canApply || !dirty || saving || clashes.length > 0} loading={saving} onClick={apply}>
            {t("sheet.apply")}
          </Button>
        </span>
      </div>
      <ClashNote clashes={clashes} />
      <div ref={ghostRef} className="pf-sheet__ghost" hidden />
      {locked && lockNote ? <p className="pf-sheet__lock">{lockNote}</p> : null}
      {tip ? (
        <div className="pf-sheet__tip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 280), top: Math.min(tip.y + 14, window.innerHeight - 140) }} role="tooltip">
          <strong>{tip.name}</strong>
          <span>{tip.when}</span>
          {tip.address ? <small>{tip.address}</small> : null}
        </div>
      ) : null}
      <div className="pf-sheet__frame">
        <div className="pf-sheet__heads" ref={headRef}>
          <div className="pf-sheet__corner" />
          {visibleDays.map(({ day, index }) => (
            <div key={day.date} className="pf-sheet__colhead" data-active={day.date === activeDay}>
              {t("day.number", { n: index + 1 })}
              <b>{new Date(`${day.date}T12:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</b>
            </div>
          ))}
        </div>
      <div className="pf-sheet__scroller pf-scroll" ref={scrollerRef}>
        <div className="pf-sheet__table" style={{ ["--sheet-span" as string]: String(span), ["--sheet-px" as string]: String(px) }}>
          <div className="pf-sheet__gutter">
            <div className="pf-sheet__hours">
              {Array.from({ length: Math.ceil(span / 60) }, (_, index) => range.start + index * 60).map((minute) => (
                <span key={minute} className="pf-sheet__hour" style={{ top: (minute - range.start) * px }}>
                  {clock(minute)}
                </span>
              ))}
            </div>
          </div>
          {visibleDays.map(({ day, index }) => (
            <div key={day.date} id={`pf-sheet-day-${day.date}`} className="pf-sheet__day" data-active={day.date === activeDay}>
              <div
                className="pf-sheet__col"
                onDragOver={(event) => preview(index, event)}
                onDrop={(event) => dropOn(index, event)}
              >
                {draft.filter((item) => item.day === index).map((item) => {
                  const mates = draft.filter((other) => other.day === index);
                  const { laneOf, colsOf } = lanesOf(mates);
                  const lane = laneOf.get(item.id) ?? 0;
                  const cols = colsOf.get(item.id) ?? 1;
                  return (
                  <div
                    key={item.id}
                    className="pf-sheet__block"
                    data-type={item.type}
                    data-fixed={item.fixed}
                    data-overlap={overlapping.has(item.id)}
                    data-open={openStopId === item.id || undefined}
                    style={{
                      top: (item.start - range.start) * px,
                      height: Math.max(onlyDay ? 64 : 28, (item.end - item.start) * px),
                      left: cols > 1 ? `calc(${(lane / cols) * 100}% + 2px)` : 3,
                      width: cols > 1 ? `calc(${100 / cols}% - 4px)` : undefined,
                      right: cols > 1 ? "auto" : 3,
                      zIndex: openStopId === item.id ? 4 : lane + 1,
                    }}
                    onMouseEnter={(event) => {
                      if (dragId.current) return;
                      setTip({ name: item.name, when: `${clock(item.start)}–${clock(item.end)}`, address: item.address, x: event.clientX, y: event.clientY });
                    }}
                    onMouseMove={(event) => {
                      if (dragId.current) return;
                      setTip((current) => current ? { ...current, x: event.clientX, y: event.clientY } : current);
                    }}
                    onMouseLeave={() => setTip(null)}
                    onClick={() => {
                      if (dragged.current || locked || !onOpenStop) return;
                      onOpenStop(item.id);
                    }}
                  >
                    <div
                      className="pf-sheet__name"
                      draggable={!item.fixed && !locked}
                      onDragStart={(event) => {
                        if (locked || item.fixed) { event.preventDefault(); return; }
                        dragged.current = true;
                        dragId.current = item.id;
                        event.dataTransfer.setData("text/plain", item.id);
                        event.dataTransfer.effectAllowed = "move";
                      }}
                      onDragEnd={() => {
                        dragId.current = null;
                        hideGhost();
                        window.setTimeout(() => { dragged.current = false; }, 0);
                      }}
                    >
                      {item.name}
                      {onlyDay && item.address ? <small className="pf-sheet__where">{item.address}</small> : null}
                    </div>
                    <span className="pf-sheet__when">{clock(item.start)}–{clock(item.end)}</span>
                  </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
      </div>
    </div>
  );
}
