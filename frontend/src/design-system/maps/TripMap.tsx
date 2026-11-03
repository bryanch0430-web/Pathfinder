/**
 * The trip map: Google Maps with app-owned overlays (route legs, numbered
 * stops, the stay, the playback token) through the MapEngine surface.
 */

import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { IconButton } from "../atoms";
import { getMapKeys, type MapLeg } from "../../lib/api";
import { createGoogleEngine, loadGoogleMaps } from "./google";
import type { MapEngine, MapPin, MapPolyline } from "./engine";
import { kmBetween, modeForDistance, MODE, type LatLng, type TravelMode } from "./geo";
import { clockText, minutesOf } from "./time";
import { pinHtml, pinSvg, tokenHtml, tokenSvg, type PinVisual } from "./pins";
import { hotelScopeItem } from "../scope";
import type { DayPlan, PlanItem, TripPlan } from "../types";

const HAS_GEO = (item: PlanItem): item is PlanItem & { geo: NonNullable<PlanItem["geo"]> } => Boolean(item.geo);

/** Cheap identity for a pin's icon, so playback does not re-encode unchanged pins. */
/** Point at fraction `t` of a polyline, measured by distance. */
function pointAlong(path: LatLng[], t: number): LatLng {
  if (path.length === 0) return { lat: 0, lng: 0 };
  if (path.length === 1) return path[0];
  const clamped = Math.min(1, Math.max(0, t));
  const lengths: number[] = [];
  let total = 0;
  for (let index = 1; index < path.length; index += 1) {
    const step = kmBetween(path[index - 1], path[index]);
    lengths.push(step);
    total += step;
  }
  if (total === 0) return path[0];
  let remaining = clamped * total;
  for (let index = 0; index < lengths.length; index += 1) {
    if (remaining <= lengths[index]) {
      const local = lengths[index] === 0 ? 0 : remaining / lengths[index];
      const from = path[index];
      const to = path[index + 1];
      return {
        lat: from.lat + (to.lat - from.lat) * local,
        lng: from.lng + (to.lng - from.lng) * local,
      };
    }
    remaining -= lengths[index];
  }
  return path[path.length - 1];
}

const visualKey = (visual: PinVisual): string =>
  `${visual.label}|${visual.variant}|${visual.active ? 1 : 0}|${visual.dimmed ? 1 : 0}|${visual.updated ? 1 : 0}|${visual.now ? 1 : 0}|${visual.visited ? 1 : 0}`;

export interface TripMapProps {
  plan: TripPlan;
  day: DayPlan | undefined;
  points: PlanItem[];
  routePoints: PlanItem[];
  hotel: TripPlan["hotels"][number] | undefined;
  selectedItemId: string | null;
  onSelectItem: (item: PlanItem | null) => void;
  showRoute: boolean;
  onToggleRoute: () => void;
  clock: number;
  currentItemId: string | null;
  currentName: string | null;
  token: {
    position: [number, number];
    mode: TravelMode;
    moving: boolean;
    label: string;
    fromId: string | null;
    toId: string | null;
    progress: number;
  } | null;
  /** Real route geometry per leg (including restaurants). */
  legs: MapLeg[];
  speed: number;
  follow: boolean;
  updatedIds: Set<string>;
  hotelChanged: boolean;
}

