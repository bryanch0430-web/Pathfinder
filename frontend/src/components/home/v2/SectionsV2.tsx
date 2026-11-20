/*
 * SectionsV2 — the page body beneath the hero.
 *
 * Order: How it works → Capabilities (pinned horizontal) → Marquee
 *        → Agent preview → Destinations → Questions → Closing CTA.
 *
 * The capabilities section deliberately does NOT use a bento grid: the hero
 * now owns that language, and repeating it made the page read as two versions
 * of the same idea. It is a pinned horizontal sequence instead — the one place
 * on the page where scrolling changes direction.
 */
import { useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

import {
  AGENT_STEPS,
  AGENT_TRANSCRIPT,
  CAPABILITIES,
  DESTINATIONS,
  FAQ,
  ITINERARY_ROWS,
  MARQUEE,
  STEPS,
  STEP_IMAGES,
  img,
} from './content';
import {
  ArrowUpRightIcon,
  ClockIcon,
  GraphIcon,
  LayersIcon,
  MinusIcon,
  PinIcon,
  PlusIcon,
  SparkIcon,
  WalletIcon,
} from './IconsV2';
import {
  EASE,
  marquee,
  parallaxImage,
  prefersReducedMotion,
  revealHeadline,
  revealPanel,
  revealUp,
} from './motion';

const CAP_ICON = { graph: GraphIcon, spark: SparkIcon, layers: LayersIcon, wallet: WalletIcon };

/* ------------------------------------------------------------------ *
 * How it works — numbered editorial index with supporting thumbnails
 * ------------------------------------------------------------------ */

export function HowItWorks() {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const title = root.current?.querySelector<HTMLElement>('.tv-sec__title');
      const split = title ? revealHeadline(title, { trigger: root.current! }) : null;

      revealUp('.tv-sec__head .tv-label', { trigger: root.current!, start: 'top 85%' });

      // Each row wipes in on its own as it reaches the upper third.
      gsap.utils.toArray<HTMLElement>('.tv-steps__item').forEach((row) => {
        gsap.from(row, {
          y: 30,
          opacity: 0,
          duration: 1,
          ease: EASE,
          scrollTrigger: { trigger: row, start: 'top 88%' },
        });
      });

      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <section className="tv-section" id="how-it-works" ref={root}>
      <div className="tv-container">
        <header className="tv-sec__head">
          <span className="tv-label">How it works</span>
          <h2 className="tv-display tv-d2 tv-sec__title">
            Four steps from a sentence <em>to a trip you can walk through.</em>
          </h2>
        </header>

        <ol className="tv-steps">
          {STEPS.map((s, i) => (
            <li key={s.n} className="tv-steps__item">
              <span className="tv-steps__n tv-meta">{s.n}</span>

              <figure className="tv-steps__thumb">
                <img src={STEP_IMAGES[i]} alt="" aria-hidden="true" className="tv-img" loading="lazy" decoding="async" />
              </figure>

              <div className="tv-steps__body">
                <h3 className="tv-steps__title">{s.title}</h3>
                <p className="tv-body tv-steps__copy">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Capabilities — pinned horizontal sequence
 * ------------------------------------------------------------------ */

export function Capabilities() {
  const root = useRef<HTMLElement>(null);
  const track = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      // Below the tablet breakpoint the panels stack and scroll normally —
      // a pinned horizontal rail is hostile on a phone.
      const mm = gsap.matchMedia();

      mm.add('(min-width: 1024px) and (prefers-reduced-motion: no-preference)', () => {
        const el = track.current;
        const section = root.current;
        if (!el || !section) return;

        // Distance the track must travel to bring its last panel flush right.
        const distance = () => Math.max(0, el.scrollWidth - window.innerWidth);

        const tween = gsap.to(el, {
          x: () => -distance(),
          ease: 'none', // required: keeps scroll position and x in 1:1 sync
          scrollTrigger: {
            trigger: section,
            pin: true,
            scrub: 1,
            start: 'top top',
            end: () => `+=${distance() + window.innerHeight * 0.4}`,
            invalidateOnRefresh: true,
            anticipatePin: 1,
          },
        });

        // Per-panel reveals keyed to horizontal position, not page scroll.
        gsap.utils.toArray<HTMLElement>('.tv-cap__panel').forEach((panel) => {
          gsap.from(panel.querySelectorAll('.tv-cap__n, .tv-cap__title, .tv-cap__copy, .tv-cap__fig'), {
            y: 36,
            opacity: 0,
            duration: 0.9,
            ease: EASE,
            stagger: 0.08,
            scrollTrigger: {
              trigger: panel,
              containerAnimation: tween,
              start: 'left 78%',
              toggleActions: 'play none none reverse',
            },
          });
        });

        return () => tween.scrollTrigger?.kill();
      });

      // Stacked fallback: ordinary vertical reveals.
      mm.add('(max-width: 1023px)', () => {
        revealPanel('.tv-cap__panel', { trigger: root.current!, start: 'top 85%', stagger: 0.12 });
      });

      return () => mm.revert();
    },
    { scope: root },
  );

  return (
    <section className="tv-cap" id="capabilities" ref={root}>
      <div className="tv-cap__viewport">
        <div className="tv-cap__track" ref={track}>
          {/* Lead panel doubles as the section header */}
          <div className="tv-cap__panel tv-cap__panel--intro">
            <span className="tv-label">What the agent actually does</span>
            <h2 className="tv-display tv-d2 tv-cap__intro-title">
              Most planners write you a list.
              <br />
              <em>This one keeps a plan you can change.</em>
            </h2>
            <p className="tv-lead">
              Four things follow from that difference. Keep scrolling: this section moves sideways.
            </p>
            <span className="tv-cap__hint tv-meta">
              <ArrowUpRightIcon width={14} height={14} />
              Scroll
            </span>
          </div>

          {CAPABILITIES.map((c) => {
            const Icon = CAP_ICON[c.icon];
            return (
              <article key={c.n} className="tv-cap__panel">
                <figure className="tv-figure tv-cap__fig">
                  <img src={c.image} alt={c.alt} className="tv-img tv-img--drift" loading="lazy" />
                  <figcaption className="tv-cap__cap">
                    <span className="tv-tag">{c.caption}</span>
                  </figcaption>
                </figure>

                <div className="tv-cap__body">
                  <span className="tv-cap__n tv-meta">
                    <Icon width={16} height={16} />
                    {c.n}
                  </span>
                  <h3 className="tv-display tv-cap__title">{c.title}</h3>
                  <p className="tv-body tv-cap__copy">{c.body}</p>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Marquee — an infinite band of the places people have planned
 * ------------------------------------------------------------------ */

export function Marquee() {
  const root = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const track = root.current?.querySelector<HTMLElement>('.tv-marquee__track');
      if (track) marquee(track, 52);
    },
    { scope: root },
  );

  // The list is rendered twice so the -50% loop is seamless.
  const items = [...MARQUEE, ...MARQUEE];

  return (
    <div className="tv-marquee" ref={root} aria-hidden="true">
      <div className="tv-marquee__track">
        {items.map((m, i) => (
          <figure className="tv-marquee__item" key={`${m.label}-${i}`}>
            <img src={m.src} alt="" className="tv-img" loading="lazy" />
            <figcaption className="tv-meta">{m.label}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Agent preview
 * ------------------------------------------------------------------ */

export function AgentPreview() {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const title = root.current?.querySelector<HTMLElement>('.tv-agent__title');
      const split = title ? revealHeadline(title, { trigger: root.current! }) : null;

      revealUp('.tv-agent__lead, .tv-agent__stats', { trigger: root.current!, start: 'top 75%' });
      revealPanel('.tv-agent__window', { trigger: root.current!, start: 'top 78%' });

      gsap.from('.tv-agent__window .tv-trace__row', {
        x: -16,
        opacity: 0,
        duration: 0.7,
        ease: EASE,
        stagger: 0.08,
        scrollTrigger: { trigger: root.current!, start: 'top 60%' },
      });

      gsap.from('.tv-agent__window tbody tr', {
        y: 14,
        opacity: 0,
        duration: 0.7,
        ease: EASE,
        stagger: 0.07,
        scrollTrigger: { trigger: root.current!, start: 'top 52%' },
      });

      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <section className="tv-section" id="agent" ref={root}>
      <div className="tv-container">
        <div className="tv-agent tv-collapse">
          <div className="tv-agent__copy">
            <span className="tv-label">While it plans</span>
            <h2 className="tv-display tv-d3 tv-agent__title">
              You watch the plan arrive, <em>not a spinner.</em>
            </h2>
            <p className="tv-lead tv-agent__lead">
              Your guide says what it is doing as it goes: researching stops, reading what travelers
              recommend, checking the weather and public holidays for your dates, then writing the days.
              The draft streams in, and the route draws itself alongside.
            </p>

            <dl className="tv-agent__stats">
              <div>
                <dt className="tv-label">Weather</dt>
                <dd className="tv-meta">A forecast near your dates, the month’s typical beyond</dd>
              </div>
              <div>
                <dt className="tv-label">Traveler tips</dt>
                <dd className="tv-meta">Linked to the post they came from</dd>
              </div>
            </dl>
          </div>

          <div className="tv-window tv-agent__window">
            <div className="tv-window__bar">
              <div className="tv-window__dots">
                <span className="tv-window__dot" />
                <span className="tv-window__dot" />
                <span className="tv-window__dot" />
              </div>
              <span className="tv-meta tv-window__title">pathfinder · kyoto, 3 days</span>
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
                    <th className="tv-label">Day</th>
                    <th className="tv-label">Planned</th>
                    <th className="tv-label tv-hide-mobile">Date</th>
                    <th className="tv-label tv-table__num">Places</th>
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
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Destinations rail
 * ------------------------------------------------------------------ */

export function Destinations({ onNavigateExplore }: { onNavigateExplore?: () => void }) {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const title = root.current?.querySelector<HTMLElement>('.tv-sec__title');
      const split = title ? revealHeadline(title, { trigger: root.current! }) : null;

      revealPanel('.tv-rail__card', { trigger: root.current!, start: 'top 82%', stagger: 0.08 });

      gsap.utils.toArray<HTMLElement>('.tv-rail__figure img').forEach((el) => {
        parallaxImage(el, { trigger: el.closest('.tv-rail__card') ?? el, amount: 7 });
      });

      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <section className="tv-section tv-section--sunk" id="destinations" ref={root}>
      <div className="tv-container">
        <header className="tv-sec__head tv-sec__head--row">
          <div>
            <span className="tv-label">Places to start</span>
            <h2 className="tv-display tv-d2 tv-sec__title">
              Six places worth <em>a first draft.</em>
            </h2>
          </div>
          <button type="button" className="tv-link tv-hide-mobile" onClick={onNavigateExplore}>
            <span>Explore destinations</span>
            <ArrowUpRightIcon width={15} height={15} />
          </button>
        </header>
      </div>

      {/* Focusable so keyboard users can scroll the rail with the arrow keys (the cards have no controls). */}
      <div className="tv-rail" role="list" tabIndex={0} aria-label="Destinations, scroll sideways">
        {DESTINATIONS.map((d, i) => (
          <article key={d.code} role="listitem" className={`tv-rail__card ${i % 3 === 1 ? 'tv-rail__card--tall' : ''}`}>
            <figure className="tv-figure tv-rail__figure">
              <img src={d.image} alt={`${d.city}, ${d.country}`} className="tv-img tv-img--drift" loading="lazy" />
            </figure>
            <div className="tv-rail__meta">
              <div className="tv-rail__line">
                <h3 className="tv-rail__city">{d.city}</h3>
                <span className="tv-meta">{d.code}</span>
              </div>
              <p className="tv-meta tv-rail__note">{d.note}</p>
              <div className="tv-rail__foot">
                <span className="tv-tag">
                  <ClockIcon width={12} height={12} />
                  {d.nights} nights
                </span>
                <span className="tv-meta">{d.country}</span>
              </div>
            </div>
          </article>
        ))}
      </div>

      <div className="tv-container tv-only-mobile" style={{ marginTop: '2rem' }}>
        <button type="button" className="tv-btn tv-btn--ghost" onClick={onNavigateExplore} style={{ width: '100%' }}>
          Explore all trips
        </button>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Questions
 * ------------------------------------------------------------------ */

export function Questions() {
  const [open, setOpen] = useState<number | null>(0);
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const title = root.current?.querySelector<HTMLElement>('.tv-sec__title');
      const split = title ? revealHeadline(title, { trigger: root.current! }) : null;
      revealUp('.tv-faq__item', { trigger: root.current!, start: 'top 80%', stagger: 0.06, y: 18 });
      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <section className="tv-section" id="questions" ref={root}>
      <div className="tv-container tv-container--narrow">
        <header className="tv-sec__head">
          <span className="tv-label">Questions</span>
          <h2 className="tv-display tv-d2 tv-sec__title">Before you start.</h2>
        </header>

        <div className="tv-faq">
          {FAQ.map((item, i) => {
            const isOpen = open === i;
            return (
              <div key={item.q} className="tv-faq__item">
                <button
                  type="button"
                  className="tv-faq__q"
                  aria-expanded={isOpen}
                  onClick={() => {
                    setOpen(isOpen ? null : i);
                    // Panel height changes shift everything below it.
                    requestAnimationFrame(() => ScrollTrigger.refresh());
                  }}
                >
                  <span>{item.q}</span>
                  {isOpen ? <MinusIcon width={18} height={18} /> : <PlusIcon width={18} height={18} />}
                </button>
                {isOpen && <p className="tv-body tv-faq__a">{item.a}</p>}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Closing CTA
 * ------------------------------------------------------------------ */

export function CtaBand({ onStartPlanning }: { onStartPlanning?: () => void }) {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const title = root.current?.querySelector<HTMLElement>('.tv-cta__title');
      const split = title ? revealHeadline(title, { trigger: root.current!, start: 'top 80%' }) : null;

      revealUp('.tv-cta__inner > .tv-label, .tv-cta__lead, .tv-cta__inner > .tv-btn, .tv-cta__note', {
        trigger: root.current!,
        start: 'top 72%',
      });

      const bg = root.current?.querySelector<HTMLElement>('.tv-cta__bg img');
      if (bg) parallaxImage(bg, { trigger: root.current!, amount: 14 });

      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <section className="tv-cta" id="plan" ref={root}>
      <figure className="tv-cta__bg">
        <img src={img.monoCity} alt="" aria-hidden="true" className="tv-img tv-img--mono tv-img--drift" loading="lazy" />
      </figure>

      <div className="tv-container tv-cta__inner tv-invert">
        <span className="tv-label">Ready when you are</span>
        <h2 className="tv-display tv-d2 tv-cta__title">
          Describe the trip. <em>Watch it get built.</em>
        </h2>
        <p className="tv-lead tv-cta__lead">
          Three quick questions, then a day-by-day first draft: all at once,
          or one day at a time with your guide.
        </p>
        <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
          <span>Start planning</span>
          <ArrowUpRightIcon width={15} height={15} />
        </button>
        <p className="tv-meta tv-cta__note">
          <PinIcon width={13} height={13} /> Guest planning available. No account required to try it.
        </p>
      </div>
    </section>
  );
}
