/*
 * Hero 05 — "Horizon"
 *
 * The quietest of the five. Enormous macro-whitespace, an offset (never
 * centred) serif headline, and small photographs set inline between the words
 * at type height — the photos act as punctuation rather than illustration.
 * A single wide band of imagery resolves the fold far below.
 */
import { img } from '../content';
import { ArrowUpRightIcon } from '../IconsV2';
import type { HeroProps } from './HeroDispatch';

export function HeroHorizon({ onStartPlanning, onNavigateExplore }: HeroProps) {
  return (
    <section className="tv-hero tv-hero--horizon">
      <div className="tv-ambient" aria-hidden="true" />

      <div className="tv-container tv-horizon__inner">
        <span className="tv-eyebrow tv-horizon__eyebrow">Agentic travel planning</span>

{/*
 * Explicit {' '} around each inline photo: the images drop out at phone width,
 * and without them the surrounding words would run together.
 */}
        <h1 className="tv-display tv-horizon__title">
          Somewhere{' '}
          <img src={img.kyotoGarden} alt="" aria-hidden="true" className="tv-inline-img" />{' '}
          between <em>the idea</em>{' '}
          <img src={img.porto} alt="" aria-hidden="true" className="tv-inline-img" />{' '}
          of a trip
          <br />
          and the{' '}
          <img src={img.alpineLake} alt="" aria-hidden="true" className="tv-inline-img" />{' '}
          <em>morning you leave</em>{' '}
          <img src={img.tokyoAlley} alt="" aria-hidden="true" className="tv-inline-img" />{' '}
          — there is
          <br />
          all of this <em>to work out.</em>
        </h1>

        <div className="tv-horizon__foot">
          <p className="tv-lead tv-horizon__lead">
            Pathfinder works it out with you. Describe the journey once and the agent resolves
            the cities, wires the routes, holds your budget, and opens the whole thing as a
            world you can move through.
          </p>

          <div className="tv-horizon__actions">
            <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
              <span>Start planning</span>
              <ArrowUpRightIcon width={15} height={15} />
            </button>
            <button type="button" className="tv-link" onClick={onNavigateExplore}>
              <span>Explore built trips</span>
            </button>
          </div>
        </div>
      </div>

      {/* Wide resolving band */}
      <figure className="tv-horizon__band">
        <img src={img.fuji} alt="Mount Fuji at first light" className="tv-img tv-img--soft" />
      </figure>

      <div className="tv-container tv-horizon__caption">
        <span className="tv-meta">Kyoto → Kanazawa → Takayama → Tokyo</span>
        <span className="tv-meta tv-hide-mobile">14 nights · rail only · costed to the segment</span>
      </div>
    </section>
  );
}
