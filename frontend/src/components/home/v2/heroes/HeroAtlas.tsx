/*
 * Hero 02 — "Atlas"
 *
 * Full-bleed desaturated photography under a warm wash, with the type set
 * low-left so the image carries the upper two thirds. A filmstrip of planned
 * destinations runs along the bottom edge as a horizontal index.
 *
 * Runs with an inverted, transparent navbar (`tone="over"`).
 */
import { DESTINATIONS, img } from '../content';
import { ArrowUpRightIcon, PlayIcon } from '../IconsV2';
import type { HeroProps } from './HeroDispatch';

export function HeroAtlas({ onStartPlanning, onNavigateExplore }: HeroProps) {
  return (
    <section className="tv-hero tv-hero--atlas tv-invert">
      <figure className="tv-atlas__bg">
        <img src={img.fuji} alt="Mount Fuji at first light" className="tv-img tv-img--soft" />
        <span className="tv-atlas__wash" aria-hidden="true" />
      </figure>

      <div className="tv-atlas__grid tv-container tv-container--wide">
        <div className="tv-atlas__lede">
          <span className="tv-eyebrow">Fujiyoshida, Yamanashi · 35.36°N 138.72°E</span>

          <h1 className="tv-display tv-d1 tv-atlas__title">
            Go further than
            <br />
            <em>a list of places.</em>
          </h1>

          <p className="tv-lead tv-atlas__lead">
            Pathfinder builds your journey as a connected world — cities, stays, meals and
            transit wired into one graph you can fly through, question, and rebuild on the spot.
          </p>

          <div className="tv-atlas__actions">
            <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
              <span>Start planning</span>
              <ArrowUpRightIcon width={15} height={15} />
            </button>
            <button type="button" className="tv-atlas__play" onClick={onNavigateExplore}>
              <PlayIcon width={30} height={30} />
              <span>See a finished trip</span>
            </button>
          </div>
        </div>

        {/* Filmstrip index along the base of the frame */}
        <div className="tv-strip" role="list" aria-label="Recently planned destinations">
          {DESTINATIONS.slice(0, 4).map((d) => (
            <article key={d.code} role="listitem" className="tv-strip__item">
              <figure className="tv-strip__fig">
                <img src={d.image} alt={`${d.city}, ${d.country}`} className="tv-img" loading="lazy" />
              </figure>
              <div className="tv-strip__meta">
                <span className="tv-strip__city">{d.city}</span>
                <span className="tv-meta">{d.nights}N · {d.code}</span>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
