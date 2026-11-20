// Which of the spatial workspace's own panels are showing, and where the floating ones were dragged.
// Remembered per browser; keys are per layout, so hiding Atlas's summary leaves Journal's alone.
import { useRef, useState } from 'react';
import type React from 'react';

export type PanelKey = 'title' | 'sheet' | 'strip' | 'rail';
export type LayoutId = 'atlas' | 'outline' | 'journal';
interface Offset { x: number; y: number }

export const PANELS: Record<LayoutId, { key: PanelKey; label: string }[]> = {
  atlas: [{ key: 'title', label: 'Summary' }, { key: 'sheet', label: 'Details' }, { key: 'strip', label: 'Route' }],
  outline: [{ key: 'rail', label: 'Outline' }, { key: 'sheet', label: 'Details' }],
  journal: [{ key: 'title', label: 'Summary' }],
};

const HIDDEN_KEY = 'tripverse-sv-hidden';
const OFFSET_KEY = 'tripverse-sv-offsets';
const load = <T,>(key: string, fallback: T): T => { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; } };
const store = (key: string, value: unknown) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* preference only */ } };
const clamp = (value: number, lo: number, hi: number) => Math.min(Math.max(value, Math.min(lo, hi)), Math.max(lo, hi));

// How far a panel may move and still sit inside its positioned parent, as offsets.
function range(el: HTMLElement, from: Offset) {
  const box = (el.offsetParent as HTMLElement | null)?.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  if (!box) return { lo: from, hi: from };
  return {
    lo: { x: from.x + box.left - r.left + 8, y: from.y + box.top - r.top + 8 },
    hi: { x: from.x + box.right - r.right - 8, y: from.y + box.bottom - r.bottom - 8 },
  };
}

export function useSpatialPanels(layout: LayoutId) {
  const [hidden, setHidden] = useState<Record<string, boolean>>(() => load(HIDDEN_KEY, {}));
  const [offsets, setOffsets] = useState<Record<string, Offset>>(() => load(OFFSET_KEY, {}));
  const drag = useRef<{ id: string; el: HTMLElement; px: number; py: number; from: Offset; last: Offset; lo: Offset; hi: Offset } | null>(null);
  const id = (key: PanelKey) => `${layout}:${key}`;

  const shown = (key: PanelKey) => !hidden[id(key)];
  const toggle = (key: PanelKey, show = !shown(key)) => setHidden((prev) => {
    const next = { ...prev, [id(key)]: !show };
    store(HIDDEN_KEY, next);
    return next;
  });
  const place = (key: PanelKey, offset: Offset) => setOffsets((prev) => {
    const next = { ...prev, [id(key)]: offset };
    store(OFFSET_KEY, next);
    return next;
  });
  /** CSS variables the floating panel reads; container queries drop them when the layout stacks. */
  const offsetStyle = (key: PanelKey) => {
    const o = offsets[id(key)];
    return (o ? { '--drag-x': `${o.x}px`, '--drag-y': `${o.y}px` } : undefined) as React.CSSProperties | undefined;
  };

  /** Props for a panel's move handle: pointer drag, arrow keys, double-click to put it back. */
  const gripProps = (key: PanelKey, label: string) => ({
    type: 'button' as const,
    className: 'tv-sv__move',
    'aria-label': `Move ${label}. Arrow keys move it; double-click puts it back.`,
    title: 'Drag to move · double-click to put back',
    onPointerDown: (event: React.PointerEvent<HTMLButtonElement>) => {
      if (event.button !== 0) return;
      const el = event.currentTarget.parentElement as HTMLElement;
      const from = offsets[id(key)] || { x: 0, y: 0 };
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { id: id(key), el, px: event.clientX, py: event.clientY, from, last: from, ...range(el, from) };
      el.classList.add('is-dragging');
    },
    onPointerMove: (event: React.PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      d.last = { x: clamp(d.from.x + event.clientX - d.px, d.lo.x, d.hi.x), y: clamp(d.from.y + event.clientY - d.py, d.lo.y, d.hi.y) };
      d.el.style.setProperty('--drag-x', `${d.last.x}px`);
      d.el.style.setProperty('--drag-y', `${d.last.y}px`);
    },
    onPointerUp: () => {
      const d = drag.current;
      if (!d) return;
      drag.current = null;
      d.el.classList.remove('is-dragging');
      place(key, d.last);
    },
    onPointerCancel: () => { if (drag.current) { drag.current.el.classList.remove('is-dragging'); drag.current = null; } },
    onDoubleClick: (event: React.MouseEvent<HTMLButtonElement>) => {
      const el = event.currentTarget.parentElement as HTMLElement;
      el.style.removeProperty('--drag-x');
      el.style.removeProperty('--drag-y');
      place(key, { x: 0, y: 0 });
    },
    onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => {
      const delta = ({ ArrowLeft: [-24, 0], ArrowRight: [24, 0], ArrowUp: [0, -24], ArrowDown: [0, 24] } as Record<string, number[]>)[event.key];
      if (!delta) return;
      event.preventDefault();
      const from = offsets[id(key)] || { x: 0, y: 0 };
      const { lo, hi } = range(event.currentTarget.parentElement as HTMLElement, from);
      place(key, { x: clamp(from.x + delta[0], lo.x, hi.x), y: clamp(from.y + delta[1], lo.y, hi.y) });
    },
  });

  return { list: PANELS[layout], shown, toggle, offsetStyle, gripProps };
}
