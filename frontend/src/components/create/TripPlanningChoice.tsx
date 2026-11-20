import type { OnboardingValues } from './TripOnboardingForm';
import { guideById } from '../guide/guides';

interface Props {
  disabled?: boolean;
  onGenerateFull: () => void;
  onStartBuild?: () => void;
  onEditDetails?: () => void;
  brief?: Partial<OnboardingValues>;
}

function briefLine(brief: Partial<OnboardingValues>): string {
  const prefs = brief.planning_preferences;
  const start = prefs?.start_date
    ? new Date(`${prefs.start_date}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
    : null;
  const people = (prefs?.adults || 1) + (prefs?.children || 0);
  return [
    `${prefs?.pace || 'balanced'} pace`,
    start && `from ${start}`,
    `${people} traveler${people === 1 ? '' : 's'}`,
    prefs?.comfort && prefs.comfort.replace('_', '-'),
    prefs?.interests?.length ? `into ${prefs.interests.join(', ')}` : null,
    prefs?.avoid?.length ? `avoid ${prefs.avoid.join(', ')}` : null,
    brief.places_to_visit?.length ? `must see ${brief.places_to_visit.join(', ')}` : null,
  ].filter(Boolean).join(' · ');
}

export function TripPlanningChoice({ disabled, onGenerateFull, onStartBuild, onEditDetails, brief }: Props) {
  const guide = guideById(brief?.planning_preferences?.guide);
  return <section className="tv-planning-choice" aria-label="Choose how to build your itinerary">
    <div className="tv-planning-choice__intro">
      <span className="tv-label">NEXT STEP</span>
      <h2>How should we build your journey?</h2>
      <p>Your route details are saved. Choose a planning path to continue.</p>
    </div>
    {brief && <div className="tv-planning-choice__brief">
      <img className="tv-planning-choice__guide" src={guide.image} alt={`${guide.name}, your guide`} />
      <div>
        <span className="tv-label">YOUR BRIEF · WITH {guide.name.toUpperCase()}</span>
        <p>{brief.duration_days} days from {brief.origin} to {brief.destination}</p>
        <p className="tv-planning-choice__brief-detail">{briefLine(brief)}</p>
      </div>
      {onEditDetails && <button type="button" className="tv-planning-choice__edit" disabled={disabled} onClick={onEditDetails}>Edit details</button>}
    </div>}
    <div className="tv-planning-choice__options">
      <button type="button" disabled={disabled} onClick={onGenerateFull}>
        <span className="tv-label">01 / ONE SHOT</span>
        <strong>Generate full itinerary</strong>
        <small>Create the complete day-by-day draft, spatial graph, and map together.</small>
      </button>
      <button type="button" disabled={disabled || !onStartBuild} onClick={onStartBuild}>
        <span className="tv-label">02 / DAY BY DAY</span>
        <strong>Build with {guide.name}</strong>
        <small>Plan each day together. {guide.name} tracks your budget and preferences, and suggests places travelers love.</small>
      </button>
    </div>
  </section>;
}
