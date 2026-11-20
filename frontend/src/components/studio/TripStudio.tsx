/*
 * TripStudio — one page for the whole trip: plan, map and 3D, the days,
 * the budget, and the same conversation in a window.
 *
 * It lives inside the planner (not a separate app page), so the chat, the
 * stream in progress and the live plan state are the very same objects.
 * The stage carries data-flip-id="trip-stage" for the morph from the chat card.
 *
 * Days, Details and Chat are FloatingWindows: movable, resizable and closable, with
 * toggles in the toolbar. A window docked against a side edge reserves that strip, so
 * the plan and map shrink beside it instead of hiding under it. Narrow screens get
 * bottom sheets, one at a time.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import type { ItineraryGraph } from '../create/itineraryGraph';
import type { CurrentTripContext } from '../create/CurrentTrip';
import type { TripDocument, TripModelResponse } from '../../services/tripService';
import { guideById } from '../guide/guides';
import { ThemeToggle } from '../common/ThemeToggle';
import { FloatingWindow, WINDOW_GAP, clampRect, useBoardSize, type Rect, type Size } from '../common/FloatingWindow';
import { ChatIcon, ClockIcon, WalletIcon } from '../home/v2/IconsV2';
import { EASE, prefersReducedMotion } from '../home/v2/motion';
import { StudioPlan, dayDate, money } from './StudioPlan';
import { TripConditions } from './TripConditions';
import { ExportMenu } from './ExportMenu';
import { CalendarButton } from './CalendarButton';
import { DayPlayback } from './DayPlayback';
import type { PlaybackFrame } from '../../lib/dayRoute';
import { AroundHere } from '../create/AroundHere';
import { useDayCovers } from './dayCovers';
import { creditLine, sizedImage } from '../../services/placeMedia';
import '../../styles/day-covers.css';
import '../../styles/trip-studio.css';

const SpatialWorkspace = React.lazy(() => import('../create/SpatialWorkspace').then((module) => ({ default: module.SpatialWorkspace })));
const SketchbookView = React.lazy(() => import('../sketch/SketchbookView').then((module) => ({ default: module.SketchbookView })));

type Tab = 'plan' | 'sketch' | 'map' | 'graph';
const TABS: { id: Tab; label: string }[] = [
  { id: 'plan', label: 'Plan' }, { id: 'sketch', label: 'Sketch' }, { id: 'map', label: 'Map' }, { id: 'graph', label: '3D' },
];

type Win = 'days' | 'details' | 'chat';
const WINDOWS: { id: Win; label: string; Icon: typeof ClockIcon }[] = [
  { id: 'days', label: 'Days', Icon: ClockIcon }, { id: 'details', label: 'Details', Icon: WalletIcon }, { id: 'chat', label: 'Chat', Icon: ChatIcon },
];
const LAYOUT_KEY = 'tripverse-studio-windows';
const SHEET_QUERY = '(max-width: 900px)'; // phones and portrait tablets
const MIN_CANVAS = 420; // below this, docked windows float over the canvas instead of squeezing it
const EDGE = 24;        // within this of a side, a window counts as docked there

interface Layout { open: Record<Win, boolean>; rects: Partial<Record<Win, Rect>> }

const defaultRect = (id: Win, board: Size): Rect => {
  const tall = board.h - WINDOW_GAP * 2;
  const compact = board.w < 1200; // laptops and tablets: slimmer side windows leave the canvas room
  if (id === 'days') return { x: WINDOW_GAP, y: WINDOW_GAP, w: compact ? 216 : 248, h: Math.min(tall, 620) };
  if (id === 'details') { const w = compact ? 256 : 288; return { x: board.w - w - WINDOW_GAP, y: WINDOW_GAP, w, h: Math.min(tall, 440) }; }
  return { x: board.w - 420 - WINDOW_GAP, y: WINDOW_GAP, w: 420, h: tall };
};

const readLayout = (): Layout => {
  const fallback: Layout = { open: { days: true, details: true, chat: false }, rects: {} };
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || 'null') as Layout | null;
    // Chat always starts closed: the studio opens onto the trip, not the conversation.
    return saved?.open ? { open: { ...fallback.open, ...saved.open, chat: false }, rects: saved.rects || {} } : fallback;
  } catch { return fallback; }
};

const readTab = (): Tab => {
  try { const value = localStorage.getItem('tripverse-studio-tab'); return value === 'sketch' || value === 'map' || value === 'graph' ? value : 'plan'; }
  catch { return 'plan'; }
};

function useMedia(query: string) {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, [query]);
  return matches;
}

interface Props {
  trip: TripModelResponse;
  tripContext: CurrentTripContext;
  graph: ItineraryGraph | null;
  document: TripDocument | null;
  dark: boolean;
  isLoading: boolean;
  loadingStage: string;
  day: number | null;
  onSelectDay: (day: number | null) => void;
  onBack: () => void;
  onOpenBudget: () => void;
  onFinalize?: () => void;
  /** "Add to day N" in Around here; left out while a reply is streaming, which hides the button. */
  onAddPlace?: (name: string, day: number | null) => void;
  chat: React.ReactNode;
}

