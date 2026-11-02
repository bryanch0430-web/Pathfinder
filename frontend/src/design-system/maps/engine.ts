/** The small, engine-agnostic surface both map providers implement. */

import type { LatLng } from "./geo";
import type { PinVisual } from "./pins";

export interface MapPolylineStyle {
  color: string;
  weight: number;
  opacity: number;
  dashed?: boolean;
}

export interface MapPinOptions {
  anchor: [number, number];
  title?: string;
  interactive?: boolean;
  z?: number;
  onClick?: () => void;
  /** DOM pin body (map labels). */
  html?: string;
  /** Marker icon data URI (Google markers). */
  svgIcon?: string;
  /** Icon edge in px (Google markers); defaults to 30/34. */
  size?: number;
  visual?: PinVisual;
}

export interface MapPolyline {
  setStyle(style: MapPolylineStyle): void;
  remove(): void;
}

export interface MapPin {
  moveTo(point: LatLng): void;
  setHtml(html: string): void;
  setIcon(svgIcon: string): void;
  setZIndex?(z: number): void;
  remove(): void;
}

export interface MapEngine {
  fit(points: LatLng[], padding: [number, number, number, number]): void;
  panTo(point: LatLng): void;
  setZoom(zoom: number): void;
  zoomIn(): void;
  zoomOut(): void;
  clear(): void;
  polyline(points: LatLng[], style: MapPolylineStyle): MapPolyline;
  pin(point: LatLng, options: MapPinOptions): MapPin;
  destroy(): void;
}
