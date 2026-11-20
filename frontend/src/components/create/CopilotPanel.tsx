/*
 * CopilotPanel — build-with-agent dock above the composer.
 *
 * Closed, it is one line (the day on screen, its hours, the trip budget) plus the day's
 * suggestions, so the conversation keeps the screen. "Day plan" opens the rest: every day,
 * what's planned on this one, and Finish.
 *
 * Each day is its own state: its plan, its ranked suggestions and the agent's
 * last notes for it all arrive with every reply. Switching days is purely
 * local, so it never waits on the agent. Only real decisions (add, remove,
 * finish, or a typed message) go to the server, tagged with the day on screen.
 */
import { useState } from 'react';
import type { TripBudget } from '../../services/tripService';
import { ArrowDownIcon, ArrowRightIcon, PlusIcon } from '../home/v2/IconsV2';
import { MarkdownMessage } from './MarkdownMessage';

export interface CopilotItem {
  name: string;
  area?: string | null;
  category: string;
  est_cost: number | null;
  duration_hours: number;
  why?: string;
  source?: string | null;
}

export interface CopilotDay {
  day: number;
  base: string;
  items: CopilotItem[];
  suggestions?: string[];
  reply?: string;
}

export interface CopilotState {
  status: 'building' | 'complete';
  current_day: number;
  currency: string;
  budget: number | null;
  days: CopilotDay[];
  pool: CopilotItem[];
  last_suggestions: string[];
  profile: { travel_mode: string; pace: string };
  summary?: {
    committed: number;
    baseline: number;
    remaining: number | null;
    over: boolean;
    by_day: { day: number; cost: number; hours: number; capacity_hours: number;
      travel_hours?: number; distance_km?: number; unverified_legs?: number; travel_basis?: string }[];
  };
}

export type CopilotOp = Record<string, unknown> & { op: string };

interface Props {
  copilot: CopilotState;
  budget?: TripBudget;
  viewDay: number;
  onViewDay: (day: number) => void;
  disabled?: boolean;
  onOps: (ops: CopilotOp[], label: string, day: number) => void;
}

function money(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount);
  } catch {
    return `${Math.round(amount)} ${currency}`;
  }
}

