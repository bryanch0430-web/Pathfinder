/*
 * Sketchbook pages as plain data: layoutSketch() builds them from the trip document,
 * SketchPageSvg draws them. Keeping the two apart makes the layout testable and lets
 * the PDF export (Session 6) reuse the same pages.
 */
import type { DoodleName } from './doodles';

/** Colour roles; the renderer maps them to theme tokens. */
export type Tone = 'ink' | 'muted' | 'accent' | 'red' | 'green';
export type Point = [number, number];

export type SketchElement = { id: string } & (
  | { kind: 'text'; x: number; y: number; lines: string[]; size: number; tone: Tone; anchor?: 'start' | 'middle' | 'end'; bold?: boolean }
  /** `label` names the place a card stands for. */
  | { kind: 'box'; x: number; y: number; w: number; h: number; tone: Tone; dashed?: boolean; label?: string }
  | { kind: 'ellipse'; cx: number; cy: number; w: number; h: number; tone: Tone; fill?: boolean }
  /** A hand-drawn line through the points; `curve` bends it, `arrow` adds a head at the end. */
  | { kind: 'path'; points: Point[]; tone: Tone; dashed?: boolean; arrow?: boolean; curve?: boolean }
  | { kind: 'doodle'; name: DoodleName; x: number; y: number; size: number; tone: Tone }
  | { kind: 'note'; x: number; y: number; w: number; h: number; rotate: number; title: string; lines: string[]; tag: string | null }
  | { kind: 'stamp'; x: number; y: number; w: number; text: string; tone: Tone; rotate: number }
  | { kind: 'highlight'; x: number; y: number; w: number; h: number }
);

export interface SketchPage {
  id: string;
  kind: 'overview' | 'day';
  day: number | null;
  title: string;
  /** Text alternative for the whole page, from the trip document only. */
  alt: string;
  /** Page size in px: A4 landscape, or the same sheet upright for phones. */
  w: number;
  h: number;
  elements: SketchElement[];
}

/** A4 landscape at 96 dpi, so pages print and export 1:1. */
export const PAGE = { w: 1123, h: 794, margin: 48 } as const;
