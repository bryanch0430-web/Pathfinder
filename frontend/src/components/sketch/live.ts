/*
 * Live sketch: fold the agent's streamed copilot state into the trip document (so the
 * sketch updates mid-reply, before the document is refetched), and diff two layouts
 * to find what the guide should draw.
 */
import type { CopilotState } from '../create/CopilotPanel';
import type { TripBudget, TripDocument } from '../../services/tripService';
import type { SketchPage } from './types';

const addDays = (iso: string | null, days: number) => {
  if (!iso) return null;
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
};

/** The same mapping as the backend's `trip_document._agent_days`, applied to a streamed copilot state. */
export function withCopilot(doc: TripDocument, copilot: CopilotState | null, budget?: TripBudget): TripDocument {
  if (!copilot || doc.mode === 'one_shot') return doc;
  const totals = new Map((copilot.summary?.by_day ?? []).map((entry) => [entry.day, entry]));
  const dates = new Map(doc.days.map((day) => [day.day, day.date]));
  return {
    ...doc,
    mode: 'agent',
    status: copilot.status,
    days: copilot.days.map((day) => ({
      day: day.day, base: day.base,
      date: dates.get(day.day) ?? addDays(doc.start_date, day.day - 1),
      est_cost: totals.get(day.day)?.cost ?? null, hours: totals.get(day.day)?.hours ?? null,
      items: day.items.map((item) => ({
        name: item.name, category: item.category || 'other', time_of_day: null, area: item.area ?? null,
        est_cost: item.est_cost ?? null, duration_hours: item.duration_hours ?? null, tip: item.why || null,
        source_url: item.source ?? null, option: false,
      })),
    })),
    budget: budget ? { ...doc.budget, currency: budget.currency,
      target: budget.target_amount === null ? null : Number(budget.target_amount),
      entered: Number(budget.priced_total), projected: Number(budget.projected_total),
      planned: Number(budget.projected_total), is_locked: budget.is_locked,
      locked_total: budget.locked_total === null ? null : Number(budget.locked_total) }
      : { ...doc.budget, target: copilot.budget ?? doc.budget.target,
        planned: copilot.summary?.committed ?? doc.budget.planned },
  };
}

export interface PageChange {
  page: SketchPage;
  /** Element ids that weren't on the page before: the only things to draw. */
  added: string[];
  addedPlaces: string[];
  removedPlaces: string[];
}

const places = (page: SketchPage | undefined) => new Map(
  (page?.elements ?? []).flatMap((el) => (el.kind === 'box' && el.label ? [[el.id, el.label] as const] : [])),
);

/** Pages whose elements changed between two layouts of the same trip, in page order. */
export function diffPages(prev: SketchPage[], next: SketchPage[]): PageChange[] {
  const before = new Map(prev.map((page) => [page.id, page]));
  return next.flatMap((page) => {
    const old = before.get(page.id);
    const oldIds = new Set(old?.elements.map((el) => el.id));
    const added = page.elements.filter((el) => !oldIds.has(el.id)).map((el) => el.id);
    const [was, now] = [places(old), places(page)];
    const addedPlaces = [...now].filter(([id]) => !was.has(id)).map(([, name]) => name);
    const removedPlaces = [...was].filter(([id]) => !now.has(id)).map(([, name]) => name);
    return added.length || removedPlaces.length ? [{ page, added, addedPlaces, removedPlaces }] : [];
  });
}

/** What the guide says about a change: only names and numbers from the trip, nothing invented. */
export function describeChange(change: PageChange): string | null {
  const where = change.page.day ? ` day ${change.page.day}` : '';
  const list = (names: string[]) => (names.length > 2 ? `${names.slice(0, 2).join(', ')} and ${names.length - 2} more` : names.join(' and '));
  if (change.addedPlaces.length && change.removedPlaces.length) return `Swapped ${list(change.removedPlaces)} for ${list(change.addedPlaces)} on${where}.`;
  if (change.addedPlaces.length) return `Added ${list(change.addedPlaces)} to${where}.`;
  if (change.removedPlaces.length) return `Took ${list(change.removedPlaces)} off${where}.`;
  return null;
}
