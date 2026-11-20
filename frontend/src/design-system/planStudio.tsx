import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button, Text } from "./atoms";
import { Icon } from "./icons";
import { DirectSheet, type LayoutMove, type StopCommit } from "./directEdit";
import type { TripPlan } from "./types";
import { useI18n, type MessageKey } from "../lib/i18n";

function plannerLine(note: string, t: (key: MessageKey) => string): string {
  const text = note.trim();
  if (!text) return t("sheet.plannerBusy");
  if (/understand/i.test(text)) return t("sheet.plannerUnderstand");
  if (/repair|smallest change|modif|updat/i.test(text)) return t("sheet.plannerModify");
  if (/plan|compos|gather|itinerary|locat/i.test(text)) return t("sheet.plannerPlan");
  return text;
}

function locate(plan: TripPlan, id: string) {
  for (let day = 0; day < plan.days.length; day += 1) {
    const item = plan.days[day].items.find((entry) => entry.id === id);
    if (item) return { item, day };
  }
  return null;
}

/** The plan editor shared by the desk and the traveller's Direct edit. */
export function PlanStudio({
  plan,
  title,
  onBack,
  backLabel,
  actions,
  note,
  locked = false,
  lockNote = "",
  paused = false,
  pausedNote = "",
  canApply,
  saving,
  canUndo = false,
  onUndo,
  onApply,
  onCommitStop,
  layer = "traveller",
}: {
  plan: TripPlan;
  title: string;
  onBack: () => void;
  backLabel: string;
  actions?: ReactNode;
  note?: string;
  locked?: boolean;
  lockNote?: string;
  /** Pathfinder is writing the plan, so this editor must not change it too. */
  paused?: boolean;
  pausedNote?: string;
  canApply: boolean;
  saving: boolean;
  canUndo?: boolean;
  onUndo?: () => void;
  onApply?: (items: LayoutMove[]) => Promise<void>;
  onCommitStop?: (change: StopCommit) => Promise<void>;
  /** The desk studio sits above the desk. The traveller's stays with the plan. */
  layer?: "desk" | "traveller";
}) {
  const { t } = useI18n();
  const [day, setDay] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState({ previousName: "", name: "", start: "", end: "", day: 0 });
  const [formNote, setFormNote] = useState("");
  const picked = selectedId ? locate(plan, selectedId) : null;
  useEffect(() => {
    if (!selectedId) return;
    setFormNote("");
    const found = locate(plan, selectedId);
    if (!found) {
      setSelectedId(null);
      return;
    }
    setForm({
      previousName: found.item.name,
      name: found.item.name,
      start: found.item.start.slice(0, 5),
      end: found.item.end.slice(0, 5),
      day: found.day,
    });
  }, [plan, selectedId]);
  const [place, setPlace] = useState(() => {
    const w = Math.min(1100, Math.max(720, window.innerWidth - 96));
    const h = Math.min(720, Math.max(460, window.innerHeight - 96));
    return {
      x: Math.max(24, Math.round((window.innerWidth - w) / 2)),
      y: Math.max(24, Math.round((window.innerHeight - h) / 2)),
      w,
      h,
    };
  });
  const dragRef = useRef<{ kind: "move" | "size"; x: number; y: number; place: typeof place } | null>(null);
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
    const next = drag.kind === "move"
      ? { ...drag.place, x: drag.place.x + dx, y: drag.place.y + dy }
      : { ...drag.place, w: Math.max(720, drag.place.w + dx), h: Math.max(460, drag.place.h + dy) };
    setPlace(next);
  };
  const endDrag = () => { dragRef.current = null; };
  const frozen = locked || paused;
  const nameChanged = form.name.trim() !== form.previousName;
  const timeChanged = !!picked && (form.start !== picked.item.start.slice(0, 5) || form.end !== picked.item.end.slice(0, 5) || form.day !== picked.day);
  const save = (lookup: boolean) => {
    if (!onCommitStop || !picked || frozen) return;
    const name = form.name.trim();
    if (!/^\d{2}:\d{2}$/.test(form.start) || !/^\d{2}:\d{2}$/.test(form.end) || form.end <= form.start || !name) return;
    setFormNote("");
    void Promise.resolve(onCommitStop({
      id: picked.item.id,
      previousName: form.previousName,
      name,
      start: form.start,
      end: form.end,
      day: form.day + 1,
      lookup,
    })).catch((cause: unknown) => {
      setFormNote(cause instanceof Error && cause.message ? cause.message : "");
    });
  };
  return createPortal(
    <section
      className={layer === "desk" ? "pf-win pf-ops__studio pf-ops__studio--desk" : "pf-win pf-ops__studio"}
      role="dialog"
      aria-label={title}
      style={{ left: place.x, top: place.y, width: place.w, height: place.h }}
    >
      <header
        className="pf-win__bar"
        onPointerDown={beginDrag("move")}
        onPointerMove={followDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <span className="pf-win__grip" aria-hidden="true"><i /></span>
        <h2>{title}</h2>
        <button type="button" className="pf-ops__back" onClick={onBack}>{backLabel}</button>
        {actions}
        <button type="button" className="pf-win__close" aria-label={t("panels.close")} onClick={onBack}>
          <Icon name="close" size={16} />
        </button>
      </header>
      <div className="pf-win__body pf-ops__studio-stage">
        <nav className="pf-ops__studio-days" aria-label={t("desk.dates")}>
          <button type="button" data-active={day === null || undefined} onClick={() => setDay(null)}>{t("desk.whole")}</button>
          {plan.days.map((entry, index) => {
            const label = entry.title || entry.date;
            const tip = `${t("day.number", { n: index + 1 })} · ${label} · ${entry.date}`;
            return (
              <button
                type="button"
                key={entry.date}
                title={tip}
                data-active={day === entry.date || undefined}
                onClick={() => setDay(entry.date)}
              >
                <span>{t("day.number", { n: index + 1 })} · {entry.date}</span>
                <strong>{label}</strong>
                <small>{entry.items.length}</small>
              </button>
            );
          })}
        </nav>
        <div className="pf-ops__studio-plan">
          {note ? <Text size="xs" tone="tertiary">{note}</Text> : null}
          <DirectSheet
            plan={plan}
            activeDay={day ?? ""}
            onlyDay={day}
            canApply={canApply && !paused}
            saving={saving}
            locked={frozen}
            lockNote={lockNote}
            canUndo={canUndo}
            onUndo={onUndo}
            onApply={onApply}
            openStopId={selectedId}
            onOpenStop={setSelectedId}
          />
        </div>
        <aside className="pf-ops__studio-edit">
          {picked && !frozen ? (
            <section>
              <Text size="sm" weight="semibold">{t("desk.hand")}</Text>
              <Text size="xs" tone="tertiary">{t("desk.handHint")}</Text>
              <label className="pf-desk__field">
                <span>{picked.item.type}</span>
                <input value={form.name} disabled={saving || picked.item.locked || picked.item.confirmed} onChange={(event) => setForm({ ...form, name: event.target.value })} />
              </label>
              <label className="pf-desk__field">
                <span>{t("desk.dates")}</span>
                <select value={form.day} disabled={saving} onChange={(event) => setForm({ ...form, day: Number(event.target.value) })}>
                  {plan.days.map((entry, index) => <option key={entry.date} value={index}>{t("day.number", { n: index + 1 })} · {entry.date}</option>)}
                </select>
              </label>
              <label className="pf-desk__field">
                <span>{t("desk.start")}</span>
                <input value={form.start} disabled={saving} onChange={(event) => setForm({ ...form, start: event.target.value })} />
              </label>
              <label className="pf-desk__field">
                <span>{t("desk.end")}</span>
                <input value={form.end} disabled={saving} onChange={(event) => setForm({ ...form, end: event.target.value })} />
              </label>
              {picked.item.address ? <Text size="xs" tone="tertiary">{picked.item.address}</Text> : null}
              {nameChanged ? (
                <>
                  <Text size="xs" tone="tertiary">{t("desk.lookUpHint")}</Text>
                  <Button size="sm" variant="primary" disabled={saving || !onCommitStop} onClick={() => save(true)}>{t("desk.lookUp")}</Button>
                  <Button size="sm" disabled={saving || !onCommitStop} onClick={() => save(false)}>{t("desk.saveWords")}</Button>
                </>
              ) : (
                <Button size="sm" variant="primary" disabled={saving || !timeChanged || !onCommitStop} onClick={() => save(false)}>{t("desk.saveTime")}</Button>
              )}
              {formNote ? <Text size="xs" tone="tertiary">{formNote}</Text> : null}
            </section>
          ) : (
            <Text size="sm" tone="tertiary">{locked ? lockNote : t("desk.pickStop")}</Text>
          )}
        </aside>
        {paused ? (
          <div className="pf-ops__studio-cover" role="status">
            <p>{plannerLine(pausedNote, t)}</p>
          </div>
        ) : null}
      </div>
      <span
        className="pf-win__size"
        aria-hidden="true"
        onPointerDown={beginDrag("size")}
        onPointerMove={followDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      />
    </section>,
    document.body,
  );
}
