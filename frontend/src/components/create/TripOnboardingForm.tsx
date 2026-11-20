import React, { useEffect, useRef, useState } from 'react';
import { GUIDES, DEFAULT_GUIDE } from '../guide/guides';

type Pace = 'relaxed' | 'balanced' | 'packed';
type Comfort = 'budget' | 'mid_range' | 'comfortable';
type TravelMode = 'transit' | 'walk' | 'taxi' | 'drive';
const CURRENCIES = ['INR', 'JPY', 'USD', 'EUR', 'GBP', 'AUD', 'CAD'] as const;

export interface OnboardingValues {
  origin: string;
  destination: string;
  places_to_visit: string[];
  duration_days: number;
  planning_preferences: {
    pace: Pace;
    interests: string[];
    avoid: string[];
    start_date?: string | null;
    adults?: number;
    children?: number;
    comfort?: Comfort;
    travel_mode?: TravelMode;
    guide?: string | null;
    home_currency?: (typeof CURRENCIES)[number] | null;
  };
  budget_amount?: number | null;
  currency?: (typeof CURRENCIES)[number] | null;
}

interface Props {
  initial?: Partial<OnboardingValues>;
  disabled?: boolean;
  onSubmit: (values: OnboardingValues) => void;
  /** Every section on one page: for editing a brief that is already filled in. */
  allAtOnce?: boolean;
}

/** One small question at a time. Only the first step has required fields; the rest can be skipped. */
const STEPS = [
  { title: 'Where are you going, and for how long?', note: 'From, to and the number of days are all I need to start.' },
  { title: 'Who’s coming, and what’s the budget?', note: 'Optional. Skip it and I’ll plan for one adult, mid-range.' },
  { title: 'How do you like to travel?', note: 'Optional. It sets the pace and what I pick for you.' },
  { title: 'Who should guide you?', note: 'Your guide sketches the trip and chats with you along the way.' },
];

const today = () => new Date().toISOString().slice(0, 10);

