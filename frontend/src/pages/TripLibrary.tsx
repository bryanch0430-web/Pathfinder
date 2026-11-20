import { useEffect, useMemo, useRef, useState } from 'react';
import { AppBar } from '../components/common/AppBar';
import { ArrowRightIcon, ArrowUpRightIcon, ClockIcon, PinIcon, PlusIcon } from '../components/home/v2/IconsV2';
import { apiFetch } from '../services/apiClient';
import { creditLine, sizedImage, usePlaceMedia } from '../services/placeMedia';
import { deleteTrip, getTripMessages, type TripModelResponse } from '../services/tripService';
import '../styles/trip-library.css';

type Filter = 'all' | 'completed' | 'in-progress';
type Sort = 'recent' | 'oldest' | 'destination';

interface TripLibraryProps {
  onNavigateHome: () => void;
  onNavigateExplore: () => void;
  onNavigateProfile: () => void;
  onStartPlanning: () => void;
  onOpenTrip: (tripId: string) => void;
}

const isCompleted = (trip: TripModelResponse) => trip.status === 'COMPLETED';
const isInProgress = (trip: TripModelResponse) => trip.status !== 'COMPLETED' && trip.status !== 'ARCHIVED';
const isStarted = (trip: TripModelResponse) => trip.onboarding_status === 'COMPLETE';
const title = (trip: TripModelResponse) => trip.destination?.trim() || 'Untitled journey';
const date = (value: string) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? 'Date unavailable'
    : parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
};
const stateLabel = (trip: TripModelResponse) => {
  if (isCompleted(trip)) return 'Completed';
  if (trip.status === 'ARCHIVED') return 'Archived';
  if (isStarted(trip)) return 'Planning';
  return 'Getting started';
};
const notePreview = (note: string) => {
  const lines = note.replace(/<br\s*\/?\s*>/gi, '\n').replace(/<[^>]+>/g, ' ').split(/\r?\n/);
  const firstLine = lines.map((line) => line
    .replace(/\p{Extended_Pictographic}/gu, '')
    .replace(/^[\s#>\-*]+/, '')
    .replace(/[*_`|]/g, '')
    .trim()).find(Boolean) || '';
  return firstLine.slice(0, 220) + (firstLine.length > 220 ? '…' : '');
};
// Photos fade in once decoded; the ref catches images already in the browser cache.
const revealed = (event: React.SyntheticEvent<HTMLImageElement>) => event.currentTarget.classList.add('is-loaded');
const revealIfReady = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth) img.classList.add('is-loaded'); };

export function TripLibrary({ onNavigateHome, onNavigateExplore, onNavigateProfile, onStartPlanning, onOpenTrip }: TripLibraryProps) {
  const detailRef = useRef<HTMLElement>(null);
  const [trips, setTrips] = useState<TripModelResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [sort, setSort] = useState<Sort>('recent');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [latestNote, setLatestNote] = useState<string | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const loadTrips = async () => {
    setLoading(true);
    setError(null);
    const response = await apiFetch<TripModelResponse[]>('/api/trips', { method: 'GET' });
    if (response.ok && response.data) {
      const next = response.data;
      setTrips(next);
      setSelectedId((current) => current && next.some((trip) => trip.id === current) ? current : next[0]?.id ?? null);
    } else {
      setError(response.status === 0 ? 'Could not connect to Pathfinder. Check that the API is running.' : response.error || 'Your trips could not be loaded.');
    }
    setLoading(false);
  };

  useEffect(() => { void loadTrips(); }, []);

  const visible = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return trips.filter((trip) => {
      if (filter === 'completed' && !isCompleted(trip)) return false;
      if (filter === 'in-progress' && !isInProgress(trip)) return false;
      return !term || [trip.destination, trip.origin_text, ...trip.places_to_visit]
        .some((value) => value?.toLocaleLowerCase().includes(term));
    }).sort((a, b) => {
      if (sort === 'destination') return title(a).localeCompare(title(b));
      const delta = new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      return sort === 'oldest' ? -delta : delta;
    });
  }, [trips, query, filter, sort]);

  const selected = visible.find((trip) => trip.id === selectedId) ?? visible[0] ?? null;
  // ponytail: looked up by destination name only (the library has no coordinates), so the server's
  // 20 km check is skipped; pass the trip's first located stop if namesakes show up.
  const mediaTargets = useMemo(() => trips.flatMap((trip) => trip.destination?.trim()
    ? [{ id: trip.id, name: trip.destination.trim() }] : []), [trips]);
  const photos = usePlaceMedia(mediaTargets);
  const cover = selected ? photos[selected.id] : undefined;
  const completedCount = trips.filter(isCompleted).length;
  const ongoingCount = trips.filter(isInProgress).length;

  useEffect(() => {
    setConfirmDelete(false);
    setLatestNote(null);
    if (!selected?.id) { setNoteLoading(false); return; }
    let active = true;
    setNoteLoading(true);
    void getTripMessages(selected.id).then((messages) => {
      if (!active) return;
      const note = [...messages].reverse().find((message) => message.role === 'ASSISTANT' && message.content?.trim());
      setLatestNote(note?.content?.trim() || null);
      setNoteLoading(false);
    }).catch(() => { if (active) setNoteLoading(false); });
    return () => { active = false; };
  }, [selected?.id]);

  const removeSelected = async () => {
    if (!selected || deleting) return;
    setDeleting(true);
    try {
      if (!await deleteTrip(selected.id)) throw new Error('The trip could not be deleted.');
      const remaining = trips.filter((trip) => trip.id !== selected.id);
      setTrips(remaining);
      setSelectedId(remaining[0]?.id ?? null);
      setConfirmDelete(false);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The trip could not be deleted.');
    } finally {
      setDeleting(false);
    }
  };

  const selectTrip = (id: string) => {
    setSelectedId(id);
    if (window.matchMedia('(max-width: 900px)').matches) {
      const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
      requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior, block: 'start' }));
    }
  };

  return (
    <div className="tv2 tv2-app tv-library">
      <AppBar
        current="trips"
        onNavigateHome={onNavigateHome}
        onNavigateExplore={onNavigateExplore}
        onNavigateProfile={onNavigateProfile}
        onStartPlanning={onStartPlanning}
      />
      <main className="tv-container tv-library__main">
        <header className="tv-library__intro tv-page__head">
          <div>
            <span className="tv-eyebrow">My trips</span>
            <h1 className="tv-display tv-page__title tv-library__title">Every journey, <em>in one place.</em></h1>
            <p className="tv-lead tv-library__lede">Return to a plan, follow its latest draft, or begin somewhere new.</p>
          </div>
          <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>
            <PlusIcon width={17} height={17} /><span>New journey</span>
          </button>
        </header>

        {!loading && !error && trips.length > 0 && (
          <div className="tv-library__summary" aria-label="Trip summary">
            <div><strong>{String(trips.length).padStart(2, '0')}</strong><span>Journeys</span></div>
            <div><strong>{String(completedCount).padStart(2, '0')}</strong><span>Completed</span></div>
            <div><strong>{String(ongoingCount).padStart(2, '0')}</strong><span>In progress</span></div>
          </div>
        )}

        {error && <div className="tv-library__alert" role="alert">{error} <button type="button" onClick={() => void loadTrips()}>Retry</button></div>}

        {loading ? (
          <div className="tv-library__state" role="status">Opening your journeys…</div>
        ) : trips.length === 0 && !error ? (
          <div className="tv-library__empty">
            <span className="tv-eyebrow">The first page is yours</span>
            <h2 className="tv-display">Where to first?</h2>
            <p>Your plans will gather here as you create them. Start with a place, a few days, and the stops you want to see.</p>
            <button type="button" className="tv-btn tv-btn--primary" onClick={onStartPlanning}>Plan your first trip <ArrowUpRightIcon width={16} height={16} /></button>
          </div>
        ) : trips.length > 0 && (
          <div className="tv-library__workspace">
            <section className="tv-library__index" aria-labelledby="library-index-heading">
              <div className="tv-library__section-head">
                <div><span className="tv-eyebrow">The collection</span><h2 id="library-index-heading" className="tv-display">Your journeys</h2></div>
                <span className="tv-meta">{visible.length} showing</span>
              </div>
              <div className="tv-library__tools">
                <label className="tv-library__search">
                  <span className="tv-field__label">Find a trip</span>
                  <input className="tv-input" type="search" placeholder="Destination, origin, or stop" value={query} onChange={(event) => setQuery(event.target.value)} />
                </label>
                <label className="tv-library__sort">
                  <span className="tv-field__label">Order by</span>
                  <select className="tv-input" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
                    <option value="recent">Recently updated</option><option value="oldest">Oldest updated</option><option value="destination">Destination A–Z</option>
                  </select>
                </label>
              </div>
              <div className="tv-library__filters" role="group" aria-label="Filter journeys">
                {([['all', 'All'], ['completed', 'Completed'], ['in-progress', 'In progress']] as const).map(([key, label]) => (
                  <button key={key} type="button" className={filter === key ? 'is-active' : ''} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>
                ))}
              </div>
              {visible.length ? (
                <div className="tv-library__list">
                  {visible.map((trip, index) => (
                    <button key={trip.id} type="button" className={`tv-library__row ${selected?.id === trip.id ? 'is-selected' : ''}`} aria-pressed={selected?.id === trip.id} aria-controls="trip-library-detail" onClick={() => selectTrip(trip.id)}>
                      <span className="tv-library__row-number">
                        {String(index + 1).padStart(2, '0')}
                        {photos[trip.id] && <img src={sizedImage(photos[trip.id].image, 120)} alt="" title={creditLine(photos[trip.id])} loading="lazy" decoding="async" onLoad={revealed} ref={revealIfReady} />}
                      </span>
                      <span className="tv-library__row-main"><strong>{title(trip)}</strong><span>{trip.origin_text ? `From ${trip.origin_text}` : 'Origin to be decided'} · {trip.duration_days ? `${trip.duration_days} ${trip.duration_days === 1 ? 'day' : 'days'}` : 'Dates open'}</span></span>
                      <span className={`tv-library__status ${isCompleted(trip) ? 'is-ready' : ''}`}>{stateLabel(trip)}</span>
                      <ArrowRightIcon width={17} height={17} />
                    </button>
                  ))}
                </div>
              ) : <div className="tv-library__no-results">{query.trim() ? 'No journeys match that search.' : filter === 'completed' ? 'No completed journeys yet.' : 'No journeys in this view yet.'} <button type="button" onClick={() => { setQuery(''); setFilter('all'); }}>Show all journeys</button></div>}
            </section>

            {selected && (
              <aside id="trip-library-detail" ref={detailRef} className="tv-library__detail" aria-label={`Details for ${title(selected)}`}>
                <div className="tv-library__detail-top"><span className="tv-eyebrow">Journey record</span><span className="tv-meta">Updated {date(selected.updated_at)}</span></div>
                {cover && (
                  <figure className="tv-library__cover">
                    <img key={cover.image} src={cover.image} alt={cover.title} decoding="async" onLoad={revealed} ref={revealIfReady} />
                    <figcaption>{cover.credit.file_page
                      ? <a href={cover.credit.file_page} target="_blank" rel="noopener noreferrer">{creditLine(cover)}</a>
                      : creditLine(cover)}</figcaption>
                  </figure>
                )}
                <span className={`tv-library__status ${isCompleted(selected) ? 'is-ready' : ''}`}>{stateLabel(selected)}</span>
                <h2 className="tv-display">{title(selected)}</h2>
                <div className="tv-library__route"><PinIcon width={18} height={18} /><span>{selected.origin_text || 'Choose an origin'} <ArrowRightIcon width={16} height={16} /> {selected.destination || 'Choose a destination'}</span></div>
                <dl className="tv-library__facts">
                  <div><dt>Duration</dt><dd>{selected.duration_days ? `${selected.duration_days} ${selected.duration_days === 1 ? 'day' : 'days'}` : 'Open'}</dd></div>
                  <div><dt>Started</dt><dd>{date(selected.created_at)}</dd></div>
                  <div><dt>Status</dt><dd>{stateLabel(selected)}</dd></div>
                </dl>
                <section className="tv-library__places"><h3>Places on the list</h3>{selected.places_to_visit.length ? <div>{selected.places_to_visit.map((place) => <span key={place}>{place}</span>)}</div> : <p>No places added yet. Add them while planning.</p>}</section>
                <section className="tv-library__note"><div><ClockIcon width={16} height={16} /><h3>Latest from your planner</h3></div><p>{noteLoading ? 'Loading the latest note…' : latestNote ? notePreview(latestNote) : 'Open this journey to continue the conversation.'}</p></section>
                <button type="button" className="tv-btn tv-btn--primary tv-library__open" onClick={() => onOpenTrip(selected.id)}>Open journey <ArrowUpRightIcon width={16} height={16} /></button>
                <div className="tv-library__delete">
                  {confirmDelete ? <><p>Delete this journey and its conversation?</p><div><button type="button" onClick={() => setConfirmDelete(false)}>Keep it</button><button type="button" disabled={deleting} onClick={() => void removeSelected()}>{deleting ? 'Deleting…' : 'Delete permanently'}</button></div></> : <button type="button" onClick={() => setConfirmDelete(true)}>Delete journey</button>}
                </div>
              </aside>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
