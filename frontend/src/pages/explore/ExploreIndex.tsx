/*
 * Explore — the index.
 *
 * Opens on Fuji, then six moods run sideways in a pinned chapter (from the
 * moodboard design), then the list: one line per place, set like a departures board.
 * Photographs stay out of the way until asked for. On a mouse, the picture for
 * the line you point at follows the cursor; on touch, each line carries a small
 * thumbnail. Then one chapter pins: pick the month first, and the photograph
 * for each season wipes up as you read it.
 *
 * Motion: one authored moment per chapter, all on transform / opacity /
 * clip-path, all skipped under reduced motion (content is visible by default).
 */
import { useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Flip } from 'gsap/Flip';

import { ArrowUpRightIcon } from '../../components/home/v2/IconsV2';
import { EASE, prefersReducedMotion, splitLines } from '../../components/home/v2/motion';
import { KINDS, MOODS, PLACES, moodImage, placeById, placeImage, planFrom, type Kind } from './places';

gsap.registerPlugin(useGSAP, ScrollTrigger, Flip);

const SEASONS = [
  {
    id: 'spring', name: 'Spring', turn: 'blossom and gardens.', months: 'March to May', image: 'fuji-blossom',
    caption: 'Fujiyoshida in blossom',
    line: "Cherry blossom in Japan lasts about ten days, and Seoul's palace gardens come into leaf.",
    places: ['fuji-blossom', 'kyoto-garden', 'seoul-palace'],
  },
  {
    id: 'summer', name: 'Summer', turn: 'lakes and long evenings.', months: 'June to August', image: 'alpine-lake',
    caption: 'An alpine lake village',
    line: 'The lakes are warm enough to swim, and evenings in Porto and Prague run late.',
    places: ['alpine-lake', 'porto-douro', 'prague-rooftops'],
  },
  {
    id: 'autumn', name: 'Autumn', turn: 'maples and clear air.', months: 'September to November', image: 'budapest-aerial',
    caption: 'Buda before dusk',
    line: "Kyoto's maples turn in November, central Europe goes quiet after summer, and it's spring in Sydney.",
    places: ['kyoto-garden', 'budapest-aerial', 'sydney-harbour'],
  },
  {
    id: 'winter', name: 'Winter', turn: 'warm cities, clear peaks.', months: 'December to February', image: 'pagoda-fuji',
    caption: 'The pagoda and the mountain',
    line: 'Dubai is at its most comfortable, and Fuji is clearest in the cold, dry months.',
    places: ['dubai-marina', 'pagoda-fuji', 'izakaya-lane'],
  },
];

type Filter = Kind | 'All';

