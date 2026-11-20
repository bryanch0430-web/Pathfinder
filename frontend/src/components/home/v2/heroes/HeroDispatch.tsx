/*
 * Hero 01 — "Dispatch"
 *
 * Document-first. An oversized editorial headline on a bone canvas, set
 * against a narrow mono data rail that reads like the index page of a printed
 * travel ledger. The only photograph is a small inset, so type carries the
 * whole first impression.
 */
import { img } from '../content';
import { ArrowUpRightIcon, ArrowDownIcon } from '../IconsV2';

export interface HeroProps {
  onStartPlanning?: () => void;
  onNavigateExplore?: () => void;
}

const LEDGER = [
  { k: 'Active trip', v: 'Japan · 14 nights' },
  { k: 'Graph', v: '23 nodes / 31 edges' },
  { k: 'Window', v: '02 Apr — 16 Apr' },
  { k: 'Ceiling', v: '¥420,000' },
  { k: 'Transit', v: 'Rail only' },
  { k: 'Status', v: 'Costing segments' },
];

export function HeroDispatch({ onStartPlanning, onNavigateExplore }: HeroProps) {
  return (
    <section className="tv-hero tv-hero--dispatch">
      <div className="tv-ambient" aria-hidden="true" />

      <div className="tv-container tv-hero__inner tv-collapse">
        <div className="tv-hero__lede">
          <span className="tv-eyebrow">Agentic travel planning</span>

          <h1 className="tv-display tv-d1 tv-hero__title">
            Every trip is a graph
            <br />
            before it is <em>an itinerary.</em>
          </h1>

          <p className="tv-lead tv-hero__lead">
            Describe where you want to go. Pathfinder resolves the cities, wires the routes,
            prices every segment, and hands you a trip you can walk through in three dimensions —
            reasoning out loud the entire way.
          </p>

          <div className="tv-hero__actions">
            <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
              <span>Start planning</span>
              <ArrowUpRightIcon width={15} height={15} />
            </button>
            <button type="button" className="tv-link" onClick={onNavigateExplore}>
              <span>Explore built trips</span>
            </button>
          </div>

          <div className="tv-hero__proof">
            <figure className="tv-hero__inset">
              <img src={img.kyotoGarden} alt="Kyoto temple garden" className="tv-img" />
            </figure>
            <p className="tv-meta tv-hero__proof-text">
              Last plan built: <strong>Kyoto → Kanazawa → Takayama → Tokyo</strong>, costed to the
              rail segment.
            </p>
          </div>
        </div>

        {/* Ledger rail — the trip as printed record */}
        <aside className="tv-ledger" aria-label="Sample trip record">
          <div className="tv-ledger__head">
            <span className="tv-label">Trip record</span>
            <span className="tv-tag tv-tag--green">Live</span>
          </div>

          <dl className="tv-ledger__list">
            {LEDGER.map((row) => (
              <div key={row.k} className="tv-ledger__row">
                <dt className="tv-meta">{row.k}</dt>
                <dd className="tv-ledger__v">{row.v}</dd>
              </div>
            ))}
          </dl>

          <div className="tv-ledger__foot">
            <ArrowDownIcon width={15} height={15} />
            <span className="tv-meta">Opens as a 3D universe</span>
          </div>
        </aside>
      </div>
    </section>
  );
}
