/**
 * The trip map: Google Maps with app-owned overlays (route legs, numbered
 * stops, the stay, the playback token) through the MapEngine surface.
 */

import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { IconButton } from "../atoms";
import { getMapKeys, type MapLeg, type MapStep } from "../../lib/api";
import { createGoogleEngine, loadGoogleMaps } from "./google";
import type { MapEngine, MapPin, MapPolyline } from "./engine";
import { kmBetween, modeForDistance, MODE, SEGMENT_COLOR, type LatLng, type TravelMode } from "./geo";
import { clockText, minutesOf } from "./time";
import { pinHtml, pinSvg, tokenHtml, tokenSvg, type PinVisual } from "./pins";
import { hotelScopeItem } from "../scope";
import { useI18n } from "../../lib/i18n";
import { buildGeometry, offsetPath, pointAlong, type RouteGeometry } from "../../lib/mapGeometry";
import type { DayPlan, PlanItem, TripPlan } from "../types";

const HAS_GEO = (item: PlanItem): item is PlanItem & { geo: NonNullable<PlanItem["geo"]> } => Boolean(item.geo);

/** Non-walking steps inside a WALK route (ferry, cable car, tram). */
const STEP_COLOR: Record<string, string> = {
  ...SEGMENT_COLOR,
  transit: MODE.metro.color,
};

