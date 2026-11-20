/*
 * The Sketch tab: the trip as hand-drawn pages, an overview and then one per day.
 *
 * The page follows the studio's selected day (and sets it), so the Days window, the
 * plan and the sketch always agree. Arrow keys and swipes turn pages.
 *
 * The guide draws each page the first time you see it. When the plan changes (a new
 * copilot state or a refreshed document), the layouts are diffed: the guide flips to
 * the day that changed, draws only the new elements and says what changed. Pages
 * that changed elsewhere keep their new elements queued until you visit them.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import gsap from 'gsap';
import '@fontsource/caveat/400.css';
import '@fontsource/caveat/700.css';
import '@fontsource/yomogi/400.css';

import type { TripDocument } from '../../services/tripService';
import { GuideCharacter, type GuideMood } from '../guide/GuideCharacter';
import type { Guide } from '../guide/guides';
import { prefersReducedMotion } from '../home/v2/motion';
import { layoutSketch, type Orientation } from './layout';
import { describeChange, diffPages } from './live';
import { drawPage } from './motion';
import { SketchPageSvg } from './render';
import type { SketchPage } from './types';
import { SketchNotesLayer } from './annotate/SketchNotesLayer';
import { useSketchNotes } from './annotate/notes';
import '../../styles/sketch.css';

// Excalidraw is large: it loads only when the traveler opens the editor.
const SketchEditor = React.lazy(() => import('./annotate/SketchEditor'));

const FIRST_DRAW_SECONDS = 3.2;
const SAY_MS = 5000;

interface Props {
  document: TripDocument;
  day: number | null;
  onSelectDay: (day: number | null) => void;
  guide: Guide;
  /** The agent is working: the guide thinks out loud with the current stage. */
  busy?: boolean;
  stage?: string;
}