export function ExploreIndex({ onStartPlanning }: { onStartPlanning: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  const preview = useRef<HTMLDivElement>(null);
  const flip = useRef<Flip.FlipState | null>(null);
  const [filter, setFilter] = useState<Filter>('All');
  const [active, setActive] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);

  const visible = useMemo(() => (filter === 'All' ? PLACES : PLACES.filter((p) => p.kind === filter)), [filter]);
  const plan = (id: string) => planFrom(placeById(id), onStartPlanning);

  // Leaving rows fade first; the rest then slide into their new places (Flip, below).
  const choose = (next: Filter) => {
    if (next === filter) return;
    const rows = gsap.utils.toArray<HTMLElement>('.xi-row', root.current);
    if (prefersReducedMotion()) return setFilter(next);
    const leaving = rows.filter((row) => next !== 'All' && row.dataset.kind !== next);
    const settle = () => {
      flip.current = Flip.getState(rows.filter((row) => !leaving.includes(row)));
      setFilter(next);
    };
    if (!leaving.length) return settle();
    gsap.to(leaving, { opacity: 0, y: -8, duration: 0.22, ease: 'power2.in', onComplete: settle });
  };

  useLayoutEffect(() => {
    const state = flip.current;
    flip.current = null;
    if (!state) return;
    Flip.from(state, {
      targets: gsap.utils.toArray('.xi-row', root.current),
      duration: 0.8, ease: EASE,
      onEnter: (els) => gsap.fromTo(els, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.7, ease: EASE, stagger: 0.035 }),
    });
  }, [filter]);

  // The cursor preview: follows with a little lag, and leans into fast sideways moves.
  const follow = useRef<{ x: (v: number) => void; y: (v: number) => void; r: (v: number) => void; last: number } | null>(null);
  useGSAP(() => {
    if (!preview.current) return;
    follow.current = {
      x: gsap.quickTo(preview.current, 'x', { duration: 0.55, ease: 'power3' }),
      y: gsap.quickTo(preview.current, 'y', { duration: 0.55, ease: 'power3' }),
      r: gsap.quickTo(preview.current, 'rotation', { duration: 0.8, ease: 'power3' }),
      last: 0,
    };
  }, { scope: root });

  const onMove = (event: MouseEvent) => {
    const f = follow.current;
    if (!f) return;
    f.x(event.clientX + 28);
    f.y(event.clientY - 150);
    f.r(gsap.utils.clamp(-7, 7, (event.clientX - f.last) * 0.35));
    f.last = event.clientX;
  };

  useGSAP(() => {
    const el = preview.current;
    if (!el) return;
    gsap.to(el, { autoAlpha: active ? 1 : 0, scale: active ? 1 : 0.86, duration: 0.45, ease: EASE });
    if (active) {
      const shots = gsap.utils.toArray<HTMLElement>('.xi-preview__img', el);
      shots.forEach((img) => {
        const on = img.dataset.id === active;
        gsap.to(img, {
          clipPath: on ? 'inset(0% 0% 0% 0%)' : 'inset(0% 0% 100% 0%)',
          scale: on ? 1 : 1.12, duration: on ? 0.7 : 0.4, ease: EASE, zIndex: on ? 2 : 1,
        });
      });
    }
  }, { scope: root, dependencies: [active] });

  // Entrances and the scroll-driven chapters.
  useGSAP(() => {
    if (prefersReducedMotion()) return;
    const q = gsap.utils.selector(root);

    const title = q('.xi-hero__title')[0] as HTMLElement | undefined;
    const split = title ? splitLines(title) : null;
    if (split) gsap.from(split.lines, { yPercent: 115, duration: 1.2, ease: EASE, stagger: 0.09, delay: 0.1 });
    gsap.from(q('.xi-hero__side > *'), { y: 18, opacity: 0, duration: 1, ease: EASE, stagger: 0.08, delay: 0.35 });

    // The window opens from a letterbox as it rises into view.
    gsap.fromTo(q('.xi-window__fig'),
      { clipPath: 'inset(14% 18% 14% 18% round 12px)' },
      { clipPath: 'inset(0% 0% 0% 0% round 12px)', ease: 'none',
        scrollTrigger: { trigger: q('.xi-window')[0], start: 'top 85%', end: 'center 45%', scrub: true } });
    gsap.fromTo(q('.xi-window__fig img'), { scale: 1.22 }, {
      scale: 1, ease: 'none',
      scrollTrigger: { trigger: q('.xi-window')[0], start: 'top bottom', end: 'bottom top', scrub: true },
    });

    const mm = gsap.matchMedia();

    // Moods: pinned, running sideways. The horizontal tween must stay linear; reveals key off it.
    mm.add('(min-width: 900px)', () => {
      const track = q('.xm-track')[0] as HTMLElement;
      const distance = () => track.scrollWidth - window.innerWidth;
      const run = gsap.to(track, {
        x: () => -distance(), ease: 'none',
        scrollTrigger: {
          trigger: q('.xm-moods__pin')[0], pin: true, start: 'top 60px', end: () => `+=${distance()}`,
          scrub: 0.6, invalidateOnRefresh: true, anticipatePin: 1,
          onUpdate: (self) => {
            const index = Math.min(MOODS.length - 1, Math.round(self.progress * (MOODS.length - 1)));
            setCurrent((prev) => (prev === index ? prev : index));
          },
        },
      });
      gsap.to(q('.xm-progress__bar'), {
        scaleX: 1, ease: 'none',
        scrollTrigger: { trigger: q('.xm-moods__pin')[0], start: 'top 60px', end: () => `+=${distance()}`, scrub: true },
      });
      q('.xm-panel').forEach((panel) => {
        gsap.fromTo(panel.querySelector('.xm-panel__main img'), { xPercent: -6 }, {
          xPercent: 6, ease: 'none',
          scrollTrigger: { trigger: panel, containerAnimation: run, start: 'left right', end: 'right left', scrub: true },
        });
        gsap.from(panel.querySelector('.xm-panel__inset'), {
          yPercent: 30, rotation: 6, opacity: 0, duration: 1.1, ease: EASE,
          scrollTrigger: { trigger: panel, containerAnimation: run, start: 'left 70%' },
        });
        gsap.from(panel.querySelectorAll('.xm-panel__copy > *'), {
          y: 24, opacity: 0, duration: 1, ease: EASE, stagger: 0.07,
          scrollTrigger: { trigger: panel, containerAnimation: run, start: 'left 60%' },
        });
      });
    });

    // Index lines: the hairline draws, then the line lifts.
    ScrollTrigger.batch(q('.xi-row'), {
      start: 'top 92%', once: true,
      onEnter: (rows) => gsap.from(rows, { opacity: 0, y: 22, duration: 0.9, ease: EASE, stagger: 0.05 }),
    });
    gsap.from(q('.xi-index__rule'), {
      scaleX: 0, transformOrigin: 'left center', duration: 1.4, ease: EASE,
      scrollTrigger: { trigger: q('.xi-index')[0], start: 'top 80%' },
    });

    // Seasons: each photograph wipes up over the last while its copy passes the middle.
    mm.add('(min-width: 900px)', () => {
      q('.xi-season').forEach((season, i) => {
        if (i === 0) return;
        const fig = q(`.xi-stage__fig[data-i="${i}"]`)[0];
        gsap.fromTo(fig, { clipPath: 'inset(100% 0% 0% 0%)' }, {
          clipPath: 'inset(0% 0% 0% 0%)', ease: 'none',
          scrollTrigger: { trigger: season, start: 'top 88%', end: 'top 40%', scrub: true },
        });
        gsap.fromTo(fig.querySelector('img'), { scale: 1.25, yPercent: 6 }, {
          scale: 1, yPercent: 0, ease: 'none',
          scrollTrigger: { trigger: season, start: 'top 88%', end: 'top 40%', scrub: true },
        });
      });
      gsap.to(q('.xi-stage__bar'), {
        scaleY: 1, ease: 'none',
        scrollTrigger: { trigger: q('.xi-seasons__copy')[0], start: 'top 60%', end: 'bottom 70%', scrub: true },
      });
    });

    gsap.from(q('.xi-cta__copy > *'), {
      y: 26, opacity: 0, duration: 1, ease: EASE, stagger: 0.08,
      scrollTrigger: { trigger: q('.xi-cta')[0], start: 'top 75%' },
    });
    gsap.fromTo(q('.xi-cta__fig img'), { yPercent: -12 }, {
      yPercent: 12, ease: 'none',
      scrollTrigger: { trigger: q('.xi-cta')[0], start: 'top bottom', end: 'bottom top', scrub: true },
    });

    return () => {
      split?.revert();
      mm.revert();
    };
  }, { scope: root });

  const counts = useMemo(() => Object.fromEntries(KINDS.map((k) => [k, PLACES.filter((p) => p.kind === k).length])), []);

  return (
    <div ref={root} className="xi">
      <header className="tv-container xi-hero">
        <h1 className="tv-display xi-hero__title">Places worth <em>the journey.</em></h1>
        <div className="xi-hero__side">
          <p className="tv-lead">
            Fourteen places to start from, one line each. Point at a name to see it; pick one and the planner
            opens with it written in, ready to send.
          </p>
          <a className="xi-hero__jump tv-meta" href="#xi-index"
            onClick={(event) => { event.preventDefault(); document.getElementById('xi-index')?.scrollIntoView({ behavior: 'smooth' }); }}>
            Go to the index <span aria-hidden="true">↓</span>
          </a>
        </div>
      </header>

      <div className="tv-container xi-window">
        <figure className="xi-window__fig">
          <img className="tv-img" src={placeImage('fuji-dusk')} alt="Mount Fuji at dusk, its snow lit orange" />
          <figcaption className="tv-meta">Mount Fuji at dusk · 35.36° N 138.73° E</figcaption>
        </figure>
      </div>

      <section className="xm-moods" aria-labelledby="xm-moods-title">
        <div className="xm-moods__pin">
          <div className="tv-container xm-moods__head">
            <h2 className="tv-display xm-h2" id="xm-moods-title">Six moods, <em>one at a time.</em></h2>
            <div className="xm-progress" aria-hidden="true">
              <ol>
                {MOODS.map((m, i) => (
                  <li key={m.id} className={i === current ? 'is-on' : ''}>{m.name}</li>
                ))}
              </ol>
              <span className="xm-progress__track"><span className="xm-progress__bar" /></span>
            </div>
          </div>

          <div className="xm-track">
            {MOODS.map((m, i) => (
              <article key={m.id} className="xm-panel" aria-labelledby={`xm-mood-${m.id}`}>
                <div className="xm-panel__media">
                  <figure className="xm-panel__main tv-figure">
                    <img className="tv-img" src={moodImage(m.images[0])} alt={m.alts[0]} loading="lazy" />
                  </figure>
                  <figure className="xm-panel__inset">
                    <img className="tv-img" src={moodImage(m.images[1])} alt={m.alts[1]} loading="lazy" />
                  </figure>
                </div>
                <div className="xm-panel__copy">
                  <span className="tv-meta">{i + 1} of {MOODS.length}</span>
                  <h3 className="tv-display xm-panel__name" id={`xm-mood-${m.id}`}>{m.name}, <em>{m.turn}</em></h3>
                  <p className="tv-body">{m.line}</p>
                  <ul className="xm-panel__places">
                    {m.places.map((id) => {
                      const place = placeById(id);
                      return (
                        <li key={id}>
                          <button type="button" onClick={() => plan(id)} aria-label={`Plan ${place.city}: ${place.title}`}>
                            <img src={placeImage(id)} alt="" loading="lazy" />
                            <span className="xm-place__text">
                              <span className="xm-place__city">{place.city}</span>
                              <span className="tv-meta">{place.title}</span>
                            </span>
                            <span className="tv-meta xm-place__months">{place.months}</span>
                            <ArrowUpRightIcon width={14} height={14} aria-hidden="true" />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="tv-container xi-index" id="xi-index" aria-labelledby="xi-index-title">
        <div className="xi-index__head">
          <h2 className="tv-display xi-h2" id="xi-index-title">Every place, <em>one line each.</em></h2>
          <div className="tv-seg" role="group" aria-label="Show places by kind">
            {(['All', ...KINDS] as Filter[]).map((k) => (
              <button key={k} type="button" className={`tv-seg__btn ${filter === k ? 'is-on' : ''}`}
                aria-pressed={filter === k} onClick={() => choose(k)}>
                {k}{k !== 'All' && <span className="xi-count">{counts[k]}</span>}
              </button>
            ))}
          </div>
        </div>

        <div className="xi-cols tv-label" aria-hidden="true">
          <span>Kind</span><span>Place</span><span>What you'll see</span><span>Good months</span><span>Stay</span><span />
        </div>
        <span className="xi-index__rule" aria-hidden="true" />

        <ul className="xi-list" onMouseMove={onMove} onMouseLeave={() => setActive(null)}>
          {visible.map((place) => (
            <li key={place.id} className="xi-row" data-flip-id={place.id} data-kind={place.kind}>
              <button type="button" className="xi-row__btn"
                onMouseEnter={() => setActive(place.id)} onFocus={() => setActive(place.id)} onBlur={() => setActive(null)}
                onClick={() => plan(place.id)} aria-label={`Plan ${place.city}: ${place.title}`}>
                <img className="xi-row__thumb" src={placeImage(place.id)} alt="" loading="lazy" />
                <span className="xi-row__kind tv-meta">{place.kind}</span>
                <span className="xi-row__place">
                  <span className="tv-display xi-row__city">{place.city}</span>
                  <span className="tv-meta xi-row__country">{place.country}</span>
                </span>
                <span className="xi-row__title">{place.title}</span>
                <span className="xi-row__months tv-meta">{place.months}</span>
                <span className="xi-row__nights tv-meta">{place.nights} nights</span>
                <span className="xi-row__go" aria-hidden="true"><ArrowUpRightIcon width={16} height={16} /></span>
              </button>
            </li>
          ))}
        </ul>
        <p className="tv-meta xi-index__foot">
          Good months are general guidance, not a forecast. Your planner checks the weather for your actual dates.
        </p>
      </section>

      <section className="xi-seasons" aria-labelledby="xi-seasons-title">
        <div className="tv-container xi-seasons__inner">
          <div className="xi-stage" aria-hidden="true">
            {SEASONS.map((season, i) => (
              <figure key={season.id} className="xi-stage__fig" data-i={i}>
                <img className="tv-img" src={placeImage(season.image)} alt="" loading="lazy" />
                <figcaption className="tv-meta">{season.name} · {season.caption}</figcaption>
              </figure>
            ))}
            <span className="xi-stage__track"><span className="xi-stage__bar" /></span>
          </div>

          <div className="xi-seasons__copy">
            <h2 className="tv-display xi-h2" id="xi-seasons-title">Pick the month <em>first.</em></h2>
            <p className="tv-lead">
              The same city is a different trip in another season. Start from when you can go, and these are good
              places to begin with.
            </p>
            {SEASONS.map((season) => (
              <article key={season.id} className="xi-season">
                <figure className="xi-season__inline tv-figure">
                  <img className="tv-img" src={placeImage(season.image)} alt={season.caption} loading="lazy" />
                </figure>
                <span className="tv-meta">{season.months}</span>
                <h3 className="tv-display xi-season__name">{season.name}, <em>{season.turn}</em></h3>
                <p className="tv-body xi-season__line">{season.line}</p>
                <ul className="xi-season__places">
                  {season.places.map((id) => {
                    const place = placeById(id);
                    return (
                      <li key={id}>
                        <button type="button" onClick={() => plan(id)}>
                          <span>{place.city}</span>
                          <span className="tv-meta">{place.title}</span>
                          <ArrowUpRightIcon width={14} height={14} aria-hidden="true" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="xi-cta tv-invert">
        <div className="tv-container xi-cta__inner">
          <div className="xi-cta__copy">
            <h2 className="tv-display xi-cta__title">Somewhere else in mind? <em>Describe it instead.</em></h2>
            <p className="tv-lead">
              A place, a feeling or a budget is enough. Your guide asks three quick things and plans the rest.
            </p>
            <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
              Start planning <ArrowUpRightIcon width={15} height={15} />
            </button>
          </div>
          <figure className="xi-cta__fig">
            <img className="tv-img tv-img--drift" src={placeImage('contrail')} alt="A plane drawing a contrail across a deep blue sky" loading="lazy" />
          </figure>
        </div>
      </section>

      <div ref={preview} className="xi-preview" aria-hidden="true">
        {PLACES.map((place) => (
          <img key={place.id} className="xi-preview__img" data-id={place.id} src={placeImage(place.id)} alt="" loading="lazy" />
        ))}
      </div>
    </div>
  );
}
