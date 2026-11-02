/** Map pin visuals for the Google Maps engine.
 *
 * The DOM version (`pinHtml`) is used for labels; the SVG version
 * (`pinSvg`) becomes an image icon for Google markers.
 */

import type { TravelMode } from "./geo";

export interface PinVisual {
  label: string;
  variant: "sight" | "meal" | "hotel";
  active?: boolean;
  dimmed?: boolean;
  updated?: boolean;
  now?: boolean;
  visited?: boolean;
}

const TOKEN_GLYPH: Record<TravelMode, string> = {
  walk:
    '<circle cx="13" cy="4.5" r="2"/><path d="m10.5 21 2.2-5.4 2.1 2.1V21M9 11.5l3-2.4 3 3 2.5.8M12 9.1 9.8 14"/>',
  metro:
    '<rect x="5" y="3.5" width="14" height="13" rx="3"/><path d="M8 20.5l2-4M16 20.5l-2-4M5 10h14M9 6.5h6"/>',
  drive:
    '<path d="M4 16.5 6 10h12l2 6.5v3H4z"/><path d="M6 10l1.4-3.5h9.2L18 10"/>',
  flight: '<path d="M21 12 3 6l3.5 6L3 18l18-6z"/>',
};

export function tokenHtml(mode: TravelMode, moving: boolean): string {
  return `<span class="pf-map-token pf-map-token--${mode}" data-moving="${moving}">
    <span class="pf-map-token__halo"></span>
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="white" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round">${TOKEN_GLYPH[mode]}</svg>
  </span>`;
}

export function pinHtml(visual: PinVisual): string {
  return `<span class="pf-map-pin pf-map-pin--${visual.variant}${visual.active ? " is-active" : ""}${
    visual.dimmed ? " is-dimmed" : ""
  }${visual.updated ? " is-updated" : ""}${visual.now ? " is-now" : ""}${
    visual.visited === false ? " is-future" : ""
  }"><span>${visual.label}</span></span>`;
}

const MODE_COLOR: Record<TravelMode, string> = {
  walk: "#7A746B",
  metro: "#0B7FA6",
  drive: "#C25A1F",
  flight: "#E08E2B",
};

function dataUri(svg: string): string {
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

/** Google marker icon: the same circle language as the DOM pin. */
export function pinSvg(visual: PinVisual): string {
  const hotel = visual.variant === "hotel";
  const meal = visual.variant === "meal";
  const size = hotel ? 34 : 30;
  const pad = 3;
  const radius = size / 2 - pad;
  const centre = size / 2;
  const fill = hotel ? "#1B1915" : meal ? "#FBF3DF" : "#FFFFFF";
  const stroke = visual.active ? "#CC0D0D" : hotel ? "#FFFFFF" : meal ? "#8C6A2E" : "#1B1915";
  const strokeWidth = visual.active ? 3 : 2;
  const ink = hotel ? "#FFFFFF" : meal ? "#5E471D" : "#1B1915";
  const opacity = visual.dimmed ? 0.62 : 1;
  const fontSize = hotel ? 13 : 12;
  const updated = visual.updated
    ? `<circle cx="${size - 4}" cy="4" r="4" fill="#2E7D4F" stroke="#FFFFFF" stroke-width="1.5"/>`
    : "";
  const now = visual.now
    ? `<circle cx="${centre}" cy="${centre}" r="${radius + 5}" fill="none" stroke="#CC0D0D" stroke-width="2" opacity="0.45"/>`
    : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    ${now}
    <circle cx="${centre}" cy="${centre}" r="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeWidth}" opacity="${opacity}"/>
    <text x="${centre}" y="${centre + fontSize * 0.36}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="${fontSize}" font-weight="750" fill="${ink}" opacity="${opacity}">${visual.label}</text>
    ${updated}
  </svg>`;
  return dataUri(svg.replace(/\s+/g, " "));
}

export function tokenSvg(mode: TravelMode, moving: boolean): string {
  const colour = MODE_COLOR[mode];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
    <circle cx="20" cy="20" r="16" fill="none" stroke="${colour}" stroke-width="3" opacity="${moving ? 0.55 : 0.3}"/>
    <circle cx="20" cy="20" r="12" fill="${colour}" stroke="#FFFFFF" stroke-width="2.5"/>
    <g transform="translate(8,8) scale(1)" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${TOKEN_GLYPH[mode]}</g>
  </svg>`;
  return dataUri(svg.replace(/\s+/g, " "));
}
