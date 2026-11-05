/** Google Maps engine: loader plus the shared MapEngine implementation.
 *
 * Pins are classic markers with SVG icons generated from the shared PinVisual.
 * The modern AdvancedMarkerElement and OverlayView both add a DOM host that
 * interferes with pointer events (or, for OverlayView, with vector rendering),
 * so the icon-based marker is the dependable option here.
 */

import type { MapEngine, MapPin, MapPinOptions, MapPolylineStyle } from "./engine";
import type { LatLng } from "./geo";
import { pinSvg } from "./pins";

type AnyMap = any;

declare global {
  interface Window {
    google?: AnyMap;
    __pfGoogleMapsReady?: () => void;
  }
}

let loader: Promise<void> | null = null;
let loadedKey = "";

export function loadGoogleMaps(key: string): Promise<void> {
  if (window.google?.maps) return Promise.resolve();
  if (loader && loadedKey === key) return loader;
  loadedKey = key;
  loader = new Promise<void>((resolve, reject) => {
    const callback = "__pfGoogleMapsReady";
    window[callback] = () => resolve();
    const script = document.createElement("script");
    script.src =
      "https://maps.googleapis.com/maps/api/js" +
      `?key=${encodeURIComponent(key)}` +
      "&v=weekly&language=en&region=HK&loading=async" +
      `&callback=${callback}`;
    script.async = true;
    script.defer = true;
    script.onerror = () => reject(new Error("Google Maps script failed to load"));
    document.head.appendChild(script);
    window.setTimeout(() => {
      if (!window.google?.maps) reject(new Error("Google Maps did not become ready"));
    }, 20000);
  });
  return loader;
}

export function createGoogleEngine(el: HTMLElement, centre: LatLng): MapEngine {
  const g: AnyMap = window.google;
  const map = new g.maps.Map(el, {
    center: { lat: centre.lat, lng: centre.lng },
    zoom: 12,
    disableDefaultUI: true,
    clickableIcons: false,
    gestureHandling: "greedy",
    keyboardShortcuts: false,
    backgroundColor: "#f4f0e7",
    // A quieter basemap so the bold route is what the eye lands on first.
    styles: [
      { featureType: "poi.business", elementType: "labels", stylers: [{ visibility: "off" }] },
      { featureType: "transit", elementType: "labels.icon", stylers: [{ visibility: "off" }] },
      // Basemap transit lines look like a route and compete with ours; the app
      // draws the actual legs, so hide the decorative network.
      { featureType: "transit", elementType: "geometry", stylers: [{ visibility: "off" }] },
      { featureType: "road", elementType: "labels", stylers: [{ visibility: "simplified" }] },
      { featureType: "road", elementType: "geometry", stylers: [{ lightness: 18 }] },
      { featureType: "landscape.man_made", elementType: "geometry", stylers: [{ lightness: 8 }] },
      { elementType: "geometry", stylers: [{ saturation: -28 }] },
      { elementType: "labels.text.fill", stylers: [{ color: "#6b7280" }] },
    ],
  });

  (el as unknown as { __pfMap?: AnyMap }).__pfMap = map;
  g.maps.event.addListener(map, "idle", () => {
    el.dataset.zoom = String(map.getZoom() ?? "");
  });

  // Follow must never fight a drag: remember it, and hold off briefly afterwards.
  let dragging = false;
  let lastDragEnd = 0;
  g.maps.event.addListener(map, "dragstart", () => {
    dragging = true;
  });
  g.maps.event.addListener(map, "dragend", () => {
    dragging = false;
    lastDragEnd = performance.now();
  });

  const overlays: AnyMap[] = [];

  // Google has no dashArray; a dotted repeat reads as "still ahead".
  const dashIcons = (style: MapPolylineStyle) =>
    style.dashed
      ? [
          {
            icon: {
              path: g.maps.SymbolPath.CIRCLE,
              scale: 2.2,
              fillOpacity: 1,
              fillColor: style.color,
              strokeOpacity: 0,
            },
            offset: "0",
            repeat: "14px",
          },
        ]
      : undefined;

  const polyline = (points: LatLng[], style: MapPolylineStyle) => {
    const line = new g.maps.Polyline({
      map,
      path: points,
      strokeColor: style.color,
      strokeOpacity: style.opacity,
      strokeWeight: style.weight,
      geodesic: false,
      icons: dashIcons(style),
      zIndex: style.dashed ? 2 : 3,
    });
    overlays.push(line);
    return {
      setStyle(next: MapPolylineStyle) {
        line.setOptions({
          strokeColor: next.color,
          strokeOpacity: next.opacity,
          strokeWeight: next.weight,
          icons: dashIcons(next),
          zIndex: next.dashed ? 2 : 3,
        });
      },
      remove() {
        line.setMap(null);
      },
    };
  };

  const pin = (point: LatLng, options: MapPinOptions): MapPin => {
    const url =
      options.svgIcon ??
      (options.visual ? pinSvg(options.visual) : pinSvg({ label: "•", variant: "sight" }));
    const size = options.size ?? (options.visual?.variant === "hotel" ? 34 : 30);
    const marker = new g.maps.Marker({
      map,
      position: point,
      title: options.title,
      zIndex: options.z ?? 1,
      clickable: options.interactive !== false,
      icon: {
        url,
        scaledSize: new g.maps.Size(size, size),
        anchor: new g.maps.Point(size / 2, size / 2),
      },
    });
    if (options.onClick) {
      marker.addListener("click", () => options.onClick?.());
    }
    overlays.push(marker);

    return {
      moveTo(next) {
        marker.setPosition(next);
      },
      setHtml() {
        /* icon markers carry no DOM body */
      },
      setIcon(next) {
        marker.setIcon({
          url: next,
          scaledSize: new g.maps.Size(size, size),
          anchor: new g.maps.Point(size / 2, size / 2),
        });
      },
      setZIndex(z) {
        marker.setZIndex(z);
      },
      remove() {
        marker.setMap(null);
      },
    };
  };

  return {
    fit(points, padding) {
      if (!points.length) return;
      if (points.length === 1) {
        map.setCenter(points[0]);
        map.setZoom(14);
        return;
      }
      const bounds = new g.maps.LatLngBounds();
      for (const point of points) bounds.extend(point);
      map.fitBounds(bounds, {
        top: padding[0],
        right: padding[1],
        bottom: padding[2],
        left: padding[3],
      });
    },
    panTo(point) {
      map.panTo(point);
    },
    setCenter(point) {
      map.setCenter(point);
    },
    isDragging() {
      return dragging || performance.now() - lastDragEnd < 1200;
    },
    getZoom() {
      return map.getZoom() ?? 12;
    },
    setZoom(zoom) {
      map.setZoom(zoom);
    },
    zoomIn() {
      map.setZoom((map.getZoom() ?? 12) + 1);
    },
    zoomOut() {
      map.setZoom((map.getZoom() ?? 12) - 1);
    },
    clear() {
      for (const overlay of overlays.splice(0)) {
        if (typeof overlay.setMap === "function") overlay.setMap(null);
        else overlay.map = null;
      }
    },
    polyline,
    pin,
    destroy() {
      this.clear();
      map.setOptions({});
    },
  };
}