const FERRY_ICON = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 30 30">' +
    '<circle cx="15" cy="15" r="14" fill="#ffffff" stroke="#6D28D9" stroke-width="2"/>' +
    '<path d="M7 17l1.5-3.8h13L23 17" fill="none" stroke="#6D28D9" stroke-width="1.7"/>' +
    '<path d="M6 17c2 1.9 3.8 1.9 5.8 0s3.8-1.9 5.8 0 3.8 1.9 5.8 0" fill="none" stroke="#6D28D9" stroke-width="1.7"/>' +
    '<path d="M12 13V9.5h6V13" fill="none" stroke="#6D28D9" stroke-width="1.7"/>' +
    "</svg>"
)}`;

const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Cheap identity for a pin's icon, so playback does not re-encode unchanged pins. */

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
  const { t } = useI18n();
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
  const lastLeg = useRef<string | null>(null);
  const selectRef = useRef(onSelectItem);
  selectRef.current = onSelectItem;

  const tokenPin = useRef<MapPin | null>(null);
  const tokenPos = useRef<LatLng | null>(null);
  const tokenTarget = useRef<LatLng | null>(null);
  const motionRaf = useRef<number | null>(null);
  const tokenMode = useRef("");
  const followRef = useRef(follow);
  const selectedRef = useRef(selectedItemId);
  followRef.current = follow;
  selectedRef.current = selectedItemId;
  const pinRefs = useRef(new Map<string, { pin: MapPin; item: PlanItem; key: string }>());
  const hotelPinRef = useRef<MapPin | null>(null);
  const routeRef = useRef<{
    casing: MapPolyline | null;
    legs: Array<{
      casing: MapPolyline;
      line: MapPolyline;
      extras: Array<{ casing: MapPolyline; line: MapPolyline; color: string; mode: string }>;
      color: string;
      path: LatLng[];
      geometry: RouteGeometry;
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
      if (motionRaf.current !== null) cancelAnimationFrame(motionRaf.current);
      motionRaf.current = null;
      tokenPin.current = null;
      tokenPos.current = null;
      tokenTarget.current = null;
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
    tokenTarget.current = null;

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
        casing: MapPolyline;
        line: MapPolyline;
        extras: Array<{ casing: MapPolyline; line: MapPolyline; color: string; mode: string }>;
        color: string;
        path: LatLng[];
        geometry: RouteGeometry;
        fromId: string;
        toId: string;
        doneAt: number;
        source: string;
      }> = [];
      // A hotel -> stop leg and its return use the same road. Count the pair so
      // the second one can be drawn a few metres beside the first instead of
      // hiding under it (the token still follows the true path).
      const seenPairs = new Map<string, number>();
      const pending: Array<{
        path: LatLng[];
        color: string;
        done: boolean;
        fromId: string;
        toId: string;
        doneAt: number;
        source: string;
        original: LatLng[];
        steps: MapStep[];
        shift: number;
      }> = [];

      const draw = (
        path: LatLng[],
        mode: TravelMode,
        fromId: string,
        toId: string,
        doneAt: number,
        source: string,
        steps: MapStep[] = []
      ) => {
        if (path.length < 2) return;
        const pair = fromId <= toId ? `${fromId}|${toId}` : `${toId}|${fromId}`;
        const repeat = seenPairs.get(pair) ?? 0;
        seenPairs.set(pair, repeat + 1);
        const reversed = fromId > toId;
        const side = repeat % 2 === 1 ? 1 : -1;
        // Offset by ~4 screen pixels, so the separation is visible at any zoom
        // without pushing the line onto a different street when zoomed in.
        const zoom = engine.getZoom();
        const metresPerPixel = (156543.03392 * Math.cos((path[0].lat * Math.PI) / 180)) / 2 ** zoom;
        const shift =
          repeat === 0
            ? 0
            : side * (reversed ? -1 : 1) * 4 * metresPerPixel * Math.ceil(repeat / 2);
        const linePath = shift === 0 ? path : offsetPath(path, shift);
        pending.push({
          path: linePath,
          color: MODE[mode].color,
          done: now >= doneAt,
          fromId,
          toId,
          doneAt,
          source,
          original: path,
          steps,
          shift,
        });
      };

      if (legs.length) {
        for (const leg of legs) {
          const path = leg.path.length > 1 ? leg.path.map(([lat, lng]) => ({ lat, lng })) : null;
          if (!path) continue;
          const target = itemsById.get(leg.to_id);
          const doneAt = target ? minutesOf(target.start) ?? 0 : lastEnd;
          draw(path, modeOf(leg.mode), leg.from_id, leg.to_id, doneAt, leg.source, leg.steps ?? []);
        }
      } else {
        for (let index = 1; index < routeGeo.length; index += 1) {
          const from = routeGeo[index - 1];
          const to = routeGeo[index];
          const mode = modeForDistance(kmBetween(from.geo, to.geo));
          draw([from.geo, to.geo], mode, from.id, to.id, minutesOf(to.start) ?? 0, "estimate");
        }
      }

      // All casings first, then all coloured lines: one leg's white outline can
      // never cover the next leg's coloured line at a shared corner.
      const casings = pending.map((entry) =>
        engine.polyline(entry.path, { color: "#FFFFFF", weight: 10, opacity: 0.98 })
      );
      for (let index = 0; index < pending.length; index += 1) {
        const entry = pending[index];
        const line = engine.polyline(entry.path, {
          color: entry.color,
          weight: 6,
          // Solid and bold: the route is the thing the eye should find first.
          // "Ahead" is a lighter tint of the same solid line, never a dash pattern.
          opacity: entry.done ? 0.95 : 0.72,
          dashed: false,
        });
        // The token follows the original road, not the visual offset.
        drawn.push({
          casing: casings[index],
          line,
          extras: [],
          color: entry.color,
          path: entry.original,
          geometry: buildGeometry(entry.original),
          fromId: entry.fromId,
          toId: entry.toId,
          doneAt: entry.doneAt,
          source: entry.source,
        });
      }

      // Non-walking steps inside a WALK route: draw them over the base line so a
      // ferry crossing is visibly a ferry, never a footpath across water.
      const extraPaths: Array<{ index: number; path: LatLng[]; color: string; mode: string }> = [];
      pending.forEach((entry, index) => {
        for (const step of entry.steps) {
          if (step.mode === "walk" || step.path.length < 2) continue;
          let stepPath: LatLng[] = step.path.map(([lat, lng]) => ({ lat, lng }));
          if (entry.shift !== 0) stepPath = offsetPath(stepPath, entry.shift);
          extraPaths.push({
            index,
            path: stepPath,
            color: STEP_COLOR[step.mode] ?? STEP_COLOR.transit,
            mode: step.mode,
          });
        }
      });
      const extraCasings = extraPaths.map((item) =>
        engine.polyline(item.path, { color: "#FFFFFF", weight: 10, opacity: 0.98 })
      );
      extraPaths.forEach((item, index) => {
        const line = engine.polyline(item.path, {
          color: item.color,
          weight: 7,
          opacity: 0.95,
          // Solid violet + ferry icon: unambiguous without relying on colour alone.
          dashed: false,
        });
        drawn[item.index].extras.push({
          casing: extraCasings[index],
          line,
          color: item.color,
          mode: item.mode,
        });
        if (item.mode === "ferry") {
          const midpoint = pointAlong(buildGeometry(item.path), 0.5);
          engine.pin(midpoint, {
            anchor: [-15, -15],
            size: 30,
            svgIcon: FERRY_ICON,
            interactive: false,
            z: 850,
            title: token?.label ?? "Ferry",
          });
        }
      });
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

    // One route leg at full strength: the one being travelled (or the next one
    // from the active stop). Everything else recedes so the eye has one line.
    const active =
      token && token.moving && token.fromId && token.toId
        ? { fromId: token.fromId, toId: token.toId }
        : currentItemId
          ? routeRef.current.legs.find((leg) => leg.fromId === currentItemId) ?? null
          : null;
    const hasActive = Boolean(active);
    for (const leg of routeRef.current.legs) {
      const done = clock >= leg.doneAt;
      const isActive = Boolean(
        active && leg.fromId === active.fromId && leg.toId === active.toId
      );
      const faded = hasActive && !isActive;
      leg.casing.setStyle({
        color: "#FFFFFF",
        weight: isActive ? 11 : faded ? 6 : 10,
        opacity: isActive ? 0.98 : faded ? 0.5 : 0.95,
      });
      leg.line.setStyle({
        color: leg.color,
        weight: isActive ? 7 : faded ? 3 : 6,
        opacity: isActive ? 0.98 : faded ? 0.2 : done ? 0.9 : 0.72,
        dashed: false,
      });
      for (const extra of leg.extras) {
        extra.casing.setStyle({
          color: "#FFFFFF",
          weight: isActive ? 11 : faded ? 6 : 10,
          opacity: isActive ? 0.98 : faded ? 0.5 : 0.95,
        });
        extra.line.setStyle({
          color: extra.color,
          weight: isActive ? 8 : faded ? 3.5 : 7,
          opacity: isActive ? 0.98 : faded ? 0.2 : 0.95,
          dashed: false,
        });
      }
    }

    if (selectedItemId !== lastSelected.current) {
      lastSelected.current = selectedItemId;
      const selected = withGeo.find((item) => item.id === selectedItemId);
      if (selected) {
        engine.panTo(selected.geo);
        engine.setZoom(15);
      }
    }
  }, [layersBuilt, clock, currentItemId, selectedItemId, updatedIds, hotelChanged, status, points, token]);

  // --- playback token ----------------------------------------------------
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || status !== "ready") return;

    if (!token) {
      tokenPin.current?.remove();
      tokenPin.current = null;
      tokenPos.current = null;
      tokenTarget.current = null;
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
    if (movingLeg && movingLeg.geometry.points.length > 1) {
      target = pointAlong(movingLeg.geometry, token.progress);
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
      if (kmBetween(tokenPos.current ?? target, target) > 50) {
        tokenPin.current.moveTo(target);
        tokenPos.current = target;
      }
    }
    // The motion loop follows this target every frame, so a new target never
    // restarts an easing animation and the token does not trail the clock.
    tokenTarget.current = target;

    // One animated pan when a new leg starts; with follow on, the motion loop
    // owns the camera so the two never fight.
    const legKey = `${token.fromId ?? ""}|${token.toId ?? ""}|${token.moving ? "go" : "stay"}`;
    if (token.moving && legKey !== lastLeg.current && !selectedItemId && !follow) {
      lastLeg.current = legKey;
      engine.panTo(target);
    }
  }, [token, follow, speed, status, generation, layersBuilt, legs, selectedItemId]);

  // --- token motion + follow, once per frame, outside React ---------------
  useEffect(() => {
    if (status !== "ready") return;
    const reduced = prefersReducedMotion();
    let lastCamera = 0;
    const step = (now: number) => {
      const target = tokenTarget.current;
      const position = tokenPos.current;
      const engine = engineRef.current;
      if (target && position && engine) {
        const distance = kmBetween(position, target);
        if (distance > 0.0005) {
          const k = reduced ? 1 : 0.42;
          const next = {
            lat: position.lat + (target.lat - position.lat) * k,
            lng: position.lng + (target.lng - position.lng) * k,
          };
          tokenPin.current?.moveTo(next);
          tokenPos.current = next;
          if (
            followRef.current &&
            !selectedRef.current &&
            !engine.isDragging() &&
            (reduced || now - lastCamera >= 33)
          ) {
            lastCamera = now;
            engine.setCenter(next);
          }
        } else if (distance > 0) {
          tokenPin.current?.moveTo(target);
          tokenPos.current = target;
        }
      }
      motionRaf.current = requestAnimationFrame(step);
    };
    motionRaf.current = requestAnimationFrame(step);
    return () => {
      if (motionRaf.current !== null) cancelAnimationFrame(motionRaf.current);
      motionRaf.current = null;
    };
  }, [status, generation]);

  return (
    <div className="pf-map__canvas">
      <div
        ref={containerRef}
        className="pf-map__surface"
        data-provider="google"
        role="application"
        aria-label={t("map.of", { place: day ? `${plan.destination.name}, ${day.title}` : plan.destination.name })}
      />
      <div className="pf-map__place">
        <span className="pf-map__place-icon"><Icon name="map" size={14} /></span>
        <div>
          <span className="pf-map__place-kicker">{day?.title ?? t("map.chooseDay")}</span>
          <strong>{plan.destination.name}</strong>
        </div>
      </div>
      <div className="pf-map__toolbar">
        <IconButton icon="zoomIn" label={t("map.zoomIn")} onClick={() => engineRef.current?.zoomIn()} />
        <IconButton icon="zoomOut" label={t("map.zoomOut")} onClick={() => engineRef.current?.zoomOut()} />
        <IconButton icon={showRoute ? "layers" : "route"} label={t("map.toggleRoute")} onClick={onToggleRoute} />
        <IconButton icon="close" label={t("place.clear")} onClick={() => onSelectItem(null)} />
      </div>
      <div className="pf-map__legend" aria-hidden="true">
        <span><i className="pf-legend-dot pf-legend-dot--sight" />{t("map.sight")}</span>
        <span><i className="pf-legend-dot pf-legend-dot--hotel" />{t("map.hotel")}</span>
        <span className="pf-map__provider">Google Maps</span>
      </div>
      <div className="pf-map__clock" aria-live="polite">
        <strong>{clockText(clock)}</strong>
        <span>{currentName ?? t("map.beforeFirstStop")}</span>
      </div>
      {status === "no-key" ? (
        <span className="pf-map__notice">{t("map.noKey")}</span>
      ) : null}
      {status === "failed" ? <span className="pf-map__notice">{t("map.failed")}</span> : null}
    </div>
  );
}
