import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CurrentTripContext } from './CurrentTrip';
import { airDistanceKm, type Coordinates, type ItineraryGraph, type ItineraryNode, type NearbyPlace, type RoadMetric } from './itineraryGraph';
import { expandLeg, formatMinutes, legFacts, stopMonth, type Prefs } from './spatialModel';
import { Inspector, Journal, PrefsLine, RouteStrip, RouteTree, type Sel, type SelKind, type SpatialCtx } from './SpatialDetail';
import type { GraphLayout } from './TripGraph3D';
import type { MapSkin } from './GoogleTripMap';
import { getNearbyPlaces, getRoadMetrics, getTripGeocodes, type TripDocumentItem } from '../../services/tripService';
import { placeKey, routeChain, scheduleDay, type DayRouteView, type PlaybackFrame } from '../../lib/dayRoute';
import { usePlaceMedia, type MediaTarget } from '../../services/placeMedia';
import { useSeason, type SeasonTarget, type StopSeason } from '../../services/seasonality';
import type { TripEnrichment } from '../../services/tripService';
import { useStopCoordinates } from './stopLocations';
import { useSpatialPanels, type PanelKey } from './useSpatialPanels';
import { CloseIcon, RouteIcon } from '../home/v2/IconsV2';
import { GuideCharacter } from '../guide/GuideCharacter';
import { useGuide } from '../guide/GuideContext';
import type { Guide } from '../guide/guides';

const TripGraph3D = React.lazy(() => import('./TripGraph3D').then((module) => ({ default: module.TripGraph3D })));
const TripRouteMap = React.lazy(() => import('./TripRouteMap').then((module) => ({ default: module.TripRouteMap })));
const GoogleTripMap = React.lazy(() => import('./GoogleTripMap').then((module) => ({ default: module.GoogleTripMap })));
const hasGoogleMap = Boolean(import.meta.env.VITE_GOOGLE_MAPS_API_KEY);

type Variant = 'atlas' | 'outline' | 'journal';
const VARIANTS: { id: Variant; name: string; note: string; layout: GraphLayout; skin: MapSkin }[] = [
  { id: 'atlas', name: 'Atlas', note: 'Full-bleed stage, floating detail sheet', layout: 'geo', skin: 'atlas' },
  { id: 'outline', name: 'Outline', note: 'Route tree · stage · inspector', layout: 'line', skin: 'paper' },
  { id: 'journal', name: 'Journal', note: 'Stage over an expandable timeline', layout: 'helix', skin: 'atlas' },
];

interface Props {
  isOpen: boolean;
  /** Whose face rides the map, the 3D graph and the corner of the stage. Defaults to the chat's guide. */
  guide?: Guide;
  onClose?: () => void;
  /** Fill the parent (Trip Studio) instead of docking beside the chat: no scrim, grip or close. */
  embedded?: boolean;
  /** Controlled view; when set, the host's tabs drive it and the internal switch hides. */
  mode?: 'graph' | 'map';
  onModeChange?: (mode: 'graph' | 'map') => void;
  /** Controlled day (Trip Studio day rail). null = overview; undefined = not synced. */
  selectedDay?: number | null;
  onSelectDay?: (day: number | null) => void;
  trip?: CurrentTripContext | null;
  tripId?: string;
  graph?: ItineraryGraph | null;
  preferences?: Prefs | null;
  dark?: boolean;
  /** Shows "Add to day" on Around-here places; the host sends it to the agent (COPILOT_OPS add). */
  onAddPlace?: (name: string, day: number | null) => void;
  /** The trip document's enrichment.weather; its forecast days override the climate season figures. */
  weather?: TripEnrichment['weather'] | null;
  /** A stop the day-playback bar is on. The map selects it when the name matches. */
  focusName?: string | null;
  /** The open day's places. The map draws these in visit order instead of the city graph. */
  dayPlan?: { base: string; items: TripDocumentItem[] } | null;
  playback?: PlaybackFrame | null;
  onPickStop?: (name: string) => void;
}