// Phones held upright get portrait pages; everything else gets the A4 landscape sheet.
const UPRIGHT = '(max-width: 700px) and (orientation: portrait)';
function useOrientation(): Orientation {
  const [upright, setUpright] = useState(() => window.matchMedia(UPRIGHT).matches);
  useEffect(() => {
    const list = window.matchMedia(UPRIGHT);
    const update = () => setUpright(list.matches);
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, []);
  return upright ? 'portrait' : 'landscape';
}

const overBy = (page: SketchPage | undefined) => {
  const el = page?.elements.find((entry) => entry.id === 'rc-over');
  return el?.kind === 'text' ? el.lines[0] : null;
};

export function SketchbookView({ document, day, onSelectDay, guide, busy = false, stage }: Props) {
  const orientation = useOrientation();
  const pages = useMemo(() => layoutSketch(document, orientation), [document, orientation]);
  const index = Math.max(0, pages.findIndex((page) => page.day === day));
  const page = pages[index];
  const svg = useRef<SVGSVGElement>(null);
  const swipe = useRef<{ x: number; y: number } | null>(null);

  const drawn = useRef(new Set<string>());
  const queued = useRef(new Map<string, Set<string>>()); // page id → element ids still to draw
  const previous = useRef<SketchPage[] | null>(null);
  const status = useRef(document.status);

  const [drawing, setDrawing] = useState(false);
  const [moment, setMoment] = useState<{ mood: GuideMood; say: string } | null>(null);
  const [replay, setReplay] = useState(0);
  const [editing, setEditing] = useState(false);
  const notes = useSketchNotes(document.trip_id);

  // Diff every new layout against the last one and queue what's new (before the draw effect below).
  useLayoutEffect(() => {
    const before = previous.current;
    previous.current = pages;
    if (!before) return;
    const changes = diffPages(before, pages);
    for (const change of changes) {
      const ids = queued.current.get(change.page.id) ?? new Set<string>();
      change.added.forEach((id) => ids.add(id));
      if (ids.size) queued.current.set(change.page.id, ids);
    }
    const placeChange = changes.find((change) => change.page.kind === 'day' && describeChange(change));
    const over = overBy(pages[0]);
    const newlyOver = !!over && !overBy(before[0]);
    if (newlyOver) setMoment({ mood: 'confused', say: `That puts us ${over}.` });
    else if (placeChange) setMoment({ mood: 'celebrating', say: describeChange(placeChange)! });
    if (status.current !== 'complete' && document.status === 'complete') {
      setMoment({ mood: 'celebrating', say: `All ${pages.length - 1} days are sketched.` });
    }
    status.current = document.status;
    // Fly to the day that changed (or to the receipt when only the budget tipped over).
    const target = placeChange?.page ?? (newlyOver ? pages[0] : null);
    if (target && target.id !== page.id) onSelectDay(target.day);
  }, [pages]); // eslint-disable-line react-hooks/exhaustive-deps

  // Draw: the whole page on a first visit (or a replay), otherwise only what's queued for it.
  // Cleanup finishes the page instantly; an unfinished draw is queued again for the next visit.
  useLayoutEffect(() => {
    if (!svg.current) return;
    const firstVisit = !drawn.current.has(page.id);
    const ids = queued.current.get(page.id);
    drawn.current.add(page.id);
    queued.current.delete(page.id);
    if (prefersReducedMotion() || (!firstVisit && !ids?.size)) return;

    let tl!: gsap.core.Timeline;
    const ctx = gsap.context(() => { tl = drawPage(svg.current!, firstVisit ? null : ids!, FIRST_DRAW_SECONDS); });
    setDrawing(true);
    tl.eventCallback('onComplete', () => setDrawing(false));
    return () => {
      if (tl.progress() < 1) {
        if (firstVisit) drawn.current.delete(page.id);
        else queued.current.set(page.id, new Set([...(queued.current.get(page.id) ?? []), ...ids!]));
      }
      ctx.revert();
      setDrawing(false);
    };
  }, [page, replay]);

  useEffect(() => {
    if (!moment) return;
    const timer = window.setTimeout(() => setMoment(null), SAY_MS);
    return () => window.clearTimeout(timer);
  }, [moment]);

  const go = (next: number) => {
    const clamped = Math.min(pages.length - 1, Math.max(0, next));
    if (clamped !== index) onSelectDay(pages[clamped].day);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const step = { ArrowRight: 1, PageDown: 1, ArrowLeft: -1, PageUp: -1 }[event.key];
    if (step) go(index + step);
    else if (event.key === 'Home') go(0);
    else if (event.key === 'End') go(pages.length - 1);
    else return;
    event.preventDefault();
  };

  // What the guide is doing right now, most specific first.
  const mood: GuideMood = drawing ? 'drawing' : moment?.mood ?? (busy ? 'thinking' : 'idle');
  const say = moment?.say ?? (busy && stage ? `${stage}…` : null);

  return (
    <>
    <section className="tv-sketch" aria-roledescription="sketchbook" aria-label="Trip sketchbook" tabIndex={0} onKeyDown={onKeyDown}
      onPointerDown={(event) => { if (event.pointerType !== 'mouse') swipe.current = { x: event.clientX, y: event.clientY }; }}
      onPointerUp={(event) => {
        const start = swipe.current;
        swipe.current = null;
        if (!start) return;
        const [dx, dy] = [event.clientX - start.x, event.clientY - start.y];
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) go(index + (dx < 0 ? 1 : -1));
      }}
      onPointerCancel={() => { swipe.current = null; }}>
      <div className="tv-sketch__stage">
        <SketchPageSvg ref={svg} key={page.id} page={page} penImage={guide.image} className="tv-sketch__page" />
        <SketchNotesLayer tripId={document.trip_id} page={page} />
      </div>
      <div className="tv-sketch__bar">
        <div className="tv-sketch__guide">
          <GuideCharacter guide={guide} size={40} mood={mood} label={`${guide.name}, ${mood === 'idle' ? 'your guide' : mood}`} />
          <p className={`tv-sketch__say ${say ? 'is-on' : ''}`} role="status" aria-live="polite">{say ?? ''}</p>
        </div>
        <nav className="tv-sketch__nav" aria-label="Pages">
          <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" onClick={() => go(index - 1)} disabled={index === 0} aria-label="Previous page">←</button>
          <span className="tv-sketch__count" aria-live="polite">
            {page.kind === 'overview' ? 'Overview' : page.title} <span className="tv-meta">· {index + 1} of {pages.length}</span>
          </span>
          <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" onClick={() => go(index + 1)} disabled={index === pages.length - 1} aria-label="Next page">→</button>
        </nav>
        <div className="tv-sketch__actions">
          <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" disabled={drawing}
            onClick={() => { drawn.current.delete(page.id); setReplay((count) => count + 1); }}>
            Redraw
          </button>
          {page.w > page.h && <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" onClick={() => setEditing(true)}>
            {notes[page.id] ? 'Edit drawing' : 'Draw'}
          </button>}
        </div>
      </div>
    </section>
    {/* Outside the section: its arrow-key and swipe handlers would turn pages while drawing. */}
    {editing && <React.Suspense fallback={null}>
      <SketchEditor tripId={document.trip_id} page={page} scene={notes[page.id]} onClose={() => setEditing(false)} />
    </React.Suspense>}
    </>
  );
}
