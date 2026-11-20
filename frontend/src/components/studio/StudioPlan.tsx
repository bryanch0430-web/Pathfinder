/*
 * StudioPlan — the trip document as a readable day-by-day plan.
 * Overview shows every day; a selected day groups its places by time of day.
 */
import type { TripDocument, TripDocumentItem } from '../../services/tripService';
import { formatClock, scheduleDay } from '../../lib/dayRoute';

interface Props {
  document: TripDocument;
  day: number | null;
  onSelectDay: (day: number | null) => void;
}

export const dayDate = (iso: string | null, style: 'short' | 'long' = 'short') => iso
  ? new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, style === 'long'
    ? { weekday: 'long', day: 'numeric', month: 'long' } : { weekday: 'short', day: 'numeric', month: 'short' })
  : null;

export const money = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount);
  } catch {
    return `${Math.round(amount)} ${currency}`;
  }
};

const sourceLabel = (url: string) => {
  try { return new URL(url).hostname.replace(/^www\./, '').split('.')[0]; } catch { return 'source'; }
};

function Item({ item, currency, when }: { item: TripDocumentItem; currency: string; when?: string | null }) {
  return (
    <li className={`tv-plan__item${when ? ' has-time' : ''}`}>
      {when && <time className="tv-plan__time">{when}</time>}
      <div>
        <strong>{item.name}</strong>
        <span className="tv-plan__meta">
          {item.category}{item.area ? ` · ${item.area}` : ''}
          {item.est_cost !== null ? ` · ~${money(item.est_cost, currency)} pp` : ''}
          {item.duration_hours ? ` · ${item.duration_hours} h` : ''}
        </span>
      </div>
      {item.tip && (
        <p className="tv-plan__tip">
          “{item.tip}”
          {item.source_url && <> <a href={item.source_url} target="_blank" rel="noreferrer">via {sourceLabel(item.source_url)}</a></>}
        </p>
      )}
    </li>
  );
}

export function StudioPlan({ document, day, onSelectDay }: Props) {
  const currency = document.budget.currency;
  if (!document.days.length) {
    return <p className="tv-plan__empty">The plan appears here once your itinerary is drafted.</p>;
  }

  if (day === null) {
    return (
      <div className="tv-plan tv-plan--overview">
        {document.days.map((entry) => (
          <button key={entry.day} type="button" className="tv-plan__daycard" onClick={() => onSelectDay(entry.day)}>
            <span className="tv-label">DAY {entry.day}{entry.date ? ` · ${dayDate(entry.date)?.toUpperCase()}` : ''}</span>
            <strong>{entry.base}</strong>
            <span>
              {entry.items.filter((item) => !item.option).slice(0, 3).map((item) => item.name).join(', ')
                || (entry.items.length ? `${entry.items.length} options to choose from` : 'Open day')}
            </span>
            {document.route_warnings?.some((warning) => warning.day === entry.day) &&
              <small>Travel check needs review</small>}
          </button>
        ))}
      </div>
    );
  }

  const current = document.days.find((entry) => entry.day === day);
  if (!current) return null;
  const planned = current.items.filter((item) => !item.option);
  const options = current.items.filter((item) => item.option);
  return (
    <article className="tv-plan">
      <header className="tv-plan__head">
        <span className="tv-label">DAY {current.day} OF {document.days.length}</span>
        <h2>{current.base}</h2>
        {current.date && <p>{dayDate(current.date, 'long')}</p>}
      </header>
      {current.travel_hours != null && current.items.length > 0 && <p className="tv-plan__meta">
        About {current.travel_hours} h travel from {current.base} through the planned places and back
        {current.travel_distance_km ? ` · ${current.travel_distance_km} km straight line` : ''}
        {current.unverified_legs ? ` · ${current.unverified_legs} unverified leg${current.unverified_legs === 1 ? '' : 's'}`
          : ` · ${current.travel_basis === 'road' ? 'road time' : 'distance estimate'}`}
      </p>}
      {document.route_warnings?.filter((warning) => warning.day === current.day).map((warning, index) =>
        <p key={index} className="tv-plan__tip" role="note">Travel check: {warning.message}</p>)}
      {planned.length > 0 && (
        <section className="tv-plan__slot" aria-label="Day schedule">
          <h3>Schedule</h3>
          <ul>
            {scheduleDay(planned).slice().sort((a, b) => a.startMin - b.startMin).map((row) => {
              const source = planned[Number(row.key.slice(row.key.lastIndexOf('-') + 1))];
              const when = source.start
                ? `${source.start}${source.end ? `–${source.end}` : ''}`
                : `${formatClock(row.startMin)}–${formatClock(row.endMin)}`;
              return <Item key={row.key} item={source} currency={currency} when={when} />;
            })}
          </ul>
        </section>
      )}
      {options.length > 0 && (
        <section className="tv-plan__slot tv-plan__slot--options" aria-label="Pick one">
          <h3>Pick one</h3>
          <ul>{options.map((item) => <Item key={item.name} item={item} currency={currency} />)}</ul>
        </section>
      )}
      {!current.items.length && <p className="tv-plan__empty">Nothing planned for this day yet.</p>}
    </article>
  );
}