const read = (key: string) => { try { return window.localStorage.getItem(key); } catch { return null; } };
const write = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* storage blocked */ } };
// The panel docks beside the chat; the chat always keeps at least CHAT_MIN px.
const PANEL_MIN = 380;
const CHAT_MIN = 360;
const maxWidth = () => Math.max(PANEL_MIN, Math.min(window.innerWidth - CHAT_MIN, window.innerWidth * 0.8));
const clampWidth = (width: number) => Math.round(Math.min(Math.max(PANEL_MIN, width), maxWidth()));
const coversDay = (node: ItineraryNode, day: number | null | undefined) => day != null && node.day_start != null
  && day >= node.day_start && day <= (node.day_end ?? node.day_start);
// Preview graphs stream in several times per turn; legs whose endpoints did not move reuse their metrics.
const roadCache = new Map<string, RoadMetric>();
const legKey = (a: Coordinates, b: Coordinates) => `${a.lat},${a.lon}>${b.lat},${b.lon}`;
// Keyed by name, not list position, so adding a place in chat does not shift every other place's id.
const placeId = (nodeId: string, name: string) => `draft-${nodeId}-${name.toLowerCase().replace(/\W+/g, '-')}`;
export const SpatialWorkspace: React.FC<Props> = ({ isOpen, guide: guideProp, onClose, embedded = false, mode: modeProp, onModeChange, selectedDay, onSelectDay, trip, tripId, graph, preferences, dark = false, onAddPlace, weather, focusName, dayPlan = null, playback = null, onPickStop }) => {
  const contextGuide = useGuide();
  const guide = guideProp ?? contextGuide;
  const [said, setSaid] = useState<string | null>(null);
  const [variant, setVariant] = useState<Variant>(() => VARIANTS.find((item) => item.id === read('tripverse-spatial-variant'))?.id || 'atlas');
  const [innerMode, setInnerMode] = useState<'graph' | 'map'>(() => read('tripverse-spatial-mode') === 'map' ? 'map' : 'graph');
  const mode = modeProp ?? innerMode;
  const panels = useSpatialPanels(variant);
  const [sel, setSel] = useState<Sel | null>(null);
  const [expandedNode, setExpandedNode] = useState<string | null>(null);
  const [expandedEdge, setExpandedEdge] = useState<string | null>(null);
  const [roadMetrics, setRoadMetrics] = useState<Record<string, RoadMetric>>({});
  const [googleNearby, setGoogleNearby] = useState<Record<string, NearbyPlace[]>>({});
  const [draftNearbyCoordinates, setDraftNearbyCoordinates] = useState<Record<string, Coordinates>>({});
  const [dayCoords, setDayCoords] = useState<Record<string, Coordinates>>({});
  const [dayRoads, setDayRoads] = useState<Record<string, RoadMetric>>({});
  const attemptedDay = useRef(new Set<string>());
  const [nearbyLoading, setNearbyLoading] = useState(false);
  const [nearbyError, setNearbyError] = useState<string | null>(null);
  const attemptedNearbyGeocodes = useRef(new Set<string>());
  const [width, setWidth] = useState(() => clampWidth(Number(read('tripverse-spatial-width')) || window.innerWidth * 0.5));
  const panel = useRef<HTMLElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const commitWidth = (next: number) => {
    const value = clampWidth(next);
    setWidth(value);
    panel.current?.style.setProperty('--sv-w', `${value}px`);
    write('tripverse-spatial-width', String(value));
  };
  useEffect(() => {
    const onResize = () => setWidth((current) => clampWidth(current));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const prefs = useMemo<Prefs>(() => ({ ...preferences, interests: preferences?.interests?.length ? preferences.interests : trip?.interests }), [preferences, trip?.interests]);

  const nodesById = useMemo(() => Object.fromEntries((graph?.nodes || []).map((node) => [node.id, node])), [graph]);
  useEffect(() => {
    if (dayPlan) return;
    const needle = focusName?.trim().toLowerCase();
    if (!needle || !graph) return;
    for (const node of graph.nodes) {
      if (node.name.toLowerCase() === needle) {
        setSel({ kind: 'node', id: node.id });
        setExpandedNode(node.id);
        setExpandedEdge(null);
        return;
      }
      const hit = (node.nearby_places || []).find((place) => place.name.toLowerCase() === needle);
      if (hit) {
        setSel({ kind: 'nearby', id: `draft-${node.id}-${hit.name.toLowerCase().replace(/\W+/g, '-')}` });
        setExpandedNode(node.id);
        setExpandedEdge(null);
        return;
      }
    }
  }, [focusName, graph, dayPlan]);
  // Latest day props in a ref so onSelect stays stable (the map rebuilds overlays when it changes).
  const daySync = useRef({ selectedDay, onSelectDay, nodesById });
  daySync.current = { selectedDay, onSelectDay, nodesById };
  const onSelect = useCallback((kind: SelKind, id: string) => {
    setSel({ kind, id });
    if (kind === 'node') {
      setExpandedNode(id); setExpandedEdge(null);
      // Report a day only when the host's day falls outside this stop, so D2 inside a D1–3 stop doesn't jump to D1.
      const node = daySync.current.nodesById[id];
      if (node?.day_start != null && !coversDay(node, daySync.current.selectedDay)) daySync.current.onSelectDay?.(node.day_start);
    }
    if (kind === 'edge') { setExpandedEdge(id); setExpandedNode(null); }
  }, []);
  const collapse = useCallback(() => {
    setExpandedNode(null); setExpandedEdge(null); setSel(null);
    if (daySync.current.selectedDay != null) daySync.current.onSelectDay?.(null);
  }, []);
  // Host picked a day (or Overview): open that day's stop. Reacts to the prop only, not to clicks inside.
  // Streamed preview graphs re-run this; apply only when the day or its stop actually changes.
  const appliedDay = useRef<string | null>(null);
  useEffect(() => {
    if (selectedDay === undefined || !graph) return;
    if (dayPlan) {
      const routeKey = `route:${selectedDay}`;
      if (appliedDay.current === routeKey) return;
      appliedDay.current = routeKey;
      setExpandedNode(null); setExpandedEdge(null); setSel(null);
      return;
    }
    const node = selectedDay === null ? null : graph.nodes.find((item) => coversDay(item, selectedDay));
    const key = `${selectedDay}:${node?.id ?? ''}`;
    if (appliedDay.current === key) return;
    appliedDay.current = key;
    if (selectedDay === null) { setExpandedNode(null); setExpandedEdge(null); setSel(null); return; }
    if (node) { setSel({ kind: 'node', id: node.id }); setExpandedNode(node.id); setExpandedEdge(null); }
  }, [selectedDay, graph, dayPlan]);
  const pickVariant = (next: Variant) => { setVariant(next); write('tripverse-spatial-variant', next); };
  const pickMode = (next: 'graph' | 'map') => { setInnerMode(next); write('tripverse-spatial-mode', next); onModeChange?.(next); };

  useEffect(() => {
    attemptedNearbyGeocodes.current.clear();
    setSel(null); setExpandedNode(null); setExpandedEdge(null);
    setRoadMetrics({}); setGoogleNearby({}); setDraftNearbyCoordinates({}); setNearbyError(null);
  }, [tripId]);
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (expandedNode || expandedEdge) collapse(); else onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose, collapse, expandedNode, expandedEdge]);

  const { coordinates, locating: geocoding } = useStopCoordinates(isOpen ? graph : null, trip?.destination, tripId);

  useEffect(() => {
    if (!isOpen || !graph || !tripId || Object.keys(coordinates).length < 2) return;
    const legs = graph.edges.flatMap((edge) => {
      if (edge.mode && /flight|plane|rail|train|shinkansen|metro|bus|ferry|walk|cycle/i.test(edge.mode)
        && !/car|taxi|drive|road/i.test(edge.mode)) return [];
      const from = coordinates[edge.source]; const to = coordinates[edge.target];
      if (!from || !to || (!edge.mode && airDistanceKm(from, to) > 700)) return [];
      return [{ id: edge.id, from, to }];
    });
    const fromCache = () => Object.fromEntries(legs.flatMap((leg) => {
      const hit = roadCache.get(legKey(leg.from, leg.to));
      return hit ? [[leg.id, { ...hit, id: leg.id }]] : [];
    }));
    const missing = legs.filter((leg) => !roadCache.has(legKey(leg.from, leg.to)));
    setRoadMetrics(fromCache());
    if (!missing.length) return;
    let active = true;
    void getRoadMetrics(tripId, missing).then((rows) => {
      rows.forEach((row) => { const leg = missing.find((item) => item.id === row.id); if (leg) roadCache.set(legKey(leg.from, leg.to), row); });
      if (active) setRoadMetrics(fromCache());
    });
    return () => { active = false; };
  }, [isOpen, graph, tripId, coordinates]);

  useEffect(() => {
    const point = expandedNode && coordinates[expandedNode];
    if (!isOpen || !tripId || !expandedNode || !point || googleNearby[expandedNode]) return;
    let active = true;
    setNearbyLoading(true);
    setNearbyError(null);
    void getNearbyPlaces(tripId, point).then((places) => {
      if (active) setGoogleNearby((previous) => ({ ...previous, [expandedNode]: places }));
    }).catch((error) => { if (active) { setGoogleNearby((previous) => ({ ...previous, [expandedNode]: [] })); setNearbyError(error instanceof Error ? error.message : 'Live nearby search is unavailable.'); } })
      .finally(() => { if (active) setNearbyLoading(false); });
    return () => { active = false; };
  }, [isOpen, tripId, expandedNode, coordinates, googleNearby]);

  // Locate every stop's planned places, not just the open stop's, so each one can be pinned
  // on the map the moment the chat adds it.
  useEffect(() => {
    if (!isOpen || !tripId || !graph) return;
    const targets = graph.nodes.flatMap((node) => (node.nearby_places || []).slice(0, 8).map((place) => ({
      id: placeId(node.id, place.name), name: place.name, is_origin: false,
    }))).filter((place) => !draftNearbyCoordinates[place.id] && !attemptedNearbyGeocodes.current.has(place.id));
    if (!targets.length) return;
    targets.forEach((place) => attemptedNearbyGeocodes.current.add(place.id));
    let active = true;
    void getTripGeocodes(tripId, targets).then((located) => {
      if (active) setDraftNearbyCoordinates((previous) => ({ ...previous, ...located }));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [isOpen, tripId, graph, draftNearbyCoordinates]);

  const nearby = useMemo(() => {
    const itineraryNearby: NearbyPlace[] = (expandedNode && nodesById[expandedNode]?.nearby_places || []).map((place) => ({
      id: placeId(expandedNode!, place.name), name: place.name, category: place.category,
      evidence: place.evidence, source: 'itinerary',
      lat: draftNearbyCoordinates[placeId(expandedNode!, place.name)]?.lat,
      lon: draftNearbyCoordinates[placeId(expandedNode!, place.name)]?.lon,
    }));
    const googlePlaces = expandedNode ? googleNearby[expandedNode] || [] : [];
    const parent = expandedNode ? coordinates[expandedNode] : undefined;
    return [...itineraryNearby.map((place) => {
      const match = googlePlaces.find((other) => other.name.toLowerCase() === place.name.toLowerCase());
      const merged = match ? { ...place, lat: place.lat ?? match.lat, lon: place.lon ?? match.lon, distance_km: match.distance_km, maps_url: match.maps_url, price_level: match.price_level } : place;
      return merged.distance_km === undefined && parent && merged.lat !== undefined && merged.lon !== undefined
        ? { ...merged, distance_km: airDistanceKm(parent, { lat: merged.lat, lon: merged.lon }) } : merged;
    }), ...googlePlaces.filter((place) => !itineraryNearby.some((other) => other.name.toLowerCase() === place.name.toLowerCase()))];
  }, [expandedNode, nodesById, draftNearbyCoordinates, googleNearby, coordinates]);

  // Every stop's located planned places, for pinning on the map without opening the stop.
  const planned = useMemo(() => (graph?.nodes || []).flatMap((node) => (node.nearby_places || []).flatMap((place) => {
    const at = draftNearbyCoordinates[placeId(node.id, place.name)];
    return at ? [{ id: placeId(node.id, place.name), parent: node.id, name: place.name, lat: at.lat, lon: at.lon }] : [];
  })), [graph, draftNearbyCoordinates]);

  const dayChain = useMemo(() => (dayPlan ? routeChain(scheduleDay(dayPlan.items)) : []), [dayPlan]);
  const dayCoordsRef = useRef(dayCoords);
  dayCoordsRef.current = dayCoords;
  useEffect(() => {
    attemptedDay.current.clear();
    dayCoordsRef.current = {};
    setDayCoords({});
    setDayRoads({});
  }, [dayPlan?.base, tripId]);
  useEffect(() => {
    if (!isOpen || !tripId || !dayPlan) return;
    const targets = [...new Set(dayChain.map((stop) => stop.name))].map((name) => ({
      id: placeKey(name), name: `${name}, ${dayPlan.base}`, is_origin: false,
    })).filter((place) => !dayCoordsRef.current[place.id] && !attemptedDay.current.has(place.id));
    if (!targets.length) return;
    targets.forEach((place) => attemptedDay.current.add(place.id));
    let active = true;
    void getTripGeocodes(tripId, targets).then((located) => {
      if (active) setDayCoords((previous) => ({ ...previous, ...located }));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [isOpen, tripId, dayPlan, dayChain]);
  const locatedChain = useMemo(() => dayChain.flatMap((stop) => {
    const known = dayCoords[placeKey(stop.name)];
    const plannedHit = planned.find((place) => place.name.toLowerCase() === stop.name.toLowerCase());
    const at = known ?? (plannedHit ? { lat: plannedHit.lat, lon: plannedHit.lon } : null);
    return at ? [{ id: stop.key, name: stop.name, lat: at.lat, lon: at.lon, category: stop.category, start: stop.startMin, end: stop.endMin }] : [];
  }), [dayChain, dayCoords, planned]);
  useEffect(() => {
    if (!isOpen || !tripId || locatedChain.length < 2) return;
    const pairs: { id: string; from: Coordinates; to: Coordinates }[] = [];
    const fromCache = () => {
      const next: Record<string, RoadMetric> = {};
      for (let index = 1; index < locatedChain.length; index += 1) {
        const from = { lat: locatedChain[index - 1].lat, lon: locatedChain[index - 1].lon };
        const to = { lat: locatedChain[index].lat, lon: locatedChain[index].lon };
        const id = `${locatedChain[index - 1].id}>${locatedChain[index].id}`;
        const hit = roadCache.get(legKey(from, to));
        if (hit) next[id] = { ...hit, id };
        else if (!pairs.find((pair) => pair.id === id)) pairs.push({ id, from, to });
      }
      return next;
    };
    const cached = fromCache();
    setDayRoads(cached);
    if (!pairs.length) return;
    let active = true;
    void getRoadMetrics(tripId, pairs).then((rows) => {
      rows.forEach((row) => {
        const pair = pairs.find((item) => item.id === row.id);
        if (pair) roadCache.set(legKey(pair.from, pair.to), row);
      });
      if (active) setDayRoads(fromCache());
    });
    return () => { active = false; };
  }, [isOpen, tripId, locatedChain]);
  const dayRoute = useMemo<DayRouteView | null>(() => {
    if (!dayPlan || !locatedChain.length) return null;
    const legs = [];
    for (let index = 1; index < locatedChain.length; index += 1) {
      const id = `${locatedChain[index - 1].id}>${locatedChain[index].id}`;
      const metric = dayRoads[id];
      const path = metric?.geometry?.length
        ? metric.geometry.map(([lng, lat]) => ({ lat, lng }))
        : [
          { lat: locatedChain[index - 1].lat, lng: locatedChain[index - 1].lon },
          { lat: locatedChain[index].lat, lng: locatedChain[index].lon },
        ];
      legs.push({ fromId: locatedChain[index - 1].id, toId: locatedChain[index].id, path });
    }
    return { points: locatedChain, legs };
  }, [dayPlan, locatedChain, dayRoads]);

  // Photos for everything the views show. Stops wait for their coordinates so the server's
  // distance check can reject namesakes; nearby places without their own point borrow the stop's.
  const mediaTargets = useMemo<MediaTarget[]>(() => {
    const stops = (graph?.nodes || []).flatMap((node) => coordinates[node.id]
      ? [{ id: node.id, name: node.name, lat: coordinates[node.id].lat, lon: coordinates[node.id].lon }] : []);
    const home = expandedNode ? coordinates[expandedNode] : undefined;
    const around = nearby.flatMap((place) => place.lat !== undefined && place.lon !== undefined
      ? [{ id: place.id, name: place.name, lat: place.lat, lon: place.lon }]
      : home ? [{ id: place.id, name: place.name, lat: home.lat, lon: home.lon }] : []);
    return [...stops, ...around, ...planned.map(({ id, name, lat, lon }) => ({ id, name, lat, lon }))];
  }, [graph, coordinates, nearby, expandedNode, planned]);
  const media = usePlaceMedia(mediaTargets);

  // Season tips need a located stop and a date: the trip's start date plus the stop's day.
  const seasonTargets = useMemo<SeasonTarget[]>(() => (graph?.nodes || []).flatMap((node) => {
    const month = stopMonth(node, prefs.start_date);
    const point = coordinates[node.id];
    return month && point ? [{ id: node.id, lat: Number(point.lat.toFixed(2)), lon: Number(point.lon.toFixed(2)), month,
      places: (node.nearby_places || []).map(({ name, category }) => ({ name, category })) }] : [];
  }), [graph, coordinates, prefs.start_date]);
  const climate = useSeason(seasonTargets);
  // Inside the forecast window the trip document's forecast (MET Norway, Session 5) replaces the climate
  // figure for a stop: its first forecast day, labelled as a forecast. Other stops keep "Typical for <month>".
  const season = useMemo<Record<string, StopSeason>>(() => {
    const forecasts = (weather || []).filter((entry) => entry.kind === 'forecast' && entry.temp_mean !== null);
    if (!forecasts.length || !graph) return climate;
    const merged = { ...climate };
    for (const node of graph.nodes) {
      const hit = forecasts.find((entry) => coversDay(node, entry.day));
      if (!hit) continue;
      const when = new Date(`${hit.date}T00:00`);
      merged[node.id] = {
        id: node.id, month: when.getMonth() + 1, kind: 'forecast', source: 'MET Norway',
        label: `Forecast for ${when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}`,
        temp_c: hit.temp_mean!, rain_mm_day: hit.rain_mm ?? 0,
        badge: hit.badge ?? { tone: 'info', text: 'Forecast' }, notes: hit.notes,
      };
    }
    return merged;
  }, [climate, weather, graph]);

  const facts = useMemo(() => Object.fromEntries((graph?.edges || []).map((edge) => {
    const from = coordinates[edge.source]; const to = coordinates[edge.target];
    return [edge.id, legFacts(edge, roadMetrics[edge.id], from && to ? airDistanceKm(from, to) : null, prefs)];
  })), [graph, coordinates, roadMetrics, prefs]);
  const expansion = useMemo(() => {
    const edge = expandedEdge && graph?.edges.find((item) => item.id === expandedEdge);
    if (!edge || !facts[edge.id]) return null;
    return expandLeg(edge, facts[edge.id], [nodesById[edge.source]?.name || 'Start', nodesById[edge.target]?.name || 'End'],
      coordinates[edge.source], coordinates[edge.target], roadMetrics[edge.id]);
  }, [expandedEdge, graph, facts, nodesById, coordinates, roadMetrics]);

  if (!isOpen) return null;
  const hide = (key: PanelKey, label: string) => <button type="button" className="tv-sv__hide" onClick={() => panels.toggle(key, false)}
    aria-label={`Hide ${label.toLowerCase()}`} title={`Hide ${label.toLowerCase()}`}><CloseIcon width={12} height={12} /></button>;
  const move = (key: PanelKey, label: string) => <button {...panels.gripProps(key, label.toLowerCase())}><i aria-hidden="true" /></button>;
  const hiddenClasses = panels.list.filter(({ key }) => !panels.shown(key)).map(({ key }) => `hide-${key}`).join(' ');
  const spec = VARIANTS.find((item) => item.id === variant) || VARIANTS[0];
  const ctx: SpatialCtx | null = graph ? { graph, nodesById, facts, coordinates, prefs, nearby, nearbyLoading, nearbyError,
    expandedNode, expandedEdge, expansion, sel, onSelect, collapse, media, onAddPlace, season } : null;
  const totals = Object.values(facts).reduce((sum, f) => ({ km: sum.km + (f.km || 0), min: sum.min + (f.minutes || 0) }), { km: 0, min: 0 });
  const night = variant === 'atlas' || dark;

  const stage = graph && <div className="tv-sv__stage">
    <React.Suspense fallback={<div className="tv-spatial__loading">Loading route view…</div>}>
      {mode === 'graph'
        ? <TripGraph3D guideImage={guide.image} graph={graph} layout={spec.layout} night={night} coordinates={coordinates} facts={facts} nearby={nearby}
          expandedNode={expandedNode} expandedEdge={expandedEdge} expansion={expansion} selected={sel?.id || null} onSelect={onSelect} />
        : hasGoogleMap
          ? <GoogleTripMap guideImage={guide.image} graph={graph} skin={dark ? 'night' : spec.skin} coordinates={coordinates} roadMetrics={roadMetrics} facts={facts} nearby={nearby}
            expandedNode={expandedNode} expandedEdge={expandedEdge} expansion={expansion} selected={sel?.id || null} onSelect={onSelect}
            inset={variant === 'atlas' ? { right: panels.shown('sheet') ? 400 : 0, bottom: panels.shown('strip') ? 110 : 0 } : undefined} planned={planned} media={media}
            dayRoute={dayRoute} playback={playback} onPickStop={onPickStop} />
          : <TripRouteMap guideImage={guide.image} graph={graph} coordinates={coordinates} roadMetrics={roadMetrics} nearby={nearby} focusedNode={expandedNode}
            selectedNode={sel?.kind === 'node' ? sel.id : null} selectedEdge={sel?.kind === 'edge' ? sel.id : null}
            onSelectNode={(id) => onSelect('node', id)} onSelectEdge={(id) => onSelect('edge', id)} onSelectNearby={(id) => onSelect('nearby', id)}
            dayRoute={dayRoute} playback={playback} onPickStop={onPickStop} />}
    </React.Suspense>
    {(expandedNode || expandedEdge) && <button type="button" className="tv-sv__collapse" onClick={collapse}>← Whole route <kbd>Esc</kbd></button>}
    <span className="tv-sv__status">{dayPlan && !dayRoute ? 'Locating this day’s stops…' : geocoding ? 'Locating stops…' : `${Object.keys(coordinates).length}/${graph.nodes.length} located`}</span>
    <div className="tv-sv__guide">
      {said && <p className="tv-say tv-sv__say" role="status">{said}</p>}
      <GuideCharacter guide={guide} size={44} interactive mood={geocoding ? 'thinking' : 'idle'} label={`${guide.name}, your guide. Press for a note about this route.`}
        onPoke={() => {
          const at = expandedNode && nodesById[expandedNode];
          setSaid(at ? `${at.name}: ask me for a café, a walk or a rainy-day swap.` : `${graph.nodes.length} stops, ${Math.round(totals.km) || '—'} km. Tap a stop and I’ll zoom in.`);
          window.setTimeout(() => setSaid(null), 3800);
        }} />
    </div>
  </div>;

  const summary = <div className="tv-sv__summary">
    <div><strong>{graph?.nodes.length || 0}</strong><span>stops</span></div>
    <div><strong>{graph?.edges.length || 0}</strong><span>legs</span></div>
    <div><strong>{totals.km ? Math.round(totals.km).toLocaleString() : '—'}</strong><span>km</span></div>
    <div><strong>{totals.min ? formatMinutes(totals.min) : '—'}</strong><span>moving</span></div>
  </div>;

  return <>
    {!embedded && <button type="button" className="tv-spatial__scrim" aria-label="Close spatial view" onClick={onClose} />}
    <aside ref={panel} className={`tv-sv is-${variant} ${night ? 'is-night' : ''} ${embedded ? 'is-embedded' : ''} ${hiddenClasses}`} aria-label="Spatial trip workspace"
      style={{ '--sv-w': `${width}px` } as React.CSSProperties}>
      {/* Drag writes the CSS variable directly; React state commits once on release. */}
      {!embedded && <div className="tv-sv__grip" role="separator" tabIndex={0} aria-orientation="vertical" aria-label="Resize route panel"
        aria-valuemin={PANEL_MIN} aria-valuemax={Math.round(maxWidth())} aria-valuenow={width} title="Drag to resize · double-click to toggle size"
        onPointerDown={(event) => { drag.current = { x: event.clientX, width }; event.currentTarget.setPointerCapture(event.pointerId); panel.current?.classList.add('is-resizing'); }}
        onPointerMove={(event) => { if (drag.current) panel.current?.style.setProperty('--sv-w', `${clampWidth(drag.current.width + drag.current.x - event.clientX)}px`); }}
        onPointerUp={(event) => { if (!drag.current) return; commitWidth(drag.current.width + drag.current.x - event.clientX); drag.current = null; panel.current?.classList.remove('is-resizing'); }}
        onPointerCancel={() => { if (drag.current) commitWidth(drag.current.width); drag.current = null; panel.current?.classList.remove('is-resizing'); }}
        onDoubleClick={() => commitWidth(width > window.innerWidth * 0.6 ? window.innerWidth * 0.42 : maxWidth())}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 120 : 40;
          const next = { ArrowLeft: width + step, ArrowRight: width - step, Home: maxWidth(), End: PANEL_MIN }[event.key];
          if (next !== undefined) { event.preventDefault(); commitWidth(next); }
        }}><span aria-hidden="true" /></div>}
      <header className="tv-sv__bar">
        <div className="tv-sv__variants" role="tablist" aria-label="Layout variation">
          {VARIANTS.map((item, index) => <button key={item.id} type="button" role="tab" aria-selected={variant === item.id}
            className={variant === item.id ? 'is-active' : ''} onClick={() => pickVariant(item.id)} title={item.note}>
            <em>{String.fromCharCode(65 + index)}</em>{item.name}</button>)}
        </div>
        <div className="tv-sv__panels" role="group" aria-label="Show or hide panels">
          {panels.list.map(({ key, label }) => <button key={key} type="button" data-panel={key} aria-pressed={panels.shown(key)}
            className={panels.shown(key) ? 'is-active' : ''} onClick={() => panels.toggle(key)}>{label}</button>)}
        </div>
        {modeProp === undefined && <div className="tv-sv__modes" role="group" aria-label="Route view">
          <button type="button" aria-pressed={mode === 'graph'} className={mode === 'graph' ? 'is-active' : ''} onClick={() => pickMode('graph')}>3D graph</button>
          <button type="button" aria-pressed={mode === 'map'} className={mode === 'map' ? 'is-active' : ''} onClick={() => pickMode('map')}>Map</button>
        </div>}
        {!embedded && onClose && <button type="button" className="tv-iconbtn" onClick={onClose} aria-label="Close spatial view"><CloseIcon width={16} height={16} /></button>}
      </header>

      {!graph || !ctx ? <div className="tv-spatial__empty"><RouteIcon width={28} height={28} />
        <h3>Your route will take shape with the itinerary.</h3>
        <p>When the planner writes a draft, its places and connections appear here during the same response.</p>
      </div> : variant === 'atlas' ? <div className="tv-sv__body">
        {stage}
        {panels.shown('title') && <div className="tv-sv__title" style={panels.offsetStyle('title')}>{move('title', 'Summary')}{hide('title', 'Summary')}
          <span>{trip?.days ? `${trip.days} days` : 'Route'}</span><h2>{trip?.destination || 'Your journey'}</h2>{summary}</div>}
        {panels.shown('sheet') && <div className="tv-sv__sheet" key={sel?.id || 'none'} style={panels.offsetStyle('sheet')}>
          {move('sheet', 'Details')}{hide('sheet', 'Details')}<Inspector ctx={ctx} /></div>}
        {panels.shown('strip') && <RouteStrip ctx={ctx} />}
      </div> : variant === 'outline' ? <div className="tv-sv__body">
        {panels.shown('rail') && <div className="tv-sv__rail">{hide('rail', 'Outline')}
          <div className="tv-sv__title"><span>Route outline</span><h2>{trip?.destination || 'Your journey'}</h2>{summary}<PrefsLine prefs={prefs} /></div>
          <RouteTree ctx={ctx} />
        </div>}
        {stage}
        {panels.shown('sheet') && <div className="tv-sv__sheet" key={sel?.id || 'none'}>{hide('sheet', 'Details')}<Inspector ctx={ctx} /></div>}
      </div> : <div className="tv-sv__body">
        <div className="tv-sv__lead">
          {panels.shown('title') && <div className="tv-sv__title">{hide('title', 'Summary')}<span>The journey, in order</span><h2>{trip?.destination || 'Your journey'} <em>as a journal.</em></h2>{summary}<PrefsLine prefs={prefs} /></div>}
          {stage}
        </div>
        <Journal ctx={ctx} />
      </div>}
    </aside>
  </>;
};
