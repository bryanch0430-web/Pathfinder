/*
 * CurrentTrip — the active trip as a compact ledger card.
 * The previous emoji origin marker is replaced with the system's plane glyph.
 */
import React from 'react';
import { ClockIcon, LayersIcon, PinIcon, PlaneIcon, WalletIcon } from '../home/v2/IconsV2';

export interface CurrentTripContext {
  title?: string;
  destination?: string;
  origin?: string;
  days?: number;
  startDate?: string;
  endDate?: string;
  budget?: number;
  currency?: string;
  travelers?: number;
  status?: 'DISCOVERY' | 'ONBOARDING' | 'CHOOSING' | 'ROUTING' | 'ITINERARY' | 'CONFIRMED' | 'FINAL';
  interests?: string[];
}

interface CurrentTripProps {
  trip?: CurrentTripContext | null;
  onExploreSpatial?: () => void;
  className?: string;
}

const STATUS_TONE: Record<NonNullable<CurrentTripContext['status']>, string> = {
  DISCOVERY: '',
  ONBOARDING: 'tv-tag--blue',
  CHOOSING: 'tv-tag--blue',
  ROUTING: 'tv-tag--yellow',
  ITINERARY: 'tv-tag--yellow',
  CONFIRMED: 'tv-tag--green',
  FINAL: 'tv-tag--green',
};

export const CurrentTrip: React.FC<CurrentTripProps> = ({ trip, onExploreSpatial, className = '' }) => {
  if (!trip || (!trip.destination && !trip.title)) return null;

  const budget =
    trip.budget && trip.budget > 0
      ? new Intl.NumberFormat('en-US', {
          style: 'currency',
          currency: trip.currency || 'USD',
          maximumFractionDigits: 0,
        }).format(trip.budget)
      : null;

  const rows = [
    trip.destination && trip.title !== trip.destination && { Icon: PinIcon, k: 'Destination', v: trip.destination },
    trip.origin && { Icon: PlaneIcon, k: 'From', v: trip.origin },
    trip.days && { Icon: ClockIcon, k: 'Length', v: `${trip.days} days` },
    budget && { Icon: WalletIcon, k: 'Budget', v: budget },
    trip.travelers && { Icon: PinIcon, k: 'Travellers', v: String(trip.travelers) },
  ].filter(Boolean) as Array<{ Icon: typeof PinIcon; k: string; v: string }>;

  return (
    <section className={`tv-trip ${className}`}>
      <div className="tv-trip__head">
        <span className="tv-label">Active trip</span>
        {trip.status && <span className={`tv-tag ${STATUS_TONE[trip.status]}`}>{trip.status.toLowerCase()}</span>}
      </div>

      <h3 className="tv-display tv-trip__title">{trip.title || trip.destination}</h3>

      {rows.length > 0 && (
        <dl className="tv-trip__rows">
          {rows.map(({ Icon, k, v }) => (
            <div key={k} className="tv-trip__row">
              <dt className="tv-meta">
                <Icon width={12} height={12} /> {k}
              </dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}

      {trip.interests && trip.interests.length > 0 && (
        <div className="tv-trip__tags">
          {trip.interests.slice(0, 4).map((i) => (
            <span key={i} className="tv-tag">
              {i}
            </span>
          ))}
        </div>
      )}

      {/* Only once there is a plan to open, and under the name every other entry point uses. */}
      {onExploreSpatial && (trip.status === 'ROUTING' || trip.status === 'CONFIRMED' || trip.status === 'FINAL') && (
        <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" style={{ width: '100%' }} onClick={onExploreSpatial}>
          <LayersIcon width={14} height={14} />
          <span>Open studio</span>
        </button>
      )}
    </section>
  );
};
