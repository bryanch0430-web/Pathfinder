import { useState } from 'react';
import type { TripModelResponse } from '../../services/tripService';
import { markTripUseful } from '../../services/tripService';

type Choice = 'useful' | 'not' | 'none';

function savedChoice(trip: TripModelResponse): Choice | null {
  const useful = trip.planning_preferences?.useful;
  if (useful === true) return 'useful';
  if (useful === false) return 'not';
  if (useful === null) return 'none';
  return null;
}

function StarMark({ filled }: { filled: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">
      <path fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.2" d="M8 1.6l1.7 3.6 3.9.5-2.8 2.7.7 3.9L8 10.4 4.5 12.3l.7-3.9L2.4 5.7l3.9-.5L8 1.6z" />
    </svg>
  );
}

export function TripVerdict({
  trip, onClose, onSaved,
}: {
  trip: TripModelResponse;
  onClose: () => void;
  onSaved: (saved: { useful: boolean | null; stars: number | null }) => void;
}) {
  const [choice, setChoice] = useState<Choice | null>(savedChoice(trip));
  const [stars, setStars] = useState<number | null>(trip.planning_preferences?.stars ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const place = trip.destination || 'this trip';
  const days = trip.duration_days ? `${trip.duration_days} ${trip.duration_days === 1 ? 'day' : 'days'} in ${place}` : place;

  const choose = async (next: Choice, rating?: number | null) => {
    if (saving) return;
    setSaving(true);
    setError(null);
    const useful = next === 'useful' ? true : next === 'not' ? false : null;
    const saved = await markTripUseful(trip.id, useful, next === 'useful' ? rating : null);
    setSaving(false);
    if (!saved) {
      setError('That choice could not be saved. Try again.');
      return;
    }
    setChoice(next);
    setStars(saved.stars);
    onSaved(saved);
  };

  return (
    <div className="tv-finalize" role="presentation">
      <section className="tv-finalize__card tv-verdict" role="dialog" aria-modal="true" aria-labelledby="tv-verdict-title">
        <span className="tv-label">AFTER THE TRIP IS FINAL</span>
        <h2 id="tv-verdict-title">Was this plan useful?</h2>
        <p>{days}. This does not change the itinerary. Useful plans can appear, without your name, under trending.</p>
        <div className="tv-verdict__choices" role="group" aria-label="Was this plan useful?">
          {([['useful', 'Useful'], ['not', 'Not useful'], ['none', 'No comment']] as const).map(([value, label]) => (
            <button key={value} type="button" className={choice === value ? 'is-selected' : ''} aria-pressed={choice === value} disabled={saving} onClick={() => { void choose(value, value === 'useful' ? stars : null); }}>
              {label}
            </button>
          ))}
        </div>
        {choice === 'useful' && (
          <div className="tv-verdict__stars" role="group" aria-label="Star rating">
            <span>Optional. This score shows on My trips.</span>
            <div>
              {[1, 2, 3, 4, 5].map((value) => (
                <button key={value} type="button" className={(stars ?? 0) >= value ? 'is-selected' : ''} aria-label={`${value} of 5`} aria-pressed={stars === value} disabled={saving} onClick={() => { void choose('useful', value); }}>
                  <StarMark filled={(stars ?? 0) >= value} />
                </button>
              ))}
            </div>
          </div>
        )}
        {error && <p className="tv-finalize__error" role="alert">{error}</p>}
        <div className="tv-finalize__actions">
          <button type="button" className="tv-btn tv-btn--primary" onClick={onClose}>{choice ? 'Back to the trip' : 'Skip for now'}</button>
        </div>
      </section>
    </div>
  );
}