export function CopilotPanel({ copilot, budget, viewDay, onViewDay, disabled, onOps }: Props) {
  const [open, setOpen] = useState(false);
  const day = copilot.days[viewDay - 1] || copilot.days[0];
  if (!day) return null;
  const stats = copilot.summary?.by_day.find((entry) => entry.day === day.day);
  const suggestions = (day.suggestions ?? (day.day === copilot.current_day ? copilot.last_suggestions : []))
    .map((name) => copilot.pool.find((item) => item.name === name))
    .filter((item): item is CopilotItem => Boolean(item));
  const committed = budget ? Number(budget.projected_total) : copilot.summary?.committed ?? 0;
  const target = budget ? (budget.target_amount === null ? null : Number(budget.target_amount)) : copilot.budget;
  const currency = budget?.currency || copilot.currency;
  const used = target ? Math.min(100, committed / target * 100) : 0;
  const complete = copilot.status === 'complete';
  const act = (ops: CopilotOp[], label: string) => onOps(ops, label, day.day);

  return (
    <section className="tv-copilot" aria-label="Build with the agent">
      <div className="tv-copilot__bar">
        <div className="tv-copilot__stepper">
          <button type="button" className="is-back" disabled={day.day <= 1} onClick={() => onViewDay(day.day - 1)} aria-label="Previous day">
            <ArrowRightIcon width={14} height={14} />
          </button>
          <p aria-live="polite">
            <strong>Day {day.day} of {copilot.days.length} · {day.base}</strong>
            <span>
              {day.items.length ? `${day.items.length} place${day.items.length === 1 ? '' : 's'}` : 'Open day'}
              {stats ? ` · ${stats.hours} of ${stats.capacity_hours} h` : ''}
              {stats?.travel_hours ? ` · ${stats.travel_hours} h travel` : ''}
            </span>
          </p>
          <button type="button" disabled={day.day >= copilot.days.length} onClick={() => onViewDay(day.day + 1)} aria-label="Next day">
            <ArrowRightIcon width={14} height={14} />
          </button>
        </div>

        <div className={`tv-copilot__budget ${target !== null && committed > target ? 'is-over' : ''}`}>
          <span>
            {target !== null
              ? `${budget?.is_locked ? 'Final ' : '~'}${money(committed, currency)} planned of ${money(target, currency)}${committed > target ? ' · over budget' : ''}`
              : `~${money(committed, currency)} planned · no budget set`}
          </span>
          {target !== null ? <i role="progressbar" aria-label="Budget used" aria-valuenow={Math.round(used)}
            aria-valuemin={0} aria-valuemax={100}><b style={{ width: `${used}%` }} /></i> : null}
        </div>

        <button type="button" className="tv-copilot__toggle" aria-expanded={open} aria-controls="copilot-detail"
          onClick={() => setOpen((value) => !value)}>
          Day plan<ArrowDownIcon width={13} height={13} />
        </button>
      </div>

      {suggestions.length > 0 && (
        <div className="tv-copilot__chips" aria-label={`Suggestions for day ${day.day}`}>
          {suggestions.map((item) => (
            <button key={item.name} type="button" disabled={disabled} title={item.why || undefined}
              onClick={() => act([{ op: 'add', name: item.name, day: day.day }], `Add ${item.name} to day ${day.day}`)}>
              <PlusIcon width={11} height={11} />{item.name}
              {item.est_cost !== null && <small>~{money(item.est_cost, copilot.currency)}</small>}
            </button>
          ))}
        </div>
      )}

      {open && (
        <div className="tv-copilot__detail" id="copilot-detail">
          {stats && day.items.length > 0 && <p className="tv-copilot__empty">
            Travel: ~{stats.travel_hours ?? 0} h from {day.base} through these places and back.
            {stats.distance_km ? ` ${stats.distance_km} km straight line.` : ''}
            {stats.unverified_legs ? ` ${stats.unverified_legs} leg${stats.unverified_legs === 1 ? '' : 's'} unverified; timing is provisional.`
              : ` ${stats.travel_basis === 'road' ? 'Road time where available.' : 'Distance-based estimate.'}`}
          </p>}
          <nav className="tv-copilot__days" aria-label="Trip days">
            {copilot.days.map((entry) => (
              <button key={entry.day} type="button" onClick={() => onViewDay(entry.day)}
                aria-current={entry.day === day.day ? 'step' : undefined}
                className={entry.day === day.day ? 'is-current' : ''}>
                <span>D{entry.day}</span>
                <small>{entry.items.length ? `${entry.items.length} planned` : entry.base}</small>
              </button>
            ))}
          </nav>

          {day.items.length ? (
            <ul className="tv-copilot__plan" aria-label={`Day ${day.day} plan`}>
              {day.items.map((item) => (
                <li key={item.name}>
                  <span>{item.name}{item.area ? <small> · {item.area}</small> : null}</span>
                  <small>{item.est_cost !== null ? `~${money(item.est_cost, copilot.currency)}` : 'cost unknown'} · {item.duration_hours} h</small>
                  <button type="button" disabled={disabled} aria-label={`Remove ${item.name} from day ${day.day}`}
                    onClick={() => act([{ op: 'remove', name: item.name }], `Remove ${item.name} from day ${day.day}`)}>Remove</button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="tv-copilot__empty">Nothing planned for day {day.day} yet. Tap a suggestion, or tell me what you'd like to do.</p>
          )}

          {day.reply && day.day !== copilot.current_day && (
            <details className="tv-copilot__notes">
              <summary>Notes for day {day.day}</summary>
              <MarkdownMessage content={day.reply} />
            </details>
          )}

          <div className="tv-copilot__actions">
            <span className="tv-copilot__done">
              {copilot.summary?.baseline ? `Includes ~${money(copilot.summary.baseline, copilot.currency)} stay and food. Flights are in Budget.` : ''}
              {complete ? ' Itinerary finished; you can keep changing it.' : ''}
            </span>
            {!complete && (
              <button type="button" className="tv-btn tv-btn--sm tv-btn--ghost" disabled={disabled}
                onClick={() => act([{ op: 'finish' }], 'Finish the itinerary')}>
                Finish itinerary
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
