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
  fixed: boolean;
}

export interface LayoutMove {
  id: string;
  day: number;
  start: string;
  end: string;
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
      fixed: item.type === "transit" || item.locked || item.confirmed,
    })),
  );
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
  canApply,
  saving,
  onApply,
  onFocusDay,
}: {
  plan: TripPlan;
  activeDay: string;
  canApply: boolean;
  saving: boolean;
  onApply?: (items: LayoutMove[]) => Promise<void>;
  onFocusDay?: (date: string) => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<DraftStop[]>(() => readDraft(plan));
  const [version, setVersion] = useState(plan.version);
  const dragId = useRef<string | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  if (plan.version !== version) {
    setVersion(plan.version);
    setDraft(readDraft(plan));
  }
  const range = useMemo(() => {
    let start = 7 * 60;
    let end = 22 * 60;
    for (const item of draft) {
      start = Math.min(start, Math.floor(item.start / 60) * 60);
      end = Math.max(end, Math.ceil(item.end / 60) * 60);
    }
    end = Math.min(24 * 60, Math.max(end, start + 60));
    return { start, end };
  }, [draft]);
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
    if (!scroller || !onFocusDay) return;
    const onScroll = () => {
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

  const retimed = (id: string, start: number, end: number, day = -1) => {
    setDraft((rows) =>
      rows.map((row) => {
        if (row.id !== id || row.fixed) return row;
        const nextStart = snap(start);
        const nextEnd = Math.max(nextStart + 15, end);
        return { ...row, day: day < 0 ? row.day : day, start: nextStart, end: nextEnd };
      }),
    );
  };

  const dropOn = (day: number, event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const id = dragId.current;
    dragId.current = null;
    if (!id) return;
    const row = draft.find((item) => item.id === id);
    if (!row || row.fixed) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const start = range.start + (event.clientY - rect.top) / PX;
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
    <div className="pf-sheet">
      <div className="pf-sheet__bar">
        <Text size="xs" tone="secondary">{stay ? `${stay} · ${t("sheet.hint")}` : t("sheet.hint")}</Text>
        <Button size="sm" variant="primary" disabled={!canApply || !dirty || saving} loading={saving} onClick={apply}>
          {t("sheet.apply")}
        </Button>
      </div>
      <ClashNote clashes={clashes} />
      <div className="pf-sheet__scroller pf-scroll" ref={scrollerRef}>
        <div className="pf-sheet__table" style={{ ["--sheet-span" as string]: String(span), ["--sheet-px" as string]: String(PX) }}>
          <div className="pf-sheet__gutter">
            <div className="pf-sheet__corner" />
            <div className="pf-sheet__hours">
              {Array.from({ length: Math.ceil(span / 60) }, (_, index) => range.start + index * 60).map((minute) => (
                <span key={minute} className="pf-sheet__hour" style={{ top: ((minute - range.start) / span) * 100 + "%" }}>
                  {clock(minute)}
                </span>
              ))}
            </div>
          </div>
          {plan.days.map((day, index) => (
            <div key={day.date} id={`pf-sheet-day-${day.date}`} className="pf-sheet__day" data-active={day.date === activeDay}>
              <div className="pf-sheet__colhead">
                {t("day.number", { n: index + 1 })}
                <b>{new Date(`${day.date}T12:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</b>
              </div>
              <div
                className="pf-sheet__col"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => dropOn(index, event)}
              >
                {draft.filter((item) => item.day === index).map((item) => (
                  <div
                    key={item.id}
                    className="pf-sheet__block"
                    data-type={item.type}
                    data-fixed={item.fixed}
                    data-overlap={overlapping.has(item.id)}
                    style={{
                      top: (item.start - range.start) * PX,
                      height: Math.max(36, (item.end - item.start) * PX),
                    }}
                  >
                    <div
                      className="pf-sheet__name"
                      draggable={!item.fixed}
                      onDragStart={(event) => {
                        dragId.current = item.id;
                        event.dataTransfer.setData("text/plain", item.id);
                        event.dataTransfer.effectAllowed = "move";
                      }}
                      onDragEnd={() => {
                        dragId.current = null;
                      }}
                    >
                      {item.name}
                    </div>
                    {item.fixed ? (
                      <span className="pf-sheet__when">{clock(item.start)}–{clock(item.end)}</span>
                    ) : (
                      <div className="pf-sheet__when">
                        <input
                          key={`${item.id}-s-${item.start}`}
                          aria-label={t("sheet.start")}
                          defaultValue={clock(item.start)}
                          onBlur={(event) => {
                            const match = /^(\d{1,2}):(\d{2})$/.exec(event.target.value.trim());
                            if (!match) return;
                            const start = Number(match[1]) * 60 + Number(match[2]);
                            if (start < 0 || start >= 24 * 60) return;
                            retimed(item.id, start, start + (item.end - item.start));
                          }}
                        />
                        <input
                          key={`${item.id}-d-${item.end - item.start}`}
                          aria-label={t("sheet.duration")}
                          type="number"
                          min={15}
                          step={15}
                          defaultValue={item.end - item.start}
                          onBlur={(event) => {
                            const duration = Number(event.target.value);
                            if (!Number.isFinite(duration) || duration < 15) return;
                            retimed(item.id, item.start, item.start + duration);
                          }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