export function TripMap(props: TripMapProps) {
  const {
    plan,
    day,
    points,
    routePoints,
    hotel,
    selectedItemId,
    onSelectItem,
    showRoute,
    onToggleRoute,
    clock,
    currentItemId,
    currentName,
    token,
    speed,
    follow,
    updatedIds,
    hotelChanged,
    legs,
  } = props;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const engineRef = useRef<MapEngine | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "no-key" | "failed">("loading");
  const [generation, setGeneration] = useState(0);

  const fitKey = useRef("");
  const lastSelected = useRef<string | null>(null);
  const lastNow = useRef<string | null>(null);
  const lastLeg = useRef<string | null>(null);
  const selectRef = useRef(onSelectItem);
  selectRef.current = onSelectItem;

  const tokenPin = useRef<MapPin | null>(null);
  const tokenPos = useRef<LatLng | null>(null);
  const tokenRaf = useRef<number | null>(null);
  const tokenMode = useRef("");
  const pinRefs = useRef(new Map<string, { pin: MapPin; item: PlanItem; key: string }>());
  const hotelPinRef = useRef<MapPin | null>(null);
  const routeRef = useRef<{
    casing: MapPolyline | null;
    legs: Array<{
      line: MapPolyline;
      color: string;
      path: LatLng[];
      fromId: string;
      toId: string;
      doneAt: number;
    }>;
  }>({
    casing: null,
    legs: [],
  });
  const [layersBuilt, setLayersBuilt] = useState(0);
  const clockRef = useRef(clock);
  clockRef.current = clock;
  const centre = plan.destination.geo ?? { lat: 22.3193, lng: 114.1694 };

  // --- create the map -----------------------------------------------------
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let cancelled = false;
    let engine: MapEngine | null = null;
    setStatus("loading");
    setGeneration(0);
    el.innerHTML = "";

    void (async () => {
      try {
        const keys = await getMapKeys();
        if (!keys.google.enabled) {
          if (!cancelled) setStatus("no-key");
          return;
        }
        await loadGoogleMaps(keys.google.key);
        if (cancelled) return;
        el.innerHTML = "";
        engine = createGoogleEngine(el, centre);
        engineRef.current = engine;
        if (!cancelled) {
          setStatus("ready");
          setGeneration((value) => value + 1);
        }
      } catch {
        if (!cancelled) setStatus("failed");
      }
    })();

    return () => {
      cancelled = true;
      if (tokenRaf.current !== null) cancelAnimationFrame(tokenRaf.current);
      tokenRaf.current = null;
      tokenPin.current = null;
      tokenPos.current = null;
      engine?.destroy();
      engineRef.current = null;
    };
    // Built once; the fit effect recentres when the trip changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- static layers: route and pins, built once per day ----------------
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || status !== "ready") return;
    engine.clear();
    pinRefs.current = new Map();
    hotelPinRef.current = null;
    routeRef.current = { casing: null, legs: [] };
    // The token lived in the cleared layer set; the token effect rebuilds it.
    tokenPin.current = null;
    tokenPos.current = null;

    const withGeo = points.filter(HAS_GEO);
    const routeGeo = routePoints.filter(HAS_GEO);
    const coords: LatLng[] = routeGeo.map((item) => item.geo);
    const around = hotel?.geo ? [hotel.geo, ...coords] : coords;
    const now = clockRef.current;

    if (fitKey.current !== `${plan.trip_id}:${day?.date ?? ""}`) {
      fitKey.current = `${plan.trip_id}:${day?.date ?? ""}`;
      engine.fit(around, [92, 42, 52, 42]);
    }

    if (showRoute) {
      // Draw exactly the legs the server returned: hotel -> stops -> ... -> hotel.
      // Without that data, fall back to straight lines between consecutive stops.
      const itemsById = new Map(routeGeo.map((item) => [item.id, item]));
      const lastEnd = routeGeo.reduce((latest, item) => {
        const end = minutesOf(item.end);
        return end === null ? latest : Math.max(latest, end);
      }, 0);
      const modeOf = (value: string): TravelMode =>
        value === "transit" ? "metro" : value === "drive" ? "drive" : "walk";
      const drawn: Array<{
        line: MapPolyline;
        color: string;
        path: LatLng[];
        fromId: string;
        toId: string;
        doneAt: number;
      }> = [];

      const draw = (
        path: LatLng[],
        mode: TravelMode,
        fromId: string,
        toId: string,
        doneAt: number
      ) => {
        if (path.length < 2) return;
        const color = MODE[mode].color;
        const done = now >= doneAt;
        // White casing keeps the coloured route readable on any basemap.
        engine.polyline(path, { color: "#FFFFFF", weight: 7, opacity: 0.9 });
        const line = engine.polyline(path, {
          color,
          weight: done ? 4 : 3,
          opacity: done ? 0.95 : 0.58,
          dashed: !done,
        });
        drawn.push({ line, color, path, fromId, toId, doneAt });
      };

      if (legs.length) {
        for (const leg of legs) {
          const path = leg.path.length > 1 ? leg.path.map(([lat, lng]) => ({ lat, lng })) : null;
          if (!path) continue;
          const target = itemsById.get(leg.to_id);
          const doneAt = target ? minutesOf(target.start) ?? 0 : lastEnd;
          draw(path, modeOf(leg.mode), leg.from_id, leg.to_id, doneAt);
        }
      } else {
        for (let index = 1; index < routeGeo.length; index += 1) {
          const from = routeGeo[index - 1];
          const to = routeGeo[index];
          const mode = modeForDistance(kmBetween(from.geo, to.geo));
          draw([from.geo, to.geo], mode, from.id, to.id, minutesOf(to.start) ?? 0);
        }
      }
      routeRef.current = { casing: null, legs: drawn };
    }

    if (hotel?.geo) {
      const visual: PinVisual = { label: "H", variant: "hotel", updated: hotelChanged };
      hotelPinRef.current = engine.pin(hotel.geo, {
        anchor: [-17, -17],
        title: hotel.name,
        z: 700,
        onClick: () => selectRef.current(hotelScopeItem(hotel)),
        visual,
        html: pinHtml(visual),
        svgIcon: pinSvg(visual),
      });
    }

    const numbers = new Map<string, number>();
    let counter = 0;
    for (const item of withGeo) {
      if (item.type !== "meal") {
        counter += 1;
        numbers.set(item.id, counter);
      }
    }

    const visited = new Set(
      withGeo
        .filter((item) => (minutesOf(item.start) ?? 0) <= now)
        .map((item) => item.id)
    );

    for (const item of withGeo) {
      const meal = item.type === "meal";
      const visual: PinVisual = {
        label: meal ? "M" : String(numbers.get(item.id) ?? counter),
        variant: meal ? "meal" : "sight",
        active: item.id === selectedItemId,
        dimmed: item.data_status !== "ok",
        updated: updatedIds.has(item.id),
        now: item.id === currentItemId,
        visited: visited.has(item.id),
      };
      const pin = engine.pin(item.geo, {
        anchor: [-15, -15],
        title: `${item.start}–${item.end} · ${item.name}`,
        z: item.id === selectedItemId || item.id === currentItemId ? 900 : 300,
        onClick: () => selectRef.current(item),
        visual,
        html: pinHtml(visual),
        svgIcon: pinSvg(visual),
      });
      pinRefs.current.set(item.id, { pin, item, key: visualKey(visual) });
    }

    setLayersBuilt((value) => value + 1);
  }, [
    generation,
    status,
    plan.trip_id,
    day?.date,
    points,
    routePoints,
    hotel,
    showRoute,
    hotelChanged,
    legs,
  ]);

  // --- playback state: restyle pins and legs in place --------------------
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || status !== "ready") return;
    if (pinRefs.current.size === 0 && !routeRef.current.casing) return;

    const withGeo = points.filter(HAS_GEO);
    const visited = new Set(
      withGeo
        .filter((item) => (minutesOf(item.start) ?? 0) <= clock)
        .map((item) => item.id)
    );

    const numbers = new Map<string, number>();
    let counter = 0;
    for (const item of withGeo) {
      if (item.type !== "meal") {
        counter += 1;
        numbers.set(item.id, counter);
      }
    }

    for (const [id, entry] of pinRefs.current) {
      const item = entry.item;
      const meal = item.type === "meal";
      const visual: PinVisual = {
        label: meal ? "M" : String(numbers.get(id) ?? counter),
        variant: meal ? "meal" : "sight",
        active: id === selectedItemId,
        dimmed: item.data_status !== "ok",
        updated: updatedIds.has(id),
        now: id === currentItemId,
        visited: visited.has(id),
      };
      const key = visualKey(visual);
      if (entry.key !== key) {
        entry.key = key;
        entry.pin.setIcon(pinSvg(visual));
        entry.pin.setHtml(pinHtml(visual));
      }
      entry.pin.setZIndex?.(id === selectedItemId || id === currentItemId ? 900 : 300);
    }

    if (hotel && hotelPinRef.current) {
      const visual: PinVisual = { label: "H", variant: "hotel", updated: hotelChanged };
      hotelPinRef.current.setIcon(pinSvg(visual));
      hotelPinRef.current.setHtml(pinHtml(visual));
    }

    for (const leg of routeRef.current.legs) {
      const done = clock >= leg.doneAt;
      leg.line.setStyle({
        color: leg.color,
        weight: done ? 4 : 3,
        opacity: done ? 0.95 : 0.58,
        dashed: !done,
      });
    }

    if (selectedItemId !== lastSelected.current) {
      lastSelected.current = selectedItemId;
      const selected = withGeo.find((item) => item.id === selectedItemId);
      if (selected) {
        engine.panTo(selected.geo);
        engine.setZoom(15);
      }
    }
  }, [layersBuilt, clock, currentItemId, selectedItemId, updatedIds, hotelChanged, status, points]);

  // --- playback token ----------------------------------------------------
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || status !== "ready") return;

    if (!token) {
      tokenPin.current?.remove();
      tokenPin.current = null;
      tokenPos.current = null;
      if (tokenRaf.current !== null) cancelAnimationFrame(tokenRaf.current);
      tokenRaf.current = null;
      return;
    }

    // Prefer the real route geometry: the vehicle should travel along the road,
    // not cut across the block. Falls back to the reported position.
    let target: LatLng = { lat: token.position[0], lng: token.position[1] };
    const movingLeg = token.moving
      ? routeRef.current.legs.find(
          (entry) => entry.fromId === token.fromId && entry.toId === token.toId
        )
      : undefined;
    if (movingLeg && movingLeg.path.length > 1) {
      target = pointAlong(movingLeg.path, token.progress);
    }
    const modeKey = `${token.mode}:${token.moving ? "move" : "stop"}`;

    if (!tokenPin.current) {
      tokenPin.current = engine.pin(target, {
        anchor: [-20, -20],
        size: 40,
        interactive: false,
        z: 1000,
        title: token.label,
        html: tokenHtml(token.mode, token.moving),
        svgIcon: tokenSvg(token.mode, token.moving),
      });
      tokenPos.current = target;
      tokenMode.current = modeKey;
    } else {
      if (tokenMode.current !== modeKey) {
        tokenMode.current = modeKey;
        tokenPin.current.setHtml(tokenHtml(token.mode, token.moving));
        tokenPin.current.setIcon(tokenSvg(token.mode, token.moving));
      }
      const from = tokenPos.current ?? target;
      const distance = kmBetween(from, target);
      if (tokenRaf.current !== null) cancelAnimationFrame(tokenRaf.current);
      if (distance > 50) {
        tokenPin.current.moveTo(target);
        tokenPos.current = target;
      } else {
        const duration = Math.max(70, 230 / speed);
        const started = performance.now();
        const step = (time: number) => {
          const progress = Math.min(1, (time - started) / duration);
          const eased = 1 - Math.pow(1 - progress, 3);
          const next = {
            lat: from.lat + (target.lat - from.lat) * eased,
            lng: from.lng + (target.lng - from.lng) * eased,
          };
          tokenPin.current?.moveTo(next);
          tokenPos.current = next;
          if (progress < 1) {
            tokenRaf.current = requestAnimationFrame(step);
          } else {
            tokenRaf.current = null;
          }
        };
        tokenRaf.current = requestAnimationFrame(step);
      }
    }

    // Pan once when a new leg starts, so the replay stays visible without the
    // camera fighting the user's own drag on every tick.
    const legKey = `${token.fromId ?? ""}|${token.toId ?? ""}|${token.moving ? "go" : "stay"}`;
    if (token.moving && legKey !== lastLeg.current && !selectedItemId) {
      lastLeg.current = legKey;
      engine.panTo(target);
    }

    if (follow && !selectedItemId) {
      const key = `${token.position[0].toFixed(5)},${token.position[1].toFixed(5)}`;
      if (lastNow.current !== key) {
        lastNow.current = key;
        engine.panTo(target);
      }
    }
  }, [token, follow, speed, status, generation, layersBuilt, legs, selectedItemId]);

  return (
    <div className="pf-map__canvas">
      <div
        ref={containerRef}
        className="pf-map__surface"
        data-provider="google"
        role="application"
        aria-label={`Map of ${plan.destination.name}${day ? `, ${day.title}` : ""}`}
      />
      <div className="pf-map__place">
        <span className="pf-map__place-icon"><Icon name="map" size={14} /></span>
        <div>
          <span className="pf-map__place-kicker">{day?.title ?? "Choose a day"}</span>
          <strong>{plan.destination.name}</strong>
        </div>
      </div>
      <div className="pf-map__toolbar">
        <IconButton icon="zoomIn" label="Zoom in" onClick={() => engineRef.current?.zoomIn()} />
        <IconButton icon="zoomOut" label="Zoom out" onClick={() => engineRef.current?.zoomOut()} />
        <IconButton icon={showRoute ? "layers" : "route"} label="Toggle route" onClick={onToggleRoute} />
        <IconButton icon="close" label="Clear selection" onClick={() => onSelectItem(null)} />
      </div>
      <div className="pf-map__legend" aria-hidden="true">
        <span><i className="pf-legend-dot pf-legend-dot--sight" />Sight</span>
        <span><i className="pf-legend-dot pf-legend-dot--hotel" />Hotel</span>
        <span className="pf-map__provider">Google Maps</span>
      </div>
      <div className="pf-map__clock" aria-live="polite">
        <strong>{clockText(clock)}</strong>
        <span>{currentName ?? "Before the first stop"}</span>
      </div>
      {status === "no-key" ? (
        <span className="pf-map__notice">GOOGLE_MAPS_API_KEY not set · stops still shown</span>
      ) : null}
      {status === "failed" ? <span className="pf-map__notice">Base map unavailable · stops still shown</span> : null}
    </div>
  );
}
