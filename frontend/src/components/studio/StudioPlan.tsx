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
  pinned?: string | null;
  onPin?: (name: string) => void;
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
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    if (url.includes('/travel/flights')) return 'Book flight';
    if (host.includes('google') || host.startsWith('maps.')) return 'Google Maps';
    return host.split('.')[0];
  } catch { return 'source'; }
};

/** The sentence before a dumped week of hours. */
function placeNote(tip?: string | null): string | null {
  if (!tip) return null;
  const note = tip.split(/\s+Hours:\s*/i)[0].replace(/\s+/g, ' ').trim();
  return note || null;
}

/** A week of identical days becomes one line. The rest keeps the first two days. */
function briefHours(raw?: string | null): string | null {
  const text = (raw || '').trim();
  if (!text) return null;
  const rows = text.split(';').map((part) => {
    const split = part.trim().indexOf(':');
    if (split < 1) return null;
    return { day: part.trim().slice(0, split).trim(), span: part.trim().slice(split + 1).trim() };
  }).filter((row): row is { day: string; span: string } => Boolean(row?.span));
  if (!rows.length) return text.length > 72 ? `${text.slice(0, 69).replace(/\s+\S*$/, '')}…` : text;
  const spans = [...new Set(rows.map((row) => row.span))];
  if (spans.length === 1) {
    if (rows.length >= 7) return `Every day ${spans[0]}`;
    if (rows.length === 1) return `${rows[0].day.slice(0, 3)} ${spans[0]}`;
    return `${rows[0].day.slice(0, 3)}–${rows[rows.length - 1].day.slice(0, 3)} ${spans[0]}`;
  }
  const shown = rows.slice(0, 2).map((row) => `${row.day.slice(0, 3)} ${row.span}`);
  return rows.length > 2 ? `${shown.join(' · ')} · later days differ` : shown.join(' · ');
}

function Item({ item, currency, when, pinned, onPin }: { item: TripDocumentItem; currency: string; when?: string | null; pinned?: boolean; onPin?: (name: string) => void }) {
  const note = placeNote(item.tip);
  const hoursInTip = item.tip?.split(/\s+Hours:\s*/i)[1];
  const hours = briefHours(item.hours || hoursInTip);
  return (
    <li className={`tv-plan__item${when ? ' has-time' : ''}${pinned ? ' is-pinned' : ''}`}>
      {when && <time className="tv-plan__time">{when}</time>}
      <div>
        <strong>{item.name}</strong>
        <span className="tv-plan__meta">
          {item.category}{item.area ? ` · ${item.area}` : ''}{hours ? ` · ${hours}` : ''}
          {item.est_cost !== null ? ` · ~${money(item.est_cost, currency)} pp` : ''}
          {item.duration_hours ? ` · ${item.duration_hours} h` : ''}
        </span>
      </div>
      {(note || item.source_url) && (
        <p className="tv-plan__tip">
          {note ? `“${note}”` : null}
          {item.source_url && <> <a href={item.source_url} target="_blank" rel="noreferrer">{sourceLabel(item.source_url)}</a></>}
        </p>
      )}
      {onPin && <button type="button" className="tv-plan__pin" aria-pressed={pinned} onClick={() => onPin(item.name)}>
        {pinned ? 'Asking about this' : 'Ask about this'}
      </button>}
    </li>
  );
}

export function StudioPlan({ document, day, onSelectDay, pinned, onPin }: Props) {
  const currency = document.budget.currency;
  if (!document.days.length) {
    return <p className="tv-plan__empty">The plan appears here once your itinerary is drafted.</p>;
  }

  if (day === null || !document.days.some((entry) => entry.day === day)) {
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

  const current = document.days.find((entry) => entry.day === day)!;
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
              return <Item key={row.key} item={source} currency={currency} when={when} pinned={pinned === source.name} onPin={onPin} />;
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
