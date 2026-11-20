/*
 * TripPreviewCard — the chat's doorway into the Trip Studio.
 *
 * It closes the conversation with a live summary of the plan (route photos,
 * days, dates) and morphs into the studio canvas: both carry
 * data-flip-id="trip-stage", which GSAP Flip animates between.
 */
import { guideById } from '../guide/guides';
import type { ItineraryGraph } from './itineraryGraph';
import { TripPhotoStrip } from './TripPhotoStrip';

interface Props {
  tripId: string;
  destination?: string | null;
  days?: number | null;
  startDate?: string | null;
  guideId?: string | null;
  graph: ItineraryGraph;
  onOpen: (day?: number | null) => void;
}

const shortDate = (iso: string, offset = 0) => {
  const date = new Date(`${iso}T00:00:00`);
  date.setDate(date.getDate() + offset);
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
};

export function TripPreviewCard({ tripId, destination, days, startDate, guideId, graph, onOpen }: Props) {
  const guide = guideById(guideId);
  const stops = graph.nodes.filter((node) => node.kind !== 'origin');
  const dates = startDate && days ? `${shortDate(startDate)} – ${shortDate(startDate, days - 1)}` : null;
  const openStop = (nodeId: string) => onOpen(graph.nodes.find((node) => node.id === nodeId)?.day_start ?? null);

  return (
    <section className="tv-trip-card" data-flip-id="trip-stage" aria-label="Your trip">
      <div className="tv-trip-card__head">
        <img src={guide.image} alt="" className="tv-trip-card__guide" />
        <div>
          <span className="tv-label">YOUR TRIP{days ? ` · ${days} DAYS` : ''}{dates ? ` · ${dates.toUpperCase()}` : ''}</span>
          <h3>{destination || 'Your route'}</h3>
          <p>{stops.map((node) => node.name).join(' → ') || 'Route ready'}</p>
        </div>
      </div>
      <TripPhotoStrip graph={graph} destination={destination ?? undefined} tripId={tripId} onSelectStop={openStop} />
      <div className="tv-trip-card__actions">
        <span>Plan, map and 3D view are in the studio. To change anything, just tell {guide.name}: it saves as you go.</span>
        <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" onClick={() => onOpen(null)}>
          Open studio <span aria-hidden="true">→</span>
        </button>
      </div>
    </section>
  );
}
