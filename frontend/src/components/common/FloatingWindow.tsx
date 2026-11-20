/*
 * FloatingWindow — a panel the user can move (drag its title bar), resize (corner grip),
 * close, and reset (double-click the title bar). Keyboard: focus the grip, arrows move,
 * Shift + arrows resize. Controlled: the owner keeps each window's rect and open state.
 * With `sheet` (narrow screens) it stops floating and becomes a bottom sheet.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { CloseIcon } from '../home/v2/IconsV2';
import '../../styles/floating-window.css';

export interface Rect { x: number; y: number; w: number; h: number }
export interface Size { w: number; h: number }

export const WINDOW_GAP = 8;
const SNAP = 32; // a window dropped this close to an edge sticks to it

export function clampRect(rect: Rect, board: Size, min: Size = { w: 220, h: 140 }): Rect {
  const room = { w: Math.max(0, board.w - WINDOW_GAP * 2), h: Math.max(0, board.h - WINDOW_GAP * 2) };
  const w = Math.min(Math.max(rect.w, min.w), room.w);
  const h = Math.min(Math.max(rect.h, min.h), room.h);
  return {
    w, h,
    x: Math.min(Math.max(rect.x, WINDOW_GAP), board.w - w - WINDOW_GAP),
    y: Math.min(Math.max(rect.y, WINDOW_GAP), board.h - h - WINDOW_GAP),
  };
}

const snap = (rect: Rect, board: Size): Rect => ({
  ...rect,
  x: rect.x < SNAP ? WINDOW_GAP : rect.x + rect.w > board.w - SNAP ? board.w - rect.w - WINDOW_GAP : rect.x,
  y: rect.y < SNAP ? WINDOW_GAP : rect.y + rect.h > board.h - SNAP ? board.h - rect.h - WINDOW_GAP : rect.y,
});

/** Live content size of the element windows float in. */
export function useBoardSize(ref: React.RefObject<HTMLElement | null>): Size | null {
  const [size, setSize] = useState<Size | null>(null);
  // Measure before the first paint, so docked windows reserve their strip from the start
  // instead of the canvas jumping sideways a frame later (a 0.18 layout shift).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    setSize({ w: Math.round(box.width), h: Math.round(box.height) });
    const observer = new ResizeObserver(([entry]) => setSize({ w: Math.round(entry.contentRect.width), h: Math.round(entry.contentRect.height) }));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

// ponytail: one module-wide stacking counter; windows only ever compare with each other.
let topZ = 2;

interface Props {
  title: string;
  rect: Rect;
  board: Size;
  sheet?: boolean;
  min?: Size;
  onRectChange: (rect: Rect) => void;
  onClose: () => void;
  onReset?: () => void;
  className?: string;
  /** Extra controls in the title bar, before the close button. */
  actions?: React.ReactNode;
  children: React.ReactNode;
}

export function FloatingWindow({ title, rect, board, sheet = false, min, onRectChange, onClose, onReset, className = '', actions, children }: Props) {
  const el = useRef<HTMLElement>(null);
  const drag = useRef<{ kind: 'move' | 'size'; x: number; y: number; start: Rect; last: Rect } | null>(null);
  const shown = clampRect(rect, board, min);
  const front = () => { if (el.current) el.current.style.zIndex = String(++topZ); };
  useEffect(front, []);

  // During a drag the element is painted directly; React state commits once on release.
  const paint = (next: Rect) => {
    const style = el.current!.style;
    style.translate = `${next.x}px ${next.y}px`;
    style.width = `${next.w}px`;
    style.height = `${next.h}px`;
  };
  const begin = (kind: 'move' | 'size') => (event: React.PointerEvent<HTMLElement>) => {
    if (sheet || event.button !== 0) return;
    if (kind === 'move' && (event.target as HTMLElement).closest('button:not(.tv-fw__grip), a, input, select, textarea')) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { kind, x: event.clientX, y: event.clientY, start: shown, last: shown };
    el.current?.classList.add(kind === 'move' ? 'is-moving' : 'is-sizing');
  };
  const follow = (event: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = event.clientX - d.x;
    const dy = event.clientY - d.y;
    d.last = clampRect(d.kind === 'move' ? { ...d.start, x: d.start.x + dx, y: d.start.y + dy }
      : { ...d.start, w: d.start.w + dx, h: d.start.h + dy }, board, min);
    paint(d.last);
  };
  const finish = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    el.current?.classList.remove('is-moving', 'is-sizing');
    onRectChange(d.kind === 'move' ? clampRect(snap(d.last, board), board, min) : d.last);
  };
  const nudge = (event: React.KeyboardEvent) => {
    const delta = ({ ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] } as Record<string, number[]>)[event.key];
    if (!delta || sheet) return;
    event.preventDefault();
    const [dx, dy] = delta.map((value) => value * 24);
    onRectChange(clampRect(event.shiftKey ? { ...shown, w: shown.w + dx, h: shown.h + dy } : { ...shown, x: shown.x + dx, y: shown.y + dy }, board, min));
  };

  return <section ref={el} aria-label={title} className={`tv-fw ${sheet ? 'is-sheet' : ''} ${className}`}
    style={sheet ? undefined : { translate: `${shown.x}px ${shown.y}px`, width: shown.w, height: shown.h }}
    onPointerDownCapture={front}>
    <header className="tv-fw__bar" onPointerDown={begin('move')} onPointerMove={follow} onPointerUp={finish} onPointerCancel={finish}
      onDoubleClick={(event) => { if (!sheet && onReset && !(event.target as HTMLElement).closest('button:not(.tv-fw__grip)')) onReset(); }}>
      {!sheet && <button type="button" className="tv-fw__grip" onKeyDown={nudge}
        aria-label={`Move ${title}. Arrow keys move it, Shift and arrow keys resize it.`}
        title="Drag to move · double-click the bar to reset"><i aria-hidden="true" /></button>}
      <h2>{title}</h2>
      {actions}
      <button type="button" className="tv-fw__close" onClick={onClose} aria-label={`Close ${title}`}><CloseIcon width={14} height={14} /></button>
    </header>
    <div className="tv-fw__body">{children}</div>
    {!sheet && <span className="tv-fw__size" aria-hidden="true"
      onPointerDown={begin('size')} onPointerMove={follow} onPointerUp={finish} onPointerCancel={finish} />}
  </section>;
}
