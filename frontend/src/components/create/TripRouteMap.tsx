import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { placeOnRoute, pointAlong, splitPath, type DayRouteView, type PlaybackFrame } from '../../lib/dayRoute';
import { airDistanceKm, type Coordinates, type ItineraryGraph, type NearbyPlace, type RoadMetric } from './itineraryGraph';

maplibregl.setWorkerUrl(mapWorkerUrl);

interface Props {
  /** The guide's portrait stands in for the selected stop's number. */
  guideImage?: string;
  graph: ItineraryGraph;
  coordinates: Record<string, Coordinates>;
  roadMetrics: Record<string, RoadMetric>;
  nearby: NearbyPlace[];
  focusedNode: string | null;
  selectedNode: string | null;
  selectedEdge: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onSelectNearby: (id: string) => void;
  /** The selected day's stops. While this is set, the city graph steps aside. */
  dayRoute?: DayRouteView | null;
  playback?: PlaybackFrame | null;
  onPickStop?: (name: string) => void;
}

export function TripRouteMap({ guideImage, graph, coordinates, roadMetrics, nearby, focusedNode, selectedNode, selectedEdge, onSelectNode, onSelectEdge, onSelectNearby, dayRoute = null, playback = null, onPickStop }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<Map<string, HTMLButtonElement>>(new Map());
  const tokenRef = useRef<maplibregl.Marker | null>(null);
  const waiting = useRef(false);
  const routed = useRef<DayRouteView | null>(null);
  if (routed.current !== dayRoute) {
    routed.current = dayRoute;
    waiting.current = false;
  }
  const pickRef = useRef(onPickStop);
  pickRef.current = onPickStop;
  const [fitMode, setFitMode] = useState<'stops' | 'full'>('stops');
  const [hoveredPlace, setHoveredPlace] = useState<{ name: string; detail: string } | null>(null);

  const located = graph.nodes.filter((node) => coordinates[node.id]);
  const origin = located.find((node) => node.kind === 'origin');
  const firstStop = located.find((node) => node.kind === 'stop');
  const longHaul = !!origin && !!firstStop
    && airDistanceKm(coordinates[origin.id], coordinates[firstStop.id]) > 2500;
  const selectedPlace = graph.nodes.find((node) => node.id === selectedNode);
  const visiblePlace = hoveredPlace || (selectedPlace ? { name: selectedPlace.name,
    detail: selectedPlace.kind === 'origin' ? 'Departure' : selectedPlace.day_start ? `Day ${selectedPlace.day_start}${selectedPlace.day_end && selectedPlace.day_end !== selectedPlace.day_start ? `–${selectedPlace.day_end}` : ''}` : 'Trip stop' } : null);

  useEffect(() => {
    if (!container.current) return;
    setHoveredPlace(null);
    const available = graph.nodes.filter((node) => coordinates[node.id]);
    const dayFirst = dayRoute?.points[0];
    const first = dayFirst ? { lat: dayFirst.lat, lon: dayFirst.lon } : available[0] && coordinates[available[0].id];
    const map = new maplibregl.Map({
      container: container.current,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: first ? [first.lon, first.lat] : [0, 15],
      zoom: first ? 4 : 1.3,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    map.on('load', () => {
      if (dayRoute?.points.length) {
        const empty = { type: 'FeatureCollection' as const, features: [] };
        map.addSource('day-done', { type: 'geojson', data: empty });
        map.addSource('day-ahead', { type: 'geojson', data: empty });
        map.addLayer({ id: 'day-done', type: 'line', source: 'day-done', paint: { 'line-color': '#1f1e1e', 'line-width': 4 } });
        map.addLayer({ id: 'day-ahead', type: 'line', source: 'day-ahead', paint: { 'line-color': '#1f1e1e', 'line-width': 3, 'line-dasharray': [1, 1.4] } });
        const bounds = new maplibregl.LngLatBounds();
        let visit = 0;
        dayRoute.points.forEach((point) => {
          bounds.extend([point.lon, point.lat]);
          if (point.id.endsWith('-back')) return;
          const stayPin = point.category === 'stay';
          if (!stayPin) visit += 1;
          const marker = document.createElement('button');
          marker.type = 'button';
          marker.className = `tv-routemap__marker ${stayPin ? 'is-stay' : ''}`;
          marker.textContent = stayPin ? 'H' : String(visit);
          marker.dataset.name = point.name;
          marker.title = point.name;
          marker.setAttribute('aria-label', `Select ${point.name}`);
          marker.addEventListener('click', () => pickRef.current?.(point.name));
          markersRef.current.set(point.id, marker);
          new maplibregl.Marker({ element: marker, anchor: 'center' }).setLngLat([point.lon, point.lat]).addTo(map);
        });
        const tokenEl = document.createElement('div');
        tokenEl.className = 'tv-routemap__token';
        tokenRef.current = new maplibregl.Marker({ element: tokenEl }).setLngLat([dayRoute.points[0].lon, dayRoute.points[0].lat]).addTo(map);
        if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 80, maxZoom: 15, duration: 0 });
        return;
      }
      const features = graph.edges.flatMap((edge) => {
        const source = coordinates[edge.source];
        const target = coordinates[edge.target];
        if (!source || !target) return [];
        return [{
          type: 'Feature' as const,
          properties: { id: edge.id, label: `${graph.nodes.find((node) => node.id === edge.source)?.name || 'Stop'} → ${graph.nodes.find((node) => node.id === edge.target)?.name || 'Stop'}`,
            distance: roadMetrics[edge.id]?.distance_km ? `${roadMetrics[edge.id].distance_km} km by road` : edge.distance || 'Distance not measured' },
          geometry: {
            type: 'LineString' as const,
            coordinates: roadMetrics[edge.id]?.geometry || [[source.lon, source.lat], [target.lon, target.lat]],
          },
        }];
      });
      map.addSource('trip-legs', { type: 'geojson', data: { type: 'FeatureCollection', features } });
      map.addLayer({ id: 'trip-legs-line', type: 'line', source: 'trip-legs', paint: {
        'line-color': '#1f1e1e', 'line-width': 3, 'line-opacity': 0.85,
      } });
      map.on('click', 'trip-legs-line', (event) => {
        const id = event.features?.[0]?.properties?.id;
        if (id) onSelectEdge(id);
      });
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 });
      map.on('mousemove', 'trip-legs-line', (event) => {
        map.getCanvas().style.cursor = 'pointer';
        const properties = event.features?.[0]?.properties;
        if (properties) popup.setLngLat(event.lngLat).setText(`${properties.label} · ${properties.distance}`).addTo(map);
      });
      map.on('mouseleave', 'trip-legs-line', () => { map.getCanvas().style.cursor = ''; popup.remove(); });

      const bounds = new maplibregl.LngLatBounds();
      available.forEach((node, index) => {
        const point = coordinates[node.id];
        if (fitMode === 'full' || !longHaul || node.kind !== 'origin') {
          bounds.extend([point.lon, point.lat]);
        }
        const marker = document.createElement('button');
        marker.type = 'button';
        marker.className = `tv-routemap__marker ${node.kind === 'origin' ? 'is-origin' : ''}`;
        marker.textContent = String(index + 1);
        marker.setAttribute('aria-label', `Select ${node.name}`);
        marker.title = node.name;
        marker.addEventListener('click', () => onSelectNode(node.id));
        marker.addEventListener('pointerenter', () => setHoveredPlace({ name: node.name,
          detail: node.kind === 'origin' ? 'Departure' : node.day_start ? `Day ${node.day_start}${node.day_end && node.day_end !== node.day_start ? `–${node.day_end}` : ''}` : 'Trip stop' }));
        marker.addEventListener('pointerleave', () => setHoveredPlace(null));
        marker.addEventListener('focus', () => setHoveredPlace({ name: node.name, detail: 'Trip stop' }));
        marker.addEventListener('blur', () => setHoveredPlace(null));
        markersRef.current.set(node.id, marker);
        new maplibregl.Marker({ element: marker, anchor: 'center' }).setLngLat([point.lon, point.lat]).addTo(map);
      });
      nearby.filter((place) => place.lat !== undefined && place.lon !== undefined).forEach((place) => {
        const marker = document.createElement('button');
        marker.type = 'button';
        marker.className = 'tv-routemap__marker is-nearby';
        marker.textContent = '•';
        marker.setAttribute('aria-label', `Inspect nearby place ${place.name}`);
        marker.title = place.name;
        marker.addEventListener('click', () => onSelectNearby(place.id));
        marker.addEventListener('pointerenter', () => setHoveredPlace({ name: place.name, detail: place.category || 'Nearby place' }));
        marker.addEventListener('pointerleave', () => setHoveredPlace(null));
        marker.addEventListener('focus', () => setHoveredPlace({ name: place.name, detail: place.category || 'Nearby place' }));
        marker.addEventListener('blur', () => setHoveredPlace(null));
        new maplibregl.Marker({ element: marker, anchor: 'center' }).setLngLat([place.lon!, place.lat!]).addTo(map);
      });
      const focus = focusedNode && coordinates[focusedNode];
      if (focus && fitMode === 'stops') map.jumpTo({ center: [focus.lon, focus.lat], zoom: 12 });
      else if (available.length > 1) map.fitBounds(bounds, { padding: 72, maxZoom: 10, duration: 0 });
    });
    return () => {
      tokenRef.current = null;
      markersRef.current.clear();
      mapRef.current = null;
      map.remove();
    };
  }, [graph, coordinates, roadMetrics, nearby, focusedNode, onSelectNode, onSelectEdge, onSelectNearby, fitMode, longHaul, dayRoute]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !dayRoute?.points.length) return;
    const paint = () => {
      const doneSource = map.getSource('day-done') as maplibregl.GeoJSONSource | undefined;
      const aheadSource = map.getSource('day-ahead') as maplibregl.GeoJSONSource | undefined;
      if (!doneSource || !aheadSource) return;
      const clock = playback?.clock ?? dayRoute.points[0].start;
      const where = placeOnRoute(dayRoute.points.map((point) => ({ start: point.start, end: point.end })), clock);
      const done: number[][][] = [];
      const ahead: number[][][] = [];
      const push = (bucket: number[][][], path: { lat: number; lng: number }[]) => {
        if (path.length >= 2) bucket.push(path.map((point) => [point.lng, point.lat]));
      };
      dayRoute.legs.forEach((leg, index) => {
        if (where.leg === null) push(index < where.at ? done : ahead, leg.path);
        else if (index < where.leg) push(done, leg.path);
        else if (index > where.leg) push(ahead, leg.path);
        else {
          const split = splitPath(leg.path, where.t);
          push(done, split.done);
          push(ahead, split.ahead);
        }
      });
      const collection = (lines: number[][][]) => ({
        type: 'FeatureCollection' as const,
        features: lines.map((coordinates) => ({ type: 'Feature' as const, properties: {}, geometry: { type: 'LineString' as const, coordinates } })),
      });
      doneSource.setData(collection(done));
      aheadSource.setData(collection(ahead));
      const moving = where.leg != null ? pointAlong(dayRoute.legs[where.leg]?.path || [], where.t) : null;
      const parked = dayRoute.points[where.at];
      const lng = moving ? moving.lng : parked.lon;
      const lat = moving ? moving.lat : parked.lat;
      tokenRef.current?.setLngLat([lng, lat]);
      const active = where.leg == null ? parked.name : dayRoute.points[where.leg + 1]?.name;
      markersRef.current.forEach((marker) => marker.classList.toggle('is-selected', marker.dataset.name === active));
      if (playback?.playing && playback.follow) map.jumpTo({ center: [lng, lat] });
    };
    if (map.getSource('day-ahead')) paint();
    else if (!waiting.current) {
      waiting.current = true;
      map.once('idle', () => { waiting.current = false; paint(); });
    }
  }, [dayRoute, playback?.clock, playback?.playing, playback?.follow]);

  useEffect(() => {
    if (dayRoute?.points.length) return;
    markersRef.current.forEach((marker, id) => {
      marker.classList.toggle('is-selected', id === selectedNode);
      marker.classList.toggle('has-guide', !!guideImage && id === selectedNode);
      if (guideImage) marker.style.setProperty('--guide', `url("${guideImage}")`);
    });
    const point = selectedNode && coordinates[selectedNode];
    if (point && mapRef.current?.loaded()) {
      mapRef.current.easeTo({ center: [point.lon, point.lat], zoom: Math.max(mapRef.current.getZoom(), 8), duration: 450 });
    }
  }, [selectedNode, coordinates, guideImage, dayRoute]);

  useEffect(() => {
    if (mapRef.current?.getLayer('trip-legs-line')) {
      mapRef.current.setPaintProperty('trip-legs-line', 'line-color', [
        'case', ['==', ['get', 'id'], selectedEdge || ''], '#a96d38', '#1f1e1e',
      ]);
    }
  }, [selectedEdge]);

  return <div className="tv-routemap-wrap">
    <div className="tv-routemap" ref={container} role="img" aria-label="Map of the itinerary stops and route" />
    {visiblePlace && <div className="tv-routemap__place-label" role="status"><strong>{visiblePlace.name}</strong><span>{visiblePlace.detail}</span></div>}
    <div className="tv-routemap__legend" aria-hidden="true">
      {dayRoute?.points.length
        ? <><span>H Stay</span><span>Numbered stop</span><span>Solid line is where the day has been</span></>
        : <><span>● Numbered stop</span><span>● Nearby place</span></>}
    </div>
    {!dayRoute?.points.length && longHaul && <div className="tv-routemap__fit" role="group" aria-label="Map extent">
      <button type="button" className={fitMode === 'stops' ? 'is-active' : ''} onClick={() => setFitMode('stops')}>Trip stops</button>
      <button type="button" className={fitMode === 'full' ? 'is-active' : ''} onClick={() => setFitMode('full')}>Full route</button>
    </div>}
  </div>;
}
