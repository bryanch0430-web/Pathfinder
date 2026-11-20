// Detail surfaces for the spatial view: the inspector, the expandable route tree,
// and the journal timeline. All three read the same expansion model.
import type { Coordinates, ItineraryGraph, ItineraryNode, NearbyPlace } from './itineraryGraph';
export { dayLabel } from './spatialModel';
import { dayLabel, placeLink, type Figure, type FitNote, type LegExpansion, type LegFacts, type Prefs } from './spatialModel';
import { PinIcon, RouteIcon } from '../home/v2/IconsV2';
import { creditLine, type PlaceMedia } from '../../services/placeMedia';
import { AroundHere } from './AroundHere';
import type { StopSeason } from '../../services/seasonality';

export type SelKind = 'node' | 'edge' | 'nearby' | 'sub' | 'subleg';
export interface Sel { kind: SelKind; id: string }
export interface SpatialCtx {
  graph: ItineraryGraph;
  nodesById: Record<string, ItineraryNode>;
  facts: Record<string, LegFacts>;
  coordinates: Record<string, Coordinates>;
  prefs: Prefs;
  nearby: NearbyPlace[];
  nearbyLoading: boolean;
  nearbyError: string | null;
  expandedNode: string | null;
  expandedEdge: string | null;
  expansion: LegExpansion | null;
  sel: Sel | null;
  onSelect: (kind: SelKind, id: string) => void;
  collapse: () => void;
  /** Credited Wikimedia photos by node / place id, filled in as they arrive. */
  media: Record<string, PlaceMedia>;
  onAddPlace?: (name: string, day: number | null) => void;
  /** Typical-weather badge and tips by stop id (needs the trip's start date). */
  season: Record<string, StopSeason>;
}

export const clean = (text?: string | null) => text?.replace(/<br\s*\/?\s*>/gi, ' · ').replace(/\*\*/g, '')
  .replace(/\s*\|\s*/g, ' ').replace(/^Day\s*\d+\s*/i, '').replace(/\s+/g, ' ').trim() || null;

function Fig({ label, figure, wide }: { label: string; figure: Figure; wide?: boolean }) {
  return <div className={`tv-sd__fig ${wide ? 'is-wide' : ''}`}>
    <span>{label}</span><strong>{figure.value}</strong><small>{figure.estimated ? 'Estimate · ' : ''}{figure.source}</small>
  </div>;
}
function Fit({ notes }: { notes: FitNote[] }) {
  return notes.length ? <ul className="tv-sd__fit" aria-label="Fit with your preferences">
    {notes.map((note) => <li key={note.text} className={`is-${note.tone}`}>{note.text}</li>)}
  </ul> : null;
}
function Bar({ value, max, label }: { value: number | null; max: number; label: string }) {
  return <div className="tv-sd__bar"><span>{label}</span>
    <i><b style={{ transform: `scaleX(${value && max ? Math.max(0.03, value / max) : 0})` }} /></i></div>;
}

const revealed = (event: React.SyntheticEvent<HTMLImageElement>) => event.currentTarget.classList.add('is-loaded');
// An image served from cache can finish before onLoad is attached; reveal it on mount instead.
const revealIfReady = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth) img.classList.add('is-loaded'); };

/* A credited photo; the license requires the credit wherever the photo appears. */
function Photo({ media, alt, pair }: { media?: PlaceMedia; alt: string; pair?: boolean }) {
  if (!media) return null;
  return <figure className={`tv-sd__photo ${pair ? 'is-pair' : ''}`}>
    <img src={media.image} alt={alt} loading="lazy" decoding="async" width={media.width} height={media.height} onLoad={revealed} ref={revealIfReady} />
    <figcaption>{media.credit.file_page
      ? <a href={media.credit.file_page} target="_blank" rel="noopener noreferrer">{creditLine(media)}</a> : creditLine(media)}</figcaption>
  </figure>;
}
// Small list thumbnails are decorative: the row already names the place, and the full credit is on its detail view.
export function Thumb({ media }: { media?: PlaceMedia }) {
  return media ? <img className="tv-sd__thumb" src={media.image} alt="" loading="lazy" decoding="async" onLoad={revealed} ref={revealIfReady} /> : null;
}
function WikiLine({ media }: { media?: PlaceMedia }) {
  if (!media?.description && !media?.article_url) return null;
  return <p className="tv-sd__wiki">{media.description}{media.article_url && <> · <a href={media.article_url} target="_blank" rel="noopener noreferrer">Wikipedia ↗</a></>}</p>;
}

