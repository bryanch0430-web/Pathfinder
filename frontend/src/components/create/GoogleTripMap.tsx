import { useEffect, useRef, useState } from 'react';
import { placeOnRoute, pointAlong, splitPath, type DayRouteView, type PlaybackFrame } from '../../lib/dayRoute';
import { airDistanceKm, type Coordinates, type ItineraryGraph, type NearbyPlace, type RoadMetric } from './itineraryGraph';
import type { LegExpansion, LegFacts } from './spatialModel';
import type { PlaceMedia } from '../../services/placeMedia';

type MapsApi = any;
export type MapSkin = 'atlas' | 'paper' | 'night';
let mapsPromise: Promise<MapsApi> | null = null;

function loadMaps(key: string): Promise<MapsApi> {
  if ((window as any).google?.maps) return Promise.resolve((window as any).google.maps);
  if (!mapsPromise) {
    mapsPromise = new Promise((resolve, reject) => {
      const callback = '__pathfinderMapsReady';
      (window as any)[callback] = () => { delete (window as any)[callback]; resolve((window as any).google.maps); };
      const script = document.createElement('script');
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&libraries=geometry&loading=async&callback=${callback}`;
      script.async = true;
      script.onerror = () => { delete (window as any)[callback]; mapsPromise = null; reject(new Error('Google Maps could not load. Check the key and allowed referrers.')); };
      document.head.appendChild(script);
    });
  }
  return mapsPromise;
}

const hide = (featureType: string) => ({ featureType, stylers: [{ visibility: 'off' }] });
const SKINS: Record<MapSkin, { bg: string; line: string; casing: string; accent: string; styles: object[] }> = {
  atlas: { bg: '#e9e6df', line: '#262521', casing: '#ffffff', accent: '#a96d38', styles: [
    hide('poi'), hide('transit'),
    { elementType: 'geometry', stylers: [{ color: '#efece6' }] },
    { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#d6dcda' }] },
    { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#ffffff' }] },
    { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#e2ddd3' }] },
    { featureType: 'landscape.natural', elementType: 'geometry', stylers: [{ color: '#e7e3da' }] },
    { elementType: 'labels.text.fill', stylers: [{ color: '#6b6963' }] },
    { elementType: 'labels.text.stroke', stylers: [{ color: '#f7f6f3' }] },
    { featureType: 'administrative', elementType: 'geometry.stroke', stylers: [{ color: '#c9c4b9' }] }] },
  paper: { bg: '#fbfbfa', line: '#111111', casing: '#fbfbfa', accent: '#a96d38', styles: [
    hide('poi'), hide('transit'), { featureType: 'road', elementType: 'labels', stylers: [{ visibility: 'off' }] },
    { elementType: 'geometry', stylers: [{ color: '#fbfbfa' }] },
    { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#eceeee' }] },
    { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#efefed' }] },
    { featureType: 'landscape', elementType: 'geometry', stylers: [{ color: '#f7f6f3' }] },
    { elementType: 'labels.text.fill', stylers: [{ color: '#9b9a97' }] },
    { featureType: 'administrative', elementType: 'geometry.stroke', stylers: [{ color: '#dcdcda' }] }] },
  night: { bg: '#131313', line: '#e0a768', casing: '#131313', accent: '#f4d2a8', styles: [
    hide('poi'), hide('transit'),
    { elementType: 'geometry', stylers: [{ color: '#1b1b1b' }] },
    { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#0e1214' }] },
    { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#2a2a2a' }] },
    { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#363432' }] },
    { featureType: 'landscape.natural', elementType: 'geometry', stylers: [{ color: '#1e1f1d' }] },
    { elementType: 'labels.text.fill', stylers: [{ color: '#8a8780' }] },
    { elementType: 'labels.text.stroke', stylers: [{ color: '#131313' }] },
    { featureType: 'administrative', elementType: 'geometry.stroke', stylers: [{ color: '#3a3936' }] }] },
};

interface Props {
  /** The guide's portrait stands over the open stop's pin. */
  guideImage?: string;
  graph: ItineraryGraph;
  skin: MapSkin;
  coordinates: Record<string, Coordinates>;
  roadMetrics: Record<string, RoadMetric>;
  facts: Record<string, LegFacts>;
  nearby: NearbyPlace[];
  expandedNode: string | null;
  expandedEdge: string | null;
  expansion: LegExpansion | null;
  selected: string | null;
  onSelect: (kind: 'node' | 'edge' | 'nearby' | 'sub' | 'subleg', id: string) => void;
  /** Pixels hidden under floating panes, kept clear when framing. */
  inset?: { right: number; bottom: number };
  /** Every stop's located planned places, pinned whether or not the stop is open. */
  planned?: { id: string; parent: string; name: string; lat: number; lon: number }[];
  /** Photos by node id: stops with one become photo pins. Credit is shown in the detail panel. */
  media?: Record<string, PlaceMedia>;
  /** The selected day's stops, in visit order. Replaces the city graph while a day is open. */
  dayRoute?: DayRouteView | null;
  playback?: PlaybackFrame | null;
  onPickStop?: (name: string) => void;
}

const el = (html: string, className: string, onClick: () => void) => {
  const node = document.createElement('button');
  node.type = 'button'; node.className = className; node.innerHTML = html;
  node.addEventListener('click', (event) => { event.stopPropagation(); onClick(); });
  return node;
};
const esc = (text: string) => text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

// Shared empties keep effect dependencies stable when a prop is omitted.
const NO_PLANNED: NonNullable<Props['planned']> = [];
const NO_MEDIA: Record<string, PlaceMedia> = {};

export function GoogleTripMap({ guideImage, graph, skin, coordinates, roadMetrics, facts, nearby, expandedNode, expandedEdge, expansion, selected, onSelect, inset, planned = NO_PLANNED, media = NO_MEDIA, dayRoute = null, playback = null, onPickStop }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const [maps, setMaps] = useState<MapsApi>(null);
  const [error, setError] = useState<string | null>(null);
  const [fitMode, setFitMode] = useState<'stops' | 'full'>('stops');
  const [mapType, setMapType] = useState<'roadmap' | 'terrain' | 'hybrid'>('roadmap');
  const theme = SKINS[skin];
  const located = graph.nodes.filter((node) => coordinates[node.id]);
  const origin = located.find((node) => node.kind === 'origin');
  const firstStop = located.find((node) => node.kind === 'stop');
  const longHaul = !!origin && !!firstStop && airDistanceKm(coordinates[origin.id], coordinates[firstStop.id]) > 2500;
  const pathOf = (id: string) => {
    const edge = graph.edges.find((item) => item.id === id);
    const from = edge && coordinates[edge.source]; const to = edge && coordinates[edge.target];
    if (!from || !to) return [];
    return roadMetrics[id]?.geometry?.length ? roadMetrics[id].geometry.map(([lng, lat]) => ({ lat, lng }))
      : [{ lat: from.lat, lng: from.lon }, { lat: to.lat, lng: to.lon }];
  };

  useEffect(() => {
    const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
    if (!container.current || !key) return;
    let cancelled = false;
    void loadMaps(key).then((api) => {
      if (cancelled || !container.current) return;
      mapRef.current = new api.Map(container.current, {
        center: { lat: 20, lng: 0 }, zoom: 2, disableDefaultUI: true, zoomControl: true,
        zoomControlOptions: { position: api.ControlPosition.RIGHT_BOTTOM }, gestureHandling: 'greedy', clickableIcons: false,
      });
      setMaps(api);
    }).catch((cause: Error) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; mapRef.current = null; };
  }, []);

  useEffect(() => {
    mapRef.current?.setOptions({ backgroundColor: theme.bg, styles: mapType === 'roadmap' ? theme.styles : [], mapTypeId: mapType });
  }, [maps, theme, mapType]);

  // A place that appears in the plan (added from chat) flashes and is panned into view.
  const pickRef = useRef(onPickStop);
  pickRef.current = onPickStop;
  const routeLayer = useRef<{
    pins: HTMLElement[];
    legs: { path: { lat: number; lng: number }[]; done: { setPath: (path: { lat: number; lng: number }[]) => void; setVisible: (on: boolean) => void }; ahead: { setPath: (path: { lat: number; lng: number }[]) => void; setVisible: (on: boolean) => void } }[];
    token: { setAt: (at: { lat: number; lon: number }) => void };
  } | null>(null);
  const seenPlaces = useRef(new Set<string>());
  const flashUntil = useRef(new Map<string, number>());
  useEffect(() => {
    const fresh = planned.filter((place) => !seenPlaces.current.has(place.id));
    fresh.forEach((place) => { seenPlaces.current.add(place.id); flashUntil.current.set(place.id, Date.now() + 1800); });
    const map = mapRef.current;
    const last = fresh.at(-1);
    if (maps && map && last && seenPlaces.current.size > fresh.length && !map.getBounds()?.contains({ lat: last.lat, lng: last.lon })) {
      map.panTo({ lat: last.lat, lng: last.lon });
    }
  }, [maps, planned]);

  // Overlays: rebuilt on any change — a trip is a few dozen objects at most.
  useEffect(() => {
    const map = mapRef.current;
    if (!maps || !map) return;
    class HtmlPin extends maps.OverlayView {
      constructor(private at: Coordinates, private node: HTMLElement) { super(); maps.OverlayView.preventMapHitsAndGesturesFrom(node); }
      onAdd() { this.getPanes().overlayMouseTarget.appendChild(this.node); }
      draw() {
        const p = this.getProjection()?.fromLatLngToDivPixel(new maps.LatLng(this.at.lat, this.at.lon));
        if (p) { this.node.style.left = `${p.x}px`; this.node.style.top = `${p.y}px`; }
      }
      onRemove() { this.node.remove(); }
    }
    const overlays: any[] = [];
    const pin = (at: Coordinates, node: HTMLElement) => { const o = new HtmlPin(at, node); o.setMap(map); overlays.push(o); };
    const line = (options: object) => { const l = new maps.Polyline({ map, ...options }); overlays.push(l); return l; };
    const dash = (color: string, scale = 3) => [{ icon: { path: 'M 0,-1 0,1', strokeOpacity: 1, strokeColor: color, scale }, offset: '0', repeat: '14px' }];

    routeLayer.current = null;
    if (dayRoute?.points.length) {
      const pins: HTMLElement[] = [];
      let visit = 0;
      dayRoute.points.forEach((point) => {
        if (point.id.endsWith('-back')) return;
        const stayPin = point.category === 'stay';
        if (!stayPin) visit += 1;
        const number = stayPin ? 'H' : String(visit).padStart(2, '0');
        const node = el(`<em>${number}</em><span>${esc(point.name)}</span>`, `tv-gm__stop ${point.category === 'stay' ? 'is-stay' : ''}`, () => pickRef.current?.(point.name));
        node.dataset.name = point.name;
        pin({ lat: point.lat, lon: point.lon }, node);
        pins.push(node);
      });
      const legs = dayRoute.legs.map((leg) => {
        const done = line({ path: leg.path, strokeColor: theme.line, strokeWeight: 4, zIndex: 3 });
        const ahead = line({ path: leg.path, strokeColor: theme.line, strokeOpacity: 0, strokeWeight: 3, zIndex: 2, icons: dash(theme.line) });
        done.setVisible(false);
        return { path: leg.path, done, ahead };
      });
      class TokenPin extends maps.OverlayView {
        at: { lat: number; lon: number };
        node: HTMLElement;
        constructor(at: { lat: number; lon: number }, node: HTMLElement) {
          super();
          this.at = at;
          this.node = node;
          maps.OverlayView.preventMapHitsAndGesturesFrom(node);
        }
        onAdd() { this.getPanes().overlayMouseTarget.appendChild(this.node); }
        draw() {
          const projected = this.getProjection()?.fromLatLngToDivPixel(new maps.LatLng(this.at.lat, this.at.lon));
          if (projected) { this.node.style.left = `${projected.x}px`; this.node.style.top = `${projected.y}px`; }
        }
        setAt(at: { lat: number; lon: number }) { this.at = at; this.draw(); }
        onRemove() { this.node.remove(); }
      }
      const tokenNode = document.createElement('div');
      tokenNode.className = 'tv-gm__token';
      const first = dayRoute.points[0];
      const token = new TokenPin({ lat: first.lat, lon: first.lon }, tokenNode);
      token.setMap(map);
      overlays.push(token);
      routeLayer.current = { pins, legs, token };
      return () => { overlays.forEach((overlay) => overlay.setMap(null)); routeLayer.current = null; };
    }

    graph.edges.forEach((edge) => {
      const path = pathOf(edge.id);
      if (!path.length) return;
      const f = facts[edge.id];
      const on = selected === edge.id || expandedEdge === edge.id;
      const air = f?.kind === 'air' || f?.kind === 'water';
      const color = on ? theme.accent : theme.line;
      line({ path, geodesic: air, strokeColor: theme.casing, strokeWeight: on ? 9 : 6, strokeOpacity: air ? 0 : 0.9, zIndex: 1 });
      const main = line({ path, geodesic: air, clickable: true, zIndex: 2, strokeColor: color, strokeWeight: on ? 4.5 : 3,
        strokeOpacity: air ? 0 : 1, icons: [...(air ? dash(color) : []),
          { icon: { path: maps.SymbolPath.FORWARD_CLOSED_ARROW, scale: on ? 3.2 : 2.4, fillColor: color, fillOpacity: 1, strokeColor: color }, offset: '55%' }] });
      main.addListener('click', () => onSelect('edge', edge.id));
      const mid = air && path.length === 2 ? maps.geometry?.spherical?.interpolate?.(path[0], path[1], 0.5) : null;
      const half = path.length === 2 ? { lat: (path[0].lat + path[1].lat) / 2, lng: (path[0].lng + path[1].lng) / 2 } : path[Math.floor(path.length / 2)];
      const center = mid ? { lat: mid.lat(), lon: mid.lng() } : { lat: half.lat, lon: half.lng };
      if (f && (graph.edges.length <= 8 || on)) pin(center, el(
        `<b>${esc(f.duration.value)}</b><span>${esc(f.distance.value)}</span>${f.cost.value !== 'Not quoted' ? `<span>${esc(f.cost.value)}</span>` : ''}${f.fit.some((n) => n.tone === 'warn') ? '<i>!</i>' : ''}`,
        `tv-gm__chip ${on ? 'is-active' : ''}`, () => onSelect('edge', edge.id)));
    });

    if (expansion) expansion.stops.forEach((stop) => stop.role === 'waypoint' && stop.point && pin(stop.point,
      el(`<i></i><span>${esc(stop.at)}</span>`, `tv-gm__way ${selected === stop.id ? 'is-active' : ''}`, () => onSelect('sub', stop.id))));

    const parent = expandedNode && coordinates[expandedNode];
    if (parent) nearby.forEach((place) => {
      if (place.lat === undefined || place.lon === undefined) return;
      const on = selected === place.id;
      line({ path: [{ lat: parent.lat, lng: parent.lon }, { lat: place.lat, lng: place.lon }], strokeOpacity: 0, zIndex: 1, icons: dash(theme.accent, 1.5) });
      pin({ lat: place.lat, lon: place.lon }, el(`<i></i><span>${esc(place.name)}</span>`, `tv-gm__near ${on ? 'is-active' : ''}`, () => onSelect('nearby', place.id)));
    });

    planned.forEach((place) => {
      if (place.parent === expandedNode) return; // drawn above as the open stop's nearby places
      const home = coordinates[place.parent];
      if (home) line({ path: [{ lat: home.lat, lng: home.lon }, { lat: place.lat, lng: place.lon }], strokeOpacity: 0, zIndex: 1, icons: dash(theme.line, 1) });
      const fresh = (flashUntil.current.get(place.id) || 0) > Date.now();
      pin(place, el(`<i></i><span>${esc(place.name)}</span>`, `tv-gm__near is-planned ${fresh ? 'is-new' : ''}`,
        () => { onSelect('node', place.parent); onSelect('nearby', place.id); }));
    });

    graph.nodes.forEach((node, index) => {
      const at = coordinates[node.id];
      if (!at) return;
      const on = selected === node.id || expandedNode === node.id;
      const photo = media[node.id]?.image;
      const number = String(index + 1).padStart(2, '0');
      pin(at, el(`${photo ? `<em class="has-photo" style="background-image:url(&quot;${esc(photo)}&quot;)"><b>${number}</b></em>` : `<em>${number}</em>`}<span>${esc(node.name)}</span>${node.day_start ? `<small>D${node.day_start}</small>` : ''}`,
        `tv-gm__stop ${on ? 'is-active' : ''} ${node.kind === 'origin' ? 'is-origin' : ''}`, () => onSelect('node', node.id)));
      if (on && guideImage) pin(at, el(`<img src="${esc(guideImage)}" alt="" draggable="false">`, 'tv-gm__guide', () => onSelect('node', node.id)));
    });
    return () => overlays.forEach((overlay) => overlay.setMap(null));
  }, [maps, graph, coordinates, roadMetrics, facts, nearby, planned, media, expandedNode, expandedEdge, expansion, selected, theme, onSelect, guideImage, dayRoute]);

  // Camera: frame whatever is expanded, otherwise the whole route.
  useEffect(() => {
    const map = mapRef.current;
    if (!maps || !map || dayRoute?.points.length) return;
    const bounds = new maps.LatLngBounds();
    if (expandedEdge) pathOf(expandedEdge).forEach((point) => bounds.extend(point));
    else if (expandedNode && coordinates[expandedNode]) {
      const c = coordinates[expandedNode];
      bounds.extend({ lat: c.lat, lng: c.lon });
      nearby.forEach((place) => place.lat !== undefined && bounds.extend({ lat: place.lat, lng: place.lon }));
    } else located.forEach((node) => {
      if (fitMode === 'full' || !longHaul || node.kind !== 'origin') bounds.extend({ lat: coordinates[node.id].lat, lng: coordinates[node.id].lon });
      if (node.kind === 'stop') planned.forEach((place) => place.parent === node.id && bounds.extend({ lat: place.lat, lng: place.lon }));
    });
    if (bounds.isEmpty()) return;
    const wide = (container.current?.clientWidth || 0) > 900;
    const cap = nearby.some((place) => place.lat !== undefined) ? 15 : 12;
    const frame = () => {
      map.fitBounds(bounds, { top: 80, left: 60, bottom: 80 + (wide ? inset?.bottom || 0 : 0), right: 60 + (wide ? inset?.right || 0 : 0) });
      // A single framed point would otherwise zoom to street level.
      maps.event.addListenerOnce(map, 'idle', () => { const max = expandedNode && !expandedEdge ? cap : 14; if (map.getZoom() > max) map.setZoom(max); });
    };
    // A fresh map has no projection until its first idle; framing earlier lands a zoom level short.
    if (map.getProjection()) frame(); else maps.event.addListenerOnce(map, 'idle', frame);
  }, [maps, expandedEdge, expandedNode, coordinates, nearby.length, planned.length > 0, fitMode, longHaul, inset?.right, inset?.bottom, dayRoute]);

  useEffect(() => {
    const map = mapRef.current;
    if (!maps || !map || !dayRoute?.points.length) return;
    const bounds = new maps.LatLngBounds();
    dayRoute.points.forEach((point) => bounds.extend({ lat: point.lat, lng: point.lon }));
    const frame = () => map.fitBounds(bounds, { top: 80, left: 60, bottom: 120, right: 60 });
    if (map.getProjection()) frame(); else maps.event.addListenerOnce(map, 'idle', frame);
  }, [maps, dayRoute]);

  useEffect(() => {
    const layer = routeLayer.current;
    const map = mapRef.current;
    if (!layer || !map || !dayRoute?.points.length) return;
    const clock = playback?.clock ?? dayRoute.points[0].start;
    const where = placeOnRoute(dayRoute.points.map((point) => ({ start: point.start, end: point.end })), clock);
    const paint = (polyline: { setPath: (path: { lat: number; lng: number }[]) => void; setVisible: (on: boolean) => void }, path: { lat: number; lng: number }[]) => {
      if (path.length < 2) { polyline.setVisible(false); return; }
      polyline.setPath(path);
      polyline.setVisible(true);
    };
    layer.legs.forEach((leg, index) => {
      let done: { lat: number; lng: number }[] = [];
      let ahead = leg.path;
      if (where.leg === null) {
        if (index < where.at) { done = leg.path; ahead = []; }
      } else if (index < where.leg) {
        done = leg.path;
        ahead = [];
      } else if (index === where.leg) {
        const split = splitPath(leg.path, where.t);
        done = split.done;
        ahead = split.ahead;
      }
      paint(leg.done, done);
      paint(leg.ahead, ahead);
    });
    const moving = where.leg != null ? pointAlong(dayRoute.legs[where.leg]?.path || [], where.t) : null;
    const parked = dayRoute.points[where.at];
    const at = moving ? { lat: moving.lat, lon: moving.lng } : { lat: parked.lat, lon: parked.lon };
    layer.token.setAt(at);
    const active = where.leg == null ? parked.name : dayRoute.points[where.leg + 1]?.name;
    layer.pins.forEach((node) => node.classList.toggle('is-active', node.dataset.name === active));
    if (playback?.playing && playback.follow) map.panTo({ lat: at.lat, lng: at.lon });
  }, [dayRoute, playback?.clock, playback?.playing, playback?.follow, maps]);

  return <div className={`tv-gm is-${skin}`}>
    <div className="tv-gm__canvas" ref={container} role="application" aria-label="Google map of itinerary stops, legs and nearby places" />
    <div className="tv-gm__tools" role="group" aria-label="Map options">
      {(['roadmap', 'terrain', 'hybrid'] as const).map((type) => <button key={type} type="button" className={mapType === type ? 'is-active' : ''} onClick={() => setMapType(type)}>
        {type === 'roadmap' ? 'Map' : type === 'terrain' ? 'Terrain' : 'Satellite'}</button>)}
      {!dayRoute?.points.length && longHaul && <><span />
        <button type="button" className={fitMode === 'stops' ? 'is-active' : ''} onClick={() => setFitMode('stops')}>Stops</button>
        <button type="button" className={fitMode === 'full' ? 'is-active' : ''} onClick={() => setFitMode('full')}>Full route</button></>}
    </div>
    {error && <div className="tv-gm__error" role="status">{error}</div>}
  </div>;
}
