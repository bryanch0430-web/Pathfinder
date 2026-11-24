/**
 * The draft on the real trip map: the same Google engine the finished plan uses,
 * with one colour and one dashed line per day.
 *
 * A plan that is still being written has no TripPlan, so this does not reuse
 * `TripMap`; it draws the same overlays directly. Pins are numbered instead of
 * labelled, because a dozen labels over one city are unreadable; the name is the
 * pin's tooltip. If the map key is missing or the engine fails, the caller shows
 * its schematic instead.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { getMapKeys } from "../../lib/api";
import { useI18n } from "../../lib/i18n";
import type { PartialDay, PartialRoute } from "../types";
import { createGoogleEngine, loadGoogleMaps } from "./google";
import type { MapEngine } from "./engine";
import { pinSvg } from "./pins";
import type { LatLng } from "./geo";

export const DAY_COLORS = ["#1F5F5B", "#B45309", "#6D28D9", "#0F766E", "#B91C1C", "#1D4ED8", "#A16207"];

interface DraftStop {
  name: string;
  start: string;
  end: string;
  lat: number;
  lng: number;
  day: number;
  number: number;
}

function draftStops(days: PartialDay[]): DraftStop[] {
  const stops: DraftStop[] = [];
  // Google draws later markers above earlier ones. Stops at the same address, or
  // a few metres apart, would hide pin 1 and 2 under pin 3 and look misnumbered.
  // Fan coincident pins over a small ring instead.
  const seen = new Map<string, number>();
  days.forEach((day, dayIndex) => {
    for (const stop of day.stops) {
      if (typeof stop.lat !== "number" || typeof stop.lng !== "number") continue;
      const key = `${stop.lat.toFixed(5)}:${stop.lng.toFixed(5)}`;
      const repeat = seen.get(key) ?? 0;
      seen.set(key, repeat + 1);
      const ring = repeat === 0 ? 0 : 0.00012 + repeat * 0.00006;
      const angle = (repeat * 2.4) % (Math.PI * 2);
      stops.push({
        name: stop.name,
        start: stop.start,
        end: stop.end,
        lat: stop.lat + Math.sin(angle) * ring,
        lng: stop.lng + Math.cos(angle) * ring,
        day: dayIndex,
        number: stops.length + 1,
      });
    }
  });
  return stops;
}

export function DraftMap({
  days,
  route,
  fallback,
}: {
  days: PartialDay[];
  route: PartialRoute | null;
  fallback: ReactNode;
}) {
  const { t } = useI18n();
  const host = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapEngine | null>(null);
  const fitted = useRef(0);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const keys = await getMapKeys();
        if (!keys.google?.key) throw new Error("no map key");
        await loadGoogleMaps(keys.google.key);
        if (cancelled || !host.current) return;
        map.current = createGoogleEngine(host.current, { lat: 22.3193, lng: 114.1694 });
        setReady(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      map.current?.destroy();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const engine = map.current;
    if (!engine || !ready) return;
    engine.clear();
    const stops = draftStops(days);
    const points: LatLng[] = [];
    days.forEach((day, dayIndex) => {
      const own = stops.filter((stop) => stop.day === dayIndex);
      if (own.length > 1) {
        engine.polyline(
          own.map((stop) => ({ lat: stop.lat, lng: stop.lng })),
          { color: DAY_COLORS[dayIndex % DAY_COLORS.length], weight: 5, opacity: 0.9, dashed: true },
        );
      }
      for (const stop of own) {
        engine.pin(
          { lat: stop.lat, lng: stop.lng },
          {
            anchor: [stop.lat, stop.lng],
            title: `${stop.start ? `${stop.start} · ` : ""}${stop.name}`,
            interactive: false,
            svgIcon: pinSvg({ label: String(stop.number), variant: "sight" }),
            size: 28,
            z: 4,
          },
        );
        points.push({ lat: stop.lat, lng: stop.lng });
      }
    });
    if (!route) return;
    if (route.from.lat !== null && route.from.lng !== null && route.to.lat !== null && route.to.lng !== null) {
      const from = { lat: route.from.lat, lng: route.from.lng };
      const to = { lat: route.to.lat, lng: route.to.lng };
      // A great-circle approximation: the straight line would cut across the map.
      const arc: LatLng[] = [];
      for (let step = 0; step <= 32; step += 1) {
        const t = step / 32;
        const lat = from.lat + (to.lat - from.lat) * t;
        const lng = from.lng + (to.lng - from.lng) * t;
        const lift = Math.sin(Math.PI * t) * Math.abs(to.lng - from.lng) * 0.12;
        arc.push({ lat: lat + (lat > (from.lat + to.lat) / 2 ? lift : -lift), lng });
      }
      if (points.length) {
        engine.polyline(arc, { color: "#B45309", weight: 2, opacity: 0.55, dashed: true });
      }
    }
    if (points.length > fitted.current) {
      engine.fit(points, [40, 40, 40, 40]);
      fitted.current = points.length;
    }
  }, [days, route, ready]);

  if (failed) return <>{fallback}</>;

  return (
    <div className="pf-draft-map-live">
      <div className="pf-draft-map-live__canvas" ref={host} />
      {!ready ? <div className="pf-draft-map-live__loading">{t("chat.writingPlan")}</div> : null}
      {route ? (
        <span className="pf-draft-map-live__route">
          {route.from.name} → {route.to.name}
        </span>
      ) : null}
      {days.length ? (
        <div className="pf-draft-map-live__legend">
          {days.map((day, index) => (
            <span key={`${day.date}-${index}`} className="pf-draft-map-live__day">
              <i style={{ background: DAY_COLORS[index % DAY_COLORS.length] }} />
              {day.date
                ? new Date(`${day.date}T12:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })
                : t("day.number", { n: index + 1 })}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