export function SeasonTag({ season }: { season?: StopSeason }) {
  return season ? <b className={`tv-sd-season is-${season.badge.tone}`} title={`${season.label}: about ${Math.round(season.temp_c)} °C`}>{season.badge.text}</b> : null;
}

export function PrefsLine({ prefs }: { prefs: Prefs }) {
  const bits = [prefs.pace && `${prefs.pace} pace`, ...(prefs.interests || []).slice(0, 3), ...(prefs.avoid || []).slice(0, 2).map((item) => `no ${item}`)].filter(Boolean);
  return bits.length ? <div className="tv-sd__prefs"><span>Checked against</span>{bits.map((bit) => <em key={bit as string}>{bit}</em>)}</div> : null;
}

/* ---------- Inspector ---------- */

export function Inspector({ ctx }: { ctx: SpatialCtx }) {
  const { sel, graph, nodesById, facts, nearby, expansion, prefs, onSelect } = ctx;
  if (!sel) {
    const heads = graph.nodes.flatMap((node) => (ctx.season[node.id]?.notes || []).filter((note) => note.tone === 'warn').map((note) => ({ node, note })));
    return <div className="tv-sd__empty"><PinIcon width={20} height={20} /><h3>Pick a stop or a leg.</h3>
      <p>Stops open into nearby places. Legs open into segments with time, distance and cost.</p>
      {!!heads.length && <section className="tv-sd__block tv-sd__heads" aria-label="Season heads-up">
        <h4>Heads-up <span>{heads.some(({ node }) => ctx.season[node.id]?.kind === 'forecast')
          ? heads.every(({ node }) => ctx.season[node.id]?.kind === 'forecast') ? 'forecast' : 'forecast and typical weather'
          : 'typical weather'}</span></h4>
        {heads.map(({ node, note }) => <button type="button" key={`${node.id}${note.text}`} onClick={() => onSelect('node', node.id)}>
          <strong>{node.name}<SeasonTag season={ctx.season[node.id]} /></strong><small>{note.text}</small>
        </button>)}
      </section>}
    </div>;
  }

  if (sel.kind === 'node' && nodesById[sel.id]) {
    const node = nodesById[sel.id];
    const legs = graph.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
    return <article className="tv-sd">
      <span className="tv-sd__eyebrow"><PinIcon width={12} height={12} /> Stop · {dayLabel(node)}</span>
      <Photo media={ctx.media[node.id]} alt={node.name} />
      <h3>{node.name}</h3>
      <WikiLine media={ctx.media[node.id]} />
      {clean(node.evidence) && <p className="tv-sd__lede">{clean(node.evidence)}</p>}
      <div className="tv-sd__figs">
        <Fig label="When" figure={{ value: dayLabel(node), source: node.day_start ? 'From itinerary' : 'Not scheduled' }} />
        <Fig label="Located" figure={ctx.coordinates[node.id] ? { value: `${ctx.coordinates[node.id].lat.toFixed(3)}, ${ctx.coordinates[node.id].lon.toFixed(3)}`, source: 'Geocoded' } : { value: 'Pending', source: 'Locating…' }} />
        {node.kind === 'stop' && <Fig label="Season" wide figure={ctx.season[node.id]
          ? { value: `${ctx.season[node.id].badge.text} · ${Math.round(ctx.season[node.id].temp_c)} °C · ${ctx.season[node.id].rain_mm_day} mm rain${ctx.season[node.id].kind === 'forecast' ? '' : '/day'}`,
              source: `${ctx.season[node.id].label} · ${ctx.season[node.id].source ?? 'NASA POWER climate'}` }
          : !prefs.start_date ? { value: '—', source: 'Add a start date to the trip brief for season tips' }
            : { value: '—', source: node.day_start ? 'Checking typical weather…' : 'Not scheduled' }} />}
      </div>
      <Fit notes={ctx.season[node.id]?.notes || []} />
      <SubNodes ctx={ctx} />
      {ctx.coordinates[node.id] && <AroundHere lat={ctx.coordinates[node.id].lat} lon={ctx.coordinates[node.id].lon}
        day={node.day_start ?? null} onAddPlace={ctx.onAddPlace} />}
      {!!legs.length && <section className="tv-sd__block"><h4>Connections <span>{legs.length}</span></h4>
        {legs.map((edge) => <button type="button" key={edge.id} className="tv-sd__row" onClick={() => onSelect('edge', edge.id)}>
          <RouteIcon width={13} height={13} /><span>{edge.source === node.id ? `To ${nodesById[edge.target]?.name}` : `From ${nodesById[edge.source]?.name}`}</span>
          <small>{facts[edge.id]?.duration.value} · {facts[edge.id]?.distance.value}</small>
        </button>)}
      </section>}
    </article>;
  }

  if (sel.kind === 'edge' && facts[sel.id]) {
    const edge = graph.edges.find((item) => item.id === sel.id)!;
    const f = facts[sel.id];
    const maxKm = Math.max(...Object.values(facts).map((item) => item.km || 0));
    const maxMin = Math.max(...Object.values(facts).map((item) => item.minutes || 0));
    return <article className="tv-sd">
      <span className="tv-sd__eyebrow"><RouteIcon width={12} height={12} /> Leg · {f.mode}</span>
      {(ctx.media[edge.source] || ctx.media[edge.target]) && <div className="tv-sd__pair">
        <Photo pair media={ctx.media[edge.source]} alt={nodesById[edge.source]?.name || 'Start'} />
        <Photo pair media={ctx.media[edge.target]} alt={nodesById[edge.target]?.name || 'End'} />
      </div>}
      <h3>{nodesById[edge.source]?.name} <i aria-hidden="true">→</i> {nodesById[edge.target]?.name}</h3>
      <div className="tv-sd__figs">
        <Fig label="Duration" figure={f.duration} /><Fig label="Distance" figure={f.distance} />
        <Fig label="Cost" figure={f.cost} /><Fig label="Tolls" figure={f.tolls} />
      </div>
      <div className="tv-sd__bars"><Bar label="Longest of trip · time" value={f.minutes} max={maxMin} /><Bar label="Longest of trip · distance" value={f.km} max={maxKm} /></div>
      <Fit notes={f.fit} /><PrefsLine prefs={prefs} />
      {expansion && <Segments ctx={ctx} />}
    </article>;
  }

  if (sel.kind === 'nearby') {
    const place = nearby.find((item) => item.id === sel.id);
    if (!place) return null;
    const link = placeLink(place, prefs);
    return <article className="tv-sd">
      <span className="tv-sd__eyebrow"><PinIcon width={12} height={12} /> Nearby · {place.source === 'google' ? 'Google Maps' : 'From your draft'}</span>
      <Photo media={ctx.media[place.id]} alt={place.name} />
      <h3>{place.name}</h3>
      <WikiLine media={ctx.media[place.id]} />
      <p className="tv-sd__lede">{clean(place.evidence) || place.category}</p>
      <div className="tv-sd__figs"><Fig label="From stop" figure={link.distance} /><Fig label="Getting there" figure={link.duration} /><Fig label="Cost" figure={link.cost} wide /></div>
      <Fit notes={link.fit} />
      <div className="tv-sd__actions">
        {place.maps_url && <a href={place.maps_url} target="_blank" rel="noopener noreferrer">Open in Google Maps ↗</a>}
        {ctx.expandedNode && <button type="button" onClick={() => onSelect('node', ctx.expandedNode!)}>← Back to {nodesById[ctx.expandedNode]?.name}</button>}
      </div>
    </article>;
  }

  if ((sel.kind === 'sub' || sel.kind === 'subleg') && expansion && ctx.expandedEdge) {
    const stop = expansion.stops.find((item) => item.id === sel.id);
    const leg = expansion.legs.find((item) => item.id === sel.id);
    const name = (id: string) => expansion.stops.find((item) => item.id === id)?.name;
    return <article className="tv-sd">
      <span className="tv-sd__eyebrow"><RouteIcon width={12} height={12} /> Segment of {nodesById[graph.edges.find((e) => e.id === ctx.expandedEdge)!.source]?.name} → {nodesById[graph.edges.find((e) => e.id === ctx.expandedEdge)!.target]?.name}</span>
      {stop && <><h3>{stop.name}</h3>
        <div className="tv-sd__figs"><Fig label="Position" figure={{ value: stop.at, source: 'Along the leg', estimated: true }} />
          <Fig label="Coordinates" figure={stop.point ? { value: `${stop.point.lat.toFixed(3)}, ${stop.point.lon.toFixed(3)}`, source: 'On route geometry' } : { value: '—', source: 'Not located' }} /></div>
        {stop.point && <div className="tv-sd__actions"><a href={`https://www.google.com/maps/search/?api=1&query=${stop.point.lat},${stop.point.lon}`} target="_blank" rel="noopener noreferrer">What's here? ↗</a></div>}</>}
      {leg && <><h3>{name(leg.from)} <i aria-hidden="true">→</i> {name(leg.to)}</h3>
        <div className="tv-sd__figs"><Fig label="Duration" figure={{ value: leg.duration, source: 'Pro-rated', estimated: true }} />
          <Fig label="Distance" figure={{ value: leg.distance, source: 'Pro-rated', estimated: true }} />
          <Fig label="Cost share" figure={{ value: leg.cost, source: 'Pro-rated from leg fare', estimated: true }} wide /></div></>}
      <Segments ctx={ctx} />
      <div className="tv-sd__actions"><button type="button" onClick={() => onSelect('edge', ctx.expandedEdge!)}>← Whole leg</button></div>
    </article>;
  }
  return null;
}

/* Sub-nodes of a stop: nearby places, each joined by a sub-edge with its own figures. */
function SubNodes({ ctx }: { ctx: SpatialCtx }) {
  const { nearby, nearbyLoading, nearbyError, prefs, sel, onSelect } = ctx;
  return <section className="tv-sd__block"><h4>Nearby <span>{nearbyLoading ? 'searching…' : nearby.length}</span></h4>
    {nearbyError && <p className="tv-sd__note">{nearbyError}</p>}
    {nearby.length ? <ol className="tv-sd__subs">{nearby.map((place) => {
      const link = placeLink(place, prefs);
      return <li key={place.id}><button type="button" className={sel?.id === place.id ? 'is-active' : ''} onClick={() => onSelect('nearby', place.id)}>
        {ctx.media[place.id] ? <Thumb media={ctx.media[place.id]} /> : <span className="tv-sd__knot" />}<strong>{place.name}</strong>
        <small>{[link.distance.value, link.duration.value, link.cost.value !== 'Not quoted' && link.cost.value].filter((v) => v && v !== '—').join(' · ') || place.category}</small>
        {!!link.fit.length && <em>fits</em>}
      </button></li>;
    })}</ol> : <p className="tv-sd__note">{nearbyLoading ? 'Finding nearby places…' : 'No nearby places for this stop yet.'}</p>}
  </section>;
}

/* Sub-edges of a leg: a chain of waypoints with the segment figures between them. */
export function Segments({ ctx }: { ctx: SpatialCtx }) {
  const { expansion, sel, onSelect } = ctx;
  if (!expansion) return null;
  return <section className="tv-sd__block"><h4>Segments <span>{expansion.legs.length}</span></h4>
    <ol className="tv-sd__chain">{expansion.stops.map((stop, i) => <li key={stop.id}>
      <button type="button" className={`tv-sd__chain-stop is-${stop.role} ${sel?.id === stop.id ? 'is-active' : ''}`}
        onClick={() => stop.role === 'waypoint' ? onSelect('sub', stop.id) : onSelect('edge', ctx.expandedEdge!)}>
        <span className="tv-sd__knot" /><strong>{stop.name}</strong><small>{stop.at}</small>
      </button>
      {expansion.legs[i] && <button type="button" className={`tv-sd__chain-leg ${sel?.id === expansion.legs[i].id ? 'is-active' : ''}`} onClick={() => onSelect('subleg', expansion.legs[i].id)}>
        <span>{expansion.legs[i].duration}</span><span>{expansion.legs[i].distance}</span>{expansion.legs[i].cost !== '—' && <span>{expansion.legs[i].cost}</span>}
      </button>}
    </li>)}</ol>
  </section>;
}

/* ---------- Route tree (outline) ---------- */

export function RouteTree({ ctx }: { ctx: SpatialCtx }) {
  const { graph, facts, nodesById, expandedNode, expandedEdge, sel, onSelect, collapse } = ctx;
  return <nav className="tv-sd-tree" aria-label="Route outline">
    {graph.nodes.map((node, index) => {
      const out = graph.edges.filter((edge) => edge.source === node.id);
      const open = expandedNode === node.id;
      return <div key={node.id} className="tv-sd-tree__group">
        <button type="button" aria-expanded={open} className={`tv-sd-tree__node ${sel?.id === node.id ? 'is-active' : ''}`}
          onClick={() => open ? collapse() : onSelect('node', node.id)}>
          <span className="tv-sd-tree__caret" aria-hidden="true" /><em>{String(index + 1).padStart(2, '0')}</em>
          <strong>{node.name}</strong><small>{dayLabel(node)} <SeasonTag season={ctx.season[node.id]} /></small>
        </button>
        {open && <div className="tv-sd-tree__kids">{ctx.nearby.length ? ctx.nearby.map((place) =>
          <button type="button" key={place.id} className={sel?.id === place.id ? 'is-active' : ''} onClick={() => onSelect('nearby', place.id)}>
            <span className="tv-sd__knot" />{place.name}<small>{place.distance_km !== undefined ? `${place.distance_km} km` : ''}</small>
          </button>) : <p>{ctx.nearbyLoading ? 'Searching nearby…' : 'No nearby places'}</p>}</div>}
        {out.map((edge) => {
          const f = facts[edge.id]; const legOpen = expandedEdge === edge.id;
          return <div key={edge.id}>
            <button type="button" aria-expanded={legOpen} className={`tv-sd-tree__leg ${sel?.id === edge.id ? 'is-active' : ''}`}
              onClick={() => legOpen ? collapse() : onSelect('edge', edge.id)}>
              <span className="tv-sd-tree__caret" aria-hidden="true" /><span>{f?.mode}</span>
              <small>{f?.duration.value} · {f?.distance.value}{f?.cost.value !== 'Not quoted' ? ` · ${f?.cost.value}` : ''}</small>
              {f?.fit.some((note) => note.tone === 'warn') && <i title="Preference warning">!</i>}
            </button>
            {legOpen && ctx.expansion && <div className="tv-sd-tree__kids">{ctx.expansion.legs.map((leg) =>
              <button type="button" key={leg.id} className={sel?.id === leg.id ? 'is-active' : ''} onClick={() => onSelect('subleg', leg.id)}>
                <span className="tv-sd__knot is-way" />{ctx.expansion!.stops.find((s) => s.id === leg.to)?.name.replace(nodesById[edge.target]?.name || '', 'Arrive')}
                <small>{leg.duration} · {leg.distance}</small>
              </button>)}</div>}
          </div>;
        })}
      </div>;
    })}
  </nav>;
}

/* ---------- Journal timeline ---------- */

export function Journal({ ctx }: { ctx: SpatialCtx }) {
  const { graph, facts, expandedNode, expandedEdge, onSelect, collapse } = ctx;
  const maxMin = Math.max(1, ...Object.values(facts).map((item) => item.minutes || 0));
  return <ol className="tv-sd-journal">
    {graph.nodes.map((node, index) => {
      const out = graph.edges.filter((edge) => edge.source === node.id);
      return <li key={node.id}>
        <section className={`tv-sd-journal__stop ${expandedNode === node.id ? 'is-open' : ''}`}>
          <button type="button" aria-expanded={expandedNode === node.id} onClick={() => expandedNode === node.id ? collapse() : onSelect('node', node.id)}>
            <em>{String(index + 1).padStart(2, '0')}</em>
            <span><small>{dayLabel(node)} <SeasonTag season={ctx.season[node.id]} /></small><strong>{node.name}</strong>{clean(node.evidence) && <p>{clean(node.evidence)}</p>}</span>
            <Thumb media={ctx.media[node.id]} />
          </button>
          {expandedNode === node.id && <div className="tv-sd-journal__open"><Inspector ctx={{ ...ctx, sel: ctx.sel?.kind === 'nearby' ? ctx.sel : { kind: 'node', id: node.id } }} /></div>}
        </section>
        {out.map((edge) => {
          const f = facts[edge.id];
          const open = expandedEdge === edge.id;
          return <section key={edge.id} className={`tv-sd-journal__leg ${open ? 'is-open' : ''}`}>
            <button type="button" aria-expanded={open} onClick={() => open ? collapse() : onSelect('edge', edge.id)}>
              <span className={`tv-sd-journal__rail is-${f?.kind}`} aria-hidden="true" />
              <span className="tv-sd-journal__metrics"><b>{f?.mode}</b><span>{f?.duration.value}</span><span>{f?.distance.value}</span><span>{f?.cost.value}</span></span>
              <i className="tv-sd-journal__len" aria-hidden="true"><b style={{ transform: `scaleX(${(f?.minutes || 0) / maxMin})` }} /></i>
            </button>
            {open && <div className="tv-sd-journal__open"><Inspector ctx={{ ...ctx, sel: ctx.sel && ctx.sel.kind !== 'node' && ctx.sel.kind !== 'nearby' ? ctx.sel : { kind: 'edge', id: edge.id } }} /></div>}
          </section>;
        })}
      </li>;
    })}
  </ol>;
}

/* ---------- Route strip (atlas) ---------- */

export function RouteStrip({ ctx }: { ctx: SpatialCtx }) {
  const { graph, facts, sel, onSelect } = ctx;
  return <nav className="tv-sd-strip" aria-label="Route stops and legs">
    {graph.nodes.map((node, index) => {
      const edge = graph.edges.find((item) => item.source === node.id);
      return <div key={node.id} className="tv-sd-strip__seg">
        <button type="button" className={`tv-sd-strip__stop ${sel?.id === node.id || ctx.expandedNode === node.id ? 'is-active' : ''}`} onClick={() => onSelect('node', node.id)}>
          {ctx.media[node.id] ? <Thumb media={ctx.media[node.id]} /> : <em>{String(index + 1).padStart(2, '0')}</em>}<strong>{node.name}</strong><small>{dayLabel(node)} <SeasonTag season={ctx.season[node.id]} /></small>
        </button>
        {edge && facts[edge.id] && <button type="button" className={`tv-sd-strip__leg ${sel?.id === edge.id || ctx.expandedEdge === edge.id ? 'is-active' : ''}`} onClick={() => onSelect('edge', edge.id)}>
          <span>{facts[edge.id].duration.value}</span><small>{facts[edge.id].distance.value}</small>
        </button>}
      </div>;
    })}
  </nav>;
}