export function TripStudio({
  trip, tripContext, graph, document, dark, isLoading, loadingStage, day, onSelectDay, onBack, onOpenBudget, onFinalize, onAddPlace, chat,
}: Props) {
  const root = useRef<HTMLDivElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const board = useBoardSize(boardRef);
  const sheet = useMedia(SHEET_QUERY);
  const [tab, setTab] = useState<Tab>(readTab);
  const [layout, setLayout] = useState<Layout>(readLayout);
  // Narrow screens show one bottom sheet at a time and keep that apart from the desktop layout.
  const [sheetWin, setSheetWin] = useState<Win | null>(null);
  const [focusName, setFocusName] = useState<string | null>(null);
  const [playback, setPlayback] = useState<PlaybackFrame>({ clock: 9 * 60, playing: false, follow: true });
  const [picked, setPicked] = useState<{ name: string; n: number } | null>(null);
  const onFrame = useCallback((frame: PlaybackFrame) => setPlayback(frame), []);
  const dayPlan = useMemo(() => {
    if (day == null || !document) return null;
    const entry = document.days.find((item) => item.day === day);
    return entry ? { base: entry.base, items: entry.items } : null;
  }, [document, day]);
  const isOpen = (id: Win) => sheet ? sheetWin === id : layout.open[id];
  const guide = guideById(trip.planning_preferences?.guide);
  const days = document?.days ?? [];
  const budget = document?.budget;
  const covers = useDayCovers(document);
  // The selected day's base city, located by the enrichment: what "Around here" in Trip details centres on.
  const dayBase = days.find((entry) => entry.day === day)?.base;
  const nearby = dayBase ? document?.enrichment?.places?.find((place) => place.name === dayBase) : undefined;
  // The brief is on the trip from the start; the document arrives a moment later.
  const prefs = trip.planning_preferences;
  const people = (prefs?.adults ?? document?.travelers.adults ?? 1) + (prefs?.children ?? document?.travelers.children ?? 0);

  const pickTab = (next: Tab) => {
    setTab(next);
    try { localStorage.setItem('tripverse-studio-tab', next); } catch { /* preference only */ }
  };

  // Functional updates, so two quick toggles can't overwrite each other.
  const save = (update: (prev: Layout) => Layout) => setLayout((prev) => {
    const next = update(prev);
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ ...next, open: { ...next.open, chat: false } })); } catch { /* preference only */ }
    return next;
  });
  const toggle = (id: Win, open?: boolean) => {
    if (sheet) { setSheetWin((current) => (open ?? current !== id) ? id : null); return; }
    save((prev) => ({ ...prev, open: { ...prev.open, [id]: open ?? !prev.open[id] } }));
  };
  const rectOf = (id: Win) => board ? clampRect(layout.rects[id] || defaultRect(id, board), board) : null;
  const setRect = (id: Win, rect: Rect | undefined) => save((prev) => ({ ...prev, rects: { ...prev.rects, [id]: rect } }));
  const resetLayout = () => save((prev) => ({ ...prev, rects: {} }));

  // Windows docked against a side reserve that strip, unless that would leave the canvas too thin.
  const inset = useMemo(() => {
    const none = { left: 0, right: 0 };
    if (!board || sheet) return none;
    const sides = WINDOWS.reduce((acc, { id }) => {
      const rect = layout.open[id] && clampRect(layout.rects[id] || defaultRect(id, board), board);
      if (!rect) return acc;
      if (rect.x <= EDGE) acc.left = Math.max(acc.left, rect.x + rect.w + WINDOW_GAP);
      else if (rect.x + rect.w >= board.w - EDGE) acc.right = Math.max(acc.right, board.w - rect.x + WINDOW_GAP);
      return acc;
    }, { ...none });
    return board.w - sides.left - sides.right >= MIN_CANVAS ? sides : none;
  }, [board, sheet, layout]);

  // Rails settle in after the canvas morph lands.
  useGSAP(() => {
    if (prefersReducedMotion()) return;
    gsap.from('.tv-studio__bar > *, .tv-studio__toolbar > *', { opacity: 0, y: 8, duration: 0.45, ease: EASE, stagger: 0.03, delay: 0.25 });
  }, { scope: root });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (sheet && sheetWin) setSheetWin(null);
      else if (layout.open.chat) toggle('chat', false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const windowProps = (id: Win) => {
    const rect = rectOf(id)!;
    return {
      rect, board: board!, sheet, onRectChange: (next: Rect) => setRect(id, next), onClose: () => toggle(id, false),
      onReset: () => setRect(id, undefined),
    };
  };

  return (
    <div className="tv-studio" ref={root} role="region" aria-label="Trip studio">
      <header className="tv-studio__bar">
        <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm tv-studio__back" onClick={onBack} aria-label="Back to the chat">
          <span aria-hidden="true">←</span><span className="tv-studio__back-label" aria-hidden="true">Chat</span>
        </button>
        <div className="tv-studio__title">
          <h1>{trip.destination || 'Your trip'}</h1>
          <span className="tv-meta">
            {[
              trip.duration_days && `${trip.duration_days} days`,
              document?.start_date && `${dayDate(document.start_date)} to ${dayDate(document.end_date)}`,
              `${people} traveler${people === 1 ? '' : 's'}`,
            ].filter(Boolean).join(' · ')}
          </span>
        </div>
        <span className="tv-app__bar-spacer" />
        {isLoading && <span className="tv-studio__live" role="status"><i aria-hidden="true" />{guide.name}: {loadingStage}</span>}
        <ThemeToggle />
        {trip.status === 'COMPLETED' ? <span className="tv-tag tv-tag--green">Final trip</span>
          : onFinalize && <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" onClick={onFinalize}>Finalize trip</button>}
        <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" onClick={onOpenBudget}>Budget</button>
        <CalendarButton document={document} />
        <ExportMenu document={document}
          pdf={document ? () => import('../../lib/exporters/pdf').then(({ tripPdf }) => tripPdf(document, guide)) : undefined} />
      </header>

      <main className="tv-studio__stage" data-flip-id="trip-stage">
        <div className="tv-studio__toolbar">
          <div className="tv-studio__tabs" role="tablist" aria-label="Trip view">
            {TABS.map((entry) => (
              <button key={entry.id} type="button" role="tab" id={`studio-tab-${entry.id}`} aria-selected={tab === entry.id}
                aria-controls="studio-canvas" tabIndex={tab === entry.id ? 0 : -1} onClick={() => pickTab(entry.id)}
                onKeyDown={(event) => {
                  const index = TABS.findIndex((item) => item.id === tab);
                  if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
                    const next = TABS[(index + (event.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length];
                    pickTab(next.id);
                    window.document.getElementById(`studio-tab-${next.id}`)?.focus();
                  }
                }}>
                {entry.label}
              </button>
            ))}
          </div>
          <div className="tv-studio__windows" role="group" aria-label="Panels">
            {WINDOWS.map(({ id, label, Icon }) => (
              <button key={id} type="button" aria-pressed={isOpen(id)} onClick={() => toggle(id)}
                title={`${isOpen(id) ? 'Hide' : 'Show'} ${id === 'chat' ? `chat with ${guide.name}` : label.toLowerCase()}`}>
                {id === 'chat' ? <img src={guide.image} alt="" className="tv-studio__guide" /> : <Icon width={14} height={14} />}
                <span>{label}</span>
              </button>
            ))}
            {!sheet && Object.keys(layout.rects).some((id) => layout.rects[id as Win]) && (
              <button type="button" className="tv-studio__reset" onClick={resetLayout} title="Put every panel back in its place">Reset</button>
            )}
          </div>
        </div>

        <div className="tv-studio__board" ref={boardRef}>
          <div className={`tv-studio__canvas is-${tab}`} id="studio-canvas" role="tabpanel" aria-labelledby={`studio-tab-${tab}`}
            style={{ '--inset-left': `${inset.left}px`, '--inset-right': `${inset.right}px` } as React.CSSProperties}>
            {tab === 'plan' || tab === 'sketch' ? (
              !document ? <p className="tv-plan__empty">Loading your plan…</p>
                : tab === 'plan' ? <StudioPlan document={document} day={day} onSelectDay={onSelectDay} />
                  : (
                    <React.Suspense fallback={<p className="tv-plan__empty">Loading the sketchbook…</p>}>
                      <SketchbookView document={document} day={day} onSelectDay={onSelectDay} guide={guide} busy={isLoading} stage={loadingStage} />
                    </React.Suspense>
                  )
            ) : (
              <React.Suspense fallback={<p className="tv-plan__empty">Loading the {tab === 'map' ? 'map' : '3D view'}…</p>}>
                <SpatialWorkspace isOpen embedded guide={guide} mode={tab} onModeChange={(mode) => pickTab(mode)}
                  selectedDay={day} onSelectDay={onSelectDay} trip={tripContext} tripId={trip.id}
                  graph={graph} preferences={trip.planning_preferences} dark={dark} onAddPlace={onAddPlace} weather={document?.enrichment?.weather}
                  focusName={focusName} dayPlan={dayPlan} playback={playback} onPickStop={(name) => setPicked({ name, n: Date.now() })} />
              </React.Suspense>
            )}
            {tab === 'map' && document && (
              <DayPlayback days={document.days} day={day} onSelectDay={onSelectDay} onFocus={setFocusName} onFrame={onFrame} picked={picked} />
            )}
          </div>

          {board && isOpen('days') && (
            <FloatingWindow title="Days" {...windowProps('days')} min={{ w: 200, h: 160 }} className="tv-studio__daywin">
              <nav className="tv-studio__days" aria-label="Trip days">
                <ol>
                  <li>
                    <button type="button" aria-current={day === null ? 'page' : undefined} onClick={() => onSelectDay(null)}>
                      <span className="tv-label">OVERVIEW</span><strong>Whole route</strong>
                    </button>
                  </li>
                  {days.map((entry) => (
                    <li key={entry.day}>
                      <button type="button" aria-current={day === entry.day ? 'page' : undefined} onClick={() => onSelectDay(entry.day)}
                        className={covers[entry.day] ? 'has-cover' : undefined}>
                        {covers[entry.day] && <span className="tv-daycover">
                          <img src={sizedImage(covers[entry.day].image, 120)} alt="" title={creditLine(covers[entry.day])} loading="lazy" decoding="async"
                            onLoad={(event) => event.currentTarget.classList.add('is-loaded')}
                            ref={(img) => { if (img?.complete && img.naturalWidth) img.classList.add('is-loaded'); }} />
                        </span>}
                        <span className="tv-label">DAY {entry.day}{entry.date ? ` · ${dayDate(entry.date)?.toUpperCase()}` : ''}</span>
                        <strong>{entry.base}</strong>
                        <small>{entry.items.length ? `${entry.items.length} place${entry.items.length === 1 ? '' : 's'}` : 'Open day'}</small>
                      </button>
                    </li>
                  ))}
                </ol>
                {Object.keys(covers).length > 0 && (
                  <p className="tv-studio__days-credit">Photos: Wikimedia Commons. Hover a photo for its author and licence.</p>
                )}
              </nav>
            </FloatingWindow>
          )}

          {board && isOpen('details') && (
            <FloatingWindow title="Trip details" {...windowProps('details')} min={{ w: 240, h: 160 }}>
              <div className="tv-studio__info">
                {budget && (
                  <section className="tv-studio__card">
                    <span className="tv-label">BUDGET</span>
                    <strong>{budget.target !== null ? money(budget.target, budget.currency) : 'No target set'}</strong>
                    <dl>
                      {budget.planned !== null && <><dt>Planned by {guide.name}</dt><dd>~{money(budget.planned, budget.currency)}</dd></>}
                      <dt>Projected</dt><dd>~{money(budget.projected, budget.currency)}</dd>
                      <dt>Entered</dt><dd>{money(budget.entered, budget.currency)}</dd>
                    </dl>
                    <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" onClick={onOpenBudget}>Open budget</button>
                  </section>
                )}
                {document && (
                  <section className="tv-studio__card">
                    <span className="tv-label">YOUR BRIEF</span>
                    <p>{document.pace} pace · {document.comfort.replace('_', '-')} · {document.travel_mode}</p>
                    {document.interests.length > 0 && <p>Into {document.interests.join(', ')}</p>}
                    {document.avoid.length > 0 && <p>Avoiding {document.avoid.join(', ')}</p>}
                    {document.must_see.length > 0 && <p>Must see {document.must_see.join(', ')}</p>}
                  </section>
                )}
                {document && <TripConditions enrichment={document.enrichment} selectedDay={day} onSelectDay={onSelectDay} />}
                {nearby && (
                  <section className="tv-studio__card">
                    <span className="tv-label">DAY {day} · {nearby.name.toUpperCase()}</span>
                    <AroundHere lat={nearby.lat} lon={nearby.lon} day={day} onAddPlace={onAddPlace} />
                  </section>
                )}
                {!budget && !document && <p className="tv-plan__empty">Loading trip details…</p>}
              </div>
            </FloatingWindow>
          )}

          {board && isOpen('chat') && (
            <FloatingWindow title={`Chat with ${guide.name}`} {...windowProps('chat')} min={{ w: 320, h: 280 }} className="tv-studio__chat">
              {chat}
            </FloatingWindow>
          )}
        </div>
      </main>
    </div>
  );
}
