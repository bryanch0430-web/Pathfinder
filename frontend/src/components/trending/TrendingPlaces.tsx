import { useEffect, useState } from 'react';
import { apiFetch } from '../../services/apiClient';
import { ArrowUpRightIcon } from '../home/v2/IconsV2';

export interface TrendingCard {
  destination: string;
  plans: number;
  days: number | null;
  year: number | null;
  stays?: string[];
  places: string[];
  stars: number | null;
}

export const NEW_PLAN_KEY = 'tripverse-new-plan';

/** Asks the planner to open a new trip and the brief form, with this destination filled in. */
export function planTrending(card: TrendingCard) {
  const seed = {
    destination: card.destination,
    days: card.days,
    places: card.places.slice(0, 8),
  };
  try {
    sessionStorage.setItem(NEW_PLAN_KEY, JSON.stringify(seed));
  } catch {
    // Storage blocked: the planner still opens a blank new trip.
  }
  window.dispatchEvent(new Event('tripverse-new-plan'));
}

export function TrendingPlaces({ onPlan }: { onPlan: (card: TrendingCard) => void }) {
  const [items, setItems] = useState<TrendingCard[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void (async () => {
        setLoading(true);
        const params = new URLSearchParams({ limit: '6' });
        if (query.trim()) params.set('q', query.trim());
        const response = await apiFetch<{ items: TrendingCard[] }>(`/api/trips/trending?${params}`);
        if (response.ok && response.data) {
          setItems(response.data.items);
          setError(null);
        } else {
          setError('Trending destinations could not be loaded.');
        }
        setLoading(false);
      })();
    }, query ? 250 : 0);
    return () => window.clearTimeout(handle);
  }, [query]);

  return (
    <section className="tv-trend" aria-labelledby="tv-trend-title">
      <header className="tv-trend__head">
        <div>
          <span className="tv-eyebrow">Trending</span>
          <h2 className="tv-display tv-trend__title" id="tv-trend-title">What other travellers <em>kept.</em></h2>
          <p className="tv-meta">Plans marked useful. Destination, length, overnight cities and sights. No budget, origin, party size or who planned it.</p>
        </div>
        <label className="tv-trend__search">
          <span className="tv-field__label">Search</span>
          <input className="tv-input" type="search" value={query} placeholder="Destination or sight" onChange={(event) => setQuery(event.target.value)} />
        </label>
      </header>
      {loading && <p className="tv-meta" role="status">Loading kept plans…</p>}
      {error && <p className="tv-meta" role="alert">{error}</p>}
      {!loading && !error && !items.length && (
        <p className="tv-meta">Nothing public yet. Mark a finished plan useful and its destination can show up here.</p>
      )}
      {!!items.length && (
        <div className="tv-trend__list">
          {items.map((item) => (
              <article className="tv-trend__card" key={item.destination}>
                <div className="tv-trend__top">
                  <h3>{item.destination}</h3>
                  <span className="tv-meta">{item.plans} {item.plans === 1 ? 'plan' : 'plans'}</span>
                </div>
                <p className="tv-meta">
                  {item.days ? `${item.days} ${item.days === 1 ? 'day' : 'days'}` : 'Length varies'}
                  {item.year ? ` · ${item.year}` : ''}
                  {item.stars ? ` · ${item.stars} / 5` : ''}
                </p>
                {!!item.stays?.length && <p className="tv-meta">Nights in {item.stays.join(', ')}</p>}
                {!!item.places.length && (
                  <ul className="tv-trend__places">
                    {item.places.slice(0, 8).map((place) => <li key={place}>{place}</li>)}
                  </ul>
                )}
                <p className="tv-meta">Starts a new trip. You fill in where you are travelling from.</p>
                <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" onClick={() => onPlan(item)}>
                  Start a new plan <ArrowUpRightIcon width={14} height={14} />
                </button>
              </article>
          ))}
        </div>
      )}
    </section>
  );
}
