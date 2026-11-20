/*
 * Hero 03 — "Console"
 *
 * Split screen. The pitch holds the left column; the right column is a
 * faux-OS window showing the agent mid-reason — transcript, live trace, and a
 * costed itinerary table. This is the variant that argues the product rather
 * than the destination.
 */
import { AGENT_STEPS, AGENT_TRANSCRIPT, ITINERARY_ROWS } from '../content';
import { ArrowUpRightIcon, GraphIcon } from '../IconsV2';
import type { HeroProps } from './HeroDispatch';

export function HeroConsole({ onStartPlanning, onNavigateExplore }: HeroProps) {
  return (
    <section className="tv-hero tv-hero--console">
      <div className="tv-graph" aria-hidden="true" />

      <div className="tv-container tv-container--wide tv-console tv-collapse">
        <div className="tv-console__lede">
          <span className="tv-tag tv-tag--blue">
            <GraphIcon width={12} height={12} />
            Agent v3 · streaming
          </span>

          <h1 className="tv-display tv-d1 tv-console__title">
            Watch it plan
            <br />
            <em>your trip, live.</em>
          </h1>

          <p className="tv-lead tv-console__lead">
            Give Pathfinder one sentence about the journey you want. It parses your constraints,
            scores candidate cities, wires the route graph and prices every leg — writing out
            each decision as it makes it.
          </p>

          <div className="tv-console__actions">
            <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
              <span>Start planning</span>
              <ArrowUpRightIcon width={15} height={15} />
            </button>
            <button type="button" className="tv-link" onClick={onNavigateExplore}>
              <span>Explore built trips</span>
            </button>
          </div>

          <ul className="tv-console__points">
            <li><span className="tv-kbd">01</span> Constraints you write, not fields you fill</li>
            <li><span className="tv-kbd">02</span> Replans only the branch you changed</li>
            <li><span className="tv-kbd">03</span> Opens as a navigable 3D graph</li>
          </ul>
        </div>

        <div className="tv-window tv-console__window">
          <div className="tv-window__bar">
            <div className="tv-window__dots">
              <span className="tv-window__dot" />
              <span className="tv-window__dot" />
              <span className="tv-window__dot" />
            </div>
            <span className="tv-meta tv-window__title">pathfinder — japan, 14 nights</span>
          </div>

          <div className="tv-window__body">
            {AGENT_TRANSCRIPT.map((m, i) => (
              <div key={i} className={`tv-msg tv-msg--${m.role}`}>
                <span className="tv-label tv-msg__who">{m.role === 'user' ? 'You' : 'Pathfinder'}</span>
                <p className="tv-msg__text">{m.text}</p>
              </div>
            ))}

            <ul className="tv-trace">
              {AGENT_STEPS.map((s) => (
                <li key={s.label} className={`tv-trace__row is-${s.state}`}>
                  <span className="tv-trace__dot" aria-hidden="true" />
                  <span className="tv-trace__label">{s.label}</span>
                  <span className="tv-meta tv-trace__detail">{s.detail}</span>
                </li>
              ))}
            </ul>

            <table className="tv-table">
              <thead>
                <tr>
                  <th className="tv-label">Days</th>
                  <th className="tv-label">Place</th>
                  <th className="tv-label tv-hide-mobile">Transit</th>
                  <th className="tv-label tv-table__num">Segment</th>
                </tr>
              </thead>
              <tbody>
                {ITINERARY_ROWS.map((r) => (
                  <tr key={r.day}>
                    <td className="tv-meta">{r.day}</td>
                    <td className="tv-table__place">{r.place}</td>
                    <td className="tv-meta tv-hide-mobile">{r.mode}</td>
                    <td className="tv-meta tv-table__num">{r.cost}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