function returnLabel(start: string, days: number): string {
  if (!start || !Number.isInteger(days) || days < 1) return '';
  const end = new Date(`${start}T00:00:00`);
  end.setDate(end.getDate() + days - 1);
  return end.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

export const TripOnboardingForm: React.FC<Props> = ({ initial, disabled, onSubmit, allAtOnce = false }) => {
  const prefs = initial?.planning_preferences;
  const [origin, setOrigin] = useState(initial?.origin || '');
  const [destination, setDestination] = useState(initial?.destination || '');
  const [places, setPlaces] = useState((initial?.places_to_visit || []).join(', '));
  const [days, setDays] = useState(initial?.duration_days?.toString() || '');
  const [startDate, setStartDate] = useState(prefs?.start_date || '');
  const [adults, setAdults] = useState(String(prefs?.adults ?? 1));
  const [children, setChildren] = useState(String(prefs?.children ?? 0));
  const [budget, setBudget] = useState(initial?.budget_amount ? String(initial.budget_amount) : '');
  const [currency, setCurrency] = useState<(typeof CURRENCIES)[number]>(initial?.currency || 'INR');
  const [homeCurrency, setHomeCurrency] = useState<(typeof CURRENCIES)[number]>(prefs?.home_currency || 'INR');
  const [comfort, setComfort] = useState<Comfort>(prefs?.comfort || 'mid_range');
  const [travelMode, setTravelMode] = useState<TravelMode>(prefs?.travel_mode || 'transit');
  const [pace, setPace] = useState<Pace>(prefs?.pace || 'balanced');
  const [interests, setInterests] = useState((prefs?.interests || []).join(', '));
  const [avoid, setAvoid] = useState((prefs?.avoid || []).join(', '));
  const [guide, setGuide] = useState(prefs?.guide || DEFAULT_GUIDE.id);
  const [error, setError] = useState('');
  const [step, setStep] = useState(0);
  const last = step === STEPS.length - 1;
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  // A new step announces itself and puts the keyboard at its top (not on first render: the chat keeps focus).
  useEffect(() => {
    if (moved.current) heading.current?.focus();
    moved.current = true;
  }, [step]);

  const duration = Number(days);
  const returning = returnLabel(startDate, duration);
  const list = (value: string) => [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))];
  const requested = list(places);
  const selectedInterests = list(interests);
  const selectedAvoid = list(avoid);
  const adultCount = Number(adults);
  const childCount = Number(children);
  const budgetAmount = budget.trim() ? Number(budget) : null;

  /** The first problem in steps 0..upTo, with the step it lives on, so "Skip the rest" can send you back to it. */
  const problem = (upTo: number): { step: number; message: string } | null => {
    const checks: [boolean, string][][] = [
      [[!origin.trim() || !destination.trim() || !Number.isInteger(duration) || duration < 1 || duration > 365,
        'Add where you’re travelling from, where to, and how many days (1–365).'],
      [requested.length > 20 || requested.some((place) => place.length > 255),
        'Add up to 20 places, with each name under 255 characters.']],
      [[!Number.isInteger(adultCount) || adultCount < 1 || adultCount > 20 || !Number.isInteger(childCount) || childCount < 0 || childCount > 20,
        'Travelers: 1–20 adults and 0–20 children.'],
      [budgetAmount !== null && !(budgetAmount > 0), 'Budget must be a positive amount, or leave it empty.']],
      [[[selectedInterests, selectedAvoid].some((items) => items.length > 3 || items.some((item) => item.length > 80)),
        'Add up to 3 interests and 3 things to avoid, each under 80 characters.']],
    ];
    for (const [index, group] of checks.slice(0, upTo + 1).entries()) {
      const failed = group.find(([bad]) => bad);
      if (failed) return { step: index, message: failed[1] };
    }
    return null;
  };

  /** Next on a step checks that step and moves on; the last step, Skip and the one-page form send the brief. */
  const go = (finish: boolean) => {
    const found = problem(finish ? STEPS.length - 1 : step);
    setError(found?.message ?? '');
    if (found) { setStep(found.step); return; }
    if (!finish) { setStep(step + 1); return; }
    onSubmit({
      origin: origin.trim(),
      destination: destination.trim(),
      places_to_visit: requested,
      duration_days: duration,
      planning_preferences: {
        pace, interests: selectedInterests, avoid: selectedAvoid,
        start_date: startDate || null, adults: adultCount, children: childCount,
        comfort, travel_mode: travelMode, guide, home_currency: homeCurrency,
      },
      budget_amount: budgetAmount,
      currency,
    });
  };

  const sections = [
      <fieldset className="tv-onboarding__section" key="where">
        <legend>Where and when</legend>
        <div className="tv-onboarding__grid">
          <label>Travelling from <input className="tv-input" required maxLength={255} autoComplete="address-level2" placeholder="e.g. Kolkata" value={origin} onChange={(e) => setOrigin(e.target.value)} disabled={disabled} /></label>
          <label>Travelling to <input className="tv-input" required maxLength={255} placeholder="e.g. Japan" value={destination} onChange={(e) => setDestination(e.target.value)} disabled={disabled} /></label>
          <label>Places you want to visit <input className="tv-input" placeholder="e.g. Tokyo, Kyoto, Nara (optional)" value={places} onChange={(e) => setPlaces(e.target.value)} disabled={disabled} /><small>Separate places with commas.</small></label>
          <label>How many days? <input className="tv-input" required type="number" min="1" max="365" inputMode="numeric" placeholder="e.g. 10" value={days} onChange={(e) => setDays(e.target.value)} disabled={disabled} /></label>
          <label><span>Start date <span className="tv-onboarding__optional">(optional)</span></span>
            <input className="tv-input" type="date" min={today()} value={startDate} onChange={(e) => setStartDate(e.target.value)} disabled={disabled} />
            <small>{returning ? `Back on ${returning}.` : 'Unlocks weather, holidays and calendar export.'}</small>
          </label>
        </div>
      </fieldset>,

      <fieldset className="tv-onboarding__section" key="who">
        <legend>Who's coming and the budget</legend>
        <div className="tv-onboarding__grid tv-onboarding__grid--3">
          <label>Adults <input className="tv-input" type="number" min="1" max="20" inputMode="numeric" value={adults} onChange={(e) => setAdults(e.target.value)} disabled={disabled} /></label>
          <label>Children <input className="tv-input" type="number" min="0" max="20" inputMode="numeric" value={children} onChange={(e) => setChildren(e.target.value)} disabled={disabled} /></label>
          <label>Comfort
            <select className="tv-input" value={comfort} onChange={(e) => setComfort(e.target.value as Comfort)} disabled={disabled}>
              <option value="budget">Budget</option>
              <option value="mid_range">Mid-range</option>
              <option value="comfortable">Comfortable</option>
            </select>
            <small>{comfort === 'budget' ? 'Hostels, street food.' : comfort === 'comfortable' ? 'Nicer stays, sit-down meals.' : 'Good hotels, a mix of meals.'}</small>
          </label>
          <label><span>Total budget <span className="tv-onboarding__optional">(optional)</span></span>
            <input className="tv-input" type="number" min="1" step="1" inputMode="numeric" placeholder="e.g. 150000" value={budget} onChange={(e) => setBudget(e.target.value)} disabled={disabled} />
          </label>
          <label>Currency
            <select className="tv-input" value={currency} onChange={(e) => setCurrency(e.target.value as (typeof CURRENCIES)[number])} disabled={disabled}>
              {CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
            </select>
          </label>
          <label>Your home currency
            <select className="tv-input" value={homeCurrency} onChange={(e) => setHomeCurrency(e.target.value as (typeof CURRENCIES)[number])} disabled={disabled}>
              {CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
            </select>
            <small>For “≈” conversions of local prices.</small>
          </label>
        </div>
      </fieldset>,

      <fieldset className="tv-onboarding__section" key="how">
        <legend>How do you like to travel? <span>(optional)</span></legend>
        <div className="tv-onboarding__grid">
          <label>Daily pace
            <select className="tv-input" value={pace} onChange={(event) => setPace(event.target.value as Pace)} disabled={disabled}>
              <option value="relaxed">Relaxed, with room to wander</option>
              <option value="balanced">Balanced</option>
              <option value="packed">Full days, plenty to see</option>
            </select>
          </label>
          <label>Getting around
            <select className="tv-input" value={travelMode} onChange={(event) => setTravelMode(event.target.value as TravelMode)} disabled={disabled}>
              <option value="transit">Public transit</option>
              <option value="walk">Mostly walking</option>
              <option value="taxi">Taxis and rideshare</option>
              <option value="drive">Own or rental car</option>
            </select>
          </label>
          <label>What interests you?
            <input className="tv-input" placeholder="e.g. food, quiet streets, architecture" value={interests} onChange={(event) => setInterests(event.target.value)} disabled={disabled} />
            <small>Up to 3, separated by commas.</small>
          </label>
          <label>Anything to avoid?
            <input className="tv-input" placeholder="e.g. early starts, crowded attractions" value={avoid} onChange={(event) => setAvoid(event.target.value)} disabled={disabled} />
            <small>Up to 3, separated by commas.</small>
          </label>
        </div>
      </fieldset>,

      <fieldset className="tv-onboarding__section" key="guide">
        <legend>Pick your guide</legend>
        <div className="tv-onboarding__guides" role="radiogroup" aria-label="Guide character">
          {GUIDES.map((entry) => (
            <label key={entry.id} className={`tv-onboarding__guide ${guide === entry.id ? 'is-selected' : ''}`}>
              <input type="radio" name="guide" value={entry.id} checked={guide === entry.id}
                onChange={() => setGuide(entry.id)} disabled={disabled} />
              <img src={entry.image} alt="" loading="lazy" draggable={false} />
              <strong>{entry.name}</strong>
            </label>
          ))}
        </div>
        <p className="tv-onboarding__guide-line" aria-live="polite">
          {GUIDES.find((entry) => entry.id === guide)?.line}
        </p>
      </fieldset>,
  ];

  return (
    <form className={`tv-onboarding ${allAtOnce ? '' : 'tv-onboarding--steps'}`} aria-label="Your trip brief"
      onSubmit={(event) => { event.preventDefault(); go(allAtOnce || last); }}>
      {allAtOnce ? (
        <div className="tv-onboarding__heading">
          <h2>Your trip details</h2>
          <p>The route and days are all I need. Everything else helps me plan closer to how you travel.</p>
        </div>
      ) : (
        <div className="tv-onboarding__heading">
          <p className="tv-onboarding__progress">
            <span aria-hidden="true">{STEPS.map((entry, index) => <i key={entry.title} className={index <= step ? 'is-done' : ''} />)}</span>
            Step {step + 1} of {STEPS.length}
          </p>
          <h2 ref={heading} tabIndex={-1}>{STEPS[step].title}</h2>
          <p>{STEPS[step].note}</p>
        </div>
      )}

      {allAtOnce ? sections : sections[step]}

      {error && <p role="alert" className="tv-field__error">{error}</p>}
      {allAtOnce ? (
        <button type="submit" className="tv-btn tv-btn--primary" disabled={disabled}>Continue to planning options</button>
      ) : (
        <div className="tv-onboarding__nav">
          {step > 0 && <button type="button" className="tv-btn tv-btn--ghost" disabled={disabled} onClick={() => { setError(''); setStep(step - 1); }}>Back</button>}
          {/* Skip is not a submit button, so Enter in a field always means Next. */}
          {!last && <button type="button" className="tv-onboarding__skip" disabled={disabled} onClick={() => go(true)}>Skip the rest</button>}
          <button type="submit" className="tv-btn tv-btn--primary" disabled={disabled}>{last ? 'Continue to planning options' : 'Next'}</button>
        </div>
      )}
    </form>
  );
};
