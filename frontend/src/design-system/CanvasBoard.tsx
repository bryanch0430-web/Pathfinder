import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { useI18n } from "../lib/i18n";
import { Icon } from "./icons";

export interface Rect { x: number; y: number; w: number; h: number }
interface Size { w: number; h: number }
type Id = "chat" | "plan" | "map" | "budget" | "exchange" | "sketch" | "videos" | "trending" | "profile";

const KEY = "pathfinder-canvas";
const GAP = 12;
const EDGE = 16;
const COL_GAP = 16;
const BOTTOM = 16;
/** Bump when the default arrangement changes, so a saved layout from an older default is replaced once. */
const LAYOUT_VERSION = 2;
const MIN: Record<Id, Size> = {
  chat: { w: 220, h: 200 },
  plan: { w: 280, h: 200 },
  map: { w: 240, h: 200 },
  budget: { w: 280, h: 220 },
  exchange: { w: 280, h: 180 },
  sketch: { w: 320, h: 240 },
  videos: { w: 240, h: 180 },
  trending: { w: 260, h: 200 },
  profile: { w: 320, h: 280 },
};

interface Saved {
  v: number;
  open: Record<Id, boolean>;
  rects: Partial<Record<Id, Rect>>;
  bar?: { x: number; y: number };
}

const BAR_HOME = { x: EDGE, y: 12 };

const DEFAULT_OPEN: Record<Id, boolean> = {
  chat: true,
  plan: true,
  map: true,
  budget: false,
  exchange: false,
  sketch: false,
  videos: false,
  trending: false,
  profile: false,
};

function freshSaved(): Saved {
  return { v: LAYOUT_VERSION, open: { ...DEFAULT_OPEN }, rects: {} };
}

function readSaved(): Saved {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return freshSaved();
    const parsed = JSON.parse(raw) as Partial<Saved>;
    if (parsed.v !== LAYOUT_VERSION) return freshSaved();
    return {
      v: LAYOUT_VERSION,
      open: { ...DEFAULT_OPEN, ...parsed.open, plan: parsed.open?.plan !== false },
      rects: parsed.rects ?? {},
      bar: parsed.bar && Number.isFinite(parsed.bar.x) && Number.isFinite(parsed.bar.y) ? parsed.bar : undefined,
    };
  } catch {
    return freshSaved();
  }
}

function clampRect(rect: Rect, board: Size, min: Size): Rect {
  const w = Math.min(Math.max(rect.w, min.w), Math.max(min.w, board.w - GAP * 2));
  const h = Math.min(Math.max(rect.h, min.h), Math.max(min.h, board.h - GAP * 2));
  return {
    w,
    h,
    x: Math.min(Math.max(rect.x, GAP), Math.max(GAP, board.w - w - GAP)),
    y: Math.min(Math.max(rect.y, GAP), Math.max(GAP, board.h - h - GAP)),
  };
}

/** Room under the toolbar, a little taller on a tall screen, never eating the columns. */
function bandTop(boardH: number): number {
  return Math.round(Math.min(88, Math.max(72, boardH * 0.085)));
}

/**
 * The resting arrangement: chat, plan and the map share one top and one bottom.
 * Chat is the narrow column, the plan the wide middle, the map the right column.
 * Shares are of the board, so a wider or taller screen keeps the same picture.
 */
function defaults(board: Size): Record<Id, Rect> {
  const top = bandTop(board.h);
  const bodyH = Math.max(MIN.plan.h, board.h - top - BOTTOM);
  const inner = Math.max(0, board.w - EDGE * 2 - COL_GAP * 2);
  const chatFloor = MIN.chat.w;
  const mapFloor = MIN.map.w;
  const planFloor = MIN.plan.w;
  let chatW = Math.round(inner * 0.18);
  let mapW = Math.round(inner * 0.26);
  let planW = inner - chatW - mapW;
  const floors = chatFloor + mapFloor + planFloor;
  if (inner >= floors && (chatW < chatFloor || mapW < mapFloor || planW < planFloor)) {
    chatW = Math.max(chatW, chatFloor);
    mapW = Math.max(mapW, mapFloor);
    planW = inner - chatW - mapW;
    if (planW < planFloor) {
      const deficit = planFloor - planW;
      const takeChat = Math.min(Math.max(0, chatW - chatFloor), deficit);
      chatW -= takeChat;
      mapW -= Math.min(Math.max(0, mapW - mapFloor), deficit - takeChat);
      planW = inner - chatW - mapW;
    }
  } else if (inner > 0 && inner < floors) {
    const scale = inner / floors;
    chatW = Math.round(chatFloor * scale);
    mapW = Math.round(mapFloor * scale);
    planW = Math.max(0, inner - chatW - mapW);
  }
  if (inner >= floors && (chatW > 340 || mapW > 480)) {
    chatW = Math.min(chatW, 340);
    mapW = Math.min(mapW, 480);
    planW = inner - chatW - mapW;
  }

  const xChat = EDGE;
  const xPlan = EDGE + chatW + COL_GAP;
  const xMap = xPlan + planW + COL_GAP;
  const sketchW = Math.min(880, Math.max(MIN.sketch.w, Math.round(board.w * 0.56)));
  const sketchH = Math.min(bodyH, Math.max(MIN.sketch.h, Math.round(bodyH * 0.86)));
  const videoW = Math.min(Math.max(mapW, MIN.videos.w), Math.max(MIN.videos.w, Math.round(board.w * 0.28)));
  const videoH = Math.min(bodyH, Math.max(MIN.videos.h, Math.round(bodyH * 0.55)));
  const trendW = Math.min(420, Math.max(MIN.trending.w, Math.round(board.w * 0.24)));
  const trendH = Math.min(bodyH, Math.max(MIN.trending.h, Math.round(bodyH * 0.7)));

  return {
    chat: { x: xChat, y: top, w: chatW, h: bodyH },
    plan: { x: xPlan, y: top, w: planW, h: bodyH },
    map: { x: xMap, y: top, w: mapW, h: bodyH },
    sketch: {
      x: Math.max(EDGE, Math.round((board.w - sketchW) / 2)),
      y: top,
      w: sketchW,
      h: sketchH,
    },
    videos: {
      x: Math.max(EDGE, board.w - EDGE - videoW),
      y: top + Math.max(0, bodyH - videoH),
      w: videoW,
      h: videoH,
    },
    budget: {
      x: Math.max(EDGE, xMap),
      y: top + Math.max(0, bodyH - 280),
      w: Math.max(MIN.budget.w, Math.min(mapW, 360)),
      h: 280,
    },
    exchange: {
      x: Math.max(EDGE, xMap),
      y: top + Math.max(0, Math.round(bodyH * 0.18)),
      w: Math.max(MIN.exchange.w, Math.min(mapW, 360)),
      h: 220,
    },
    trending: {
      x: EDGE,
      y: top + Math.max(0, bodyH - trendH),
      w: trendW,
      h: trendH,
    },
    profile: {
      x: Math.max(EDGE, Math.round((board.w - 440) / 2)),
      y: top + 28,
      w: Math.min(460, Math.max(MIN.profile.w, 420)),
      h: Math.min(560, Math.max(MIN.profile.h, Math.round(bodyH * 0.72))),
    },
  };
}

function useBoardSize(ref: RefObject<HTMLElement | null>): Size | null {
  const [size, setSize] = useState<Size | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const box = el.getBoundingClientRect();
      setSize({ w: Math.round(box.width), h: Math.round(box.height) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

let topZ = 2;

function Window({
  title,
  rect,
  board,
  min,
  onRect,
  onClose,
  onReset,
  children,
}: {
  title: string;
  rect: Rect;
  board: Size;
  min: Size;
  onRect: (rect: Rect) => void;
  onClose: () => void;
  onReset: () => void;
  children: ReactNode;
}) {
  const el = useRef<HTMLElement>(null);
  const drag = useRef<{ kind: "move" | "size"; x: number; y: number; start: Rect; last: Rect } | null>(null);
  const shown = clampRect(rect, board, min);
  useEffect(() => {
    if (el.current) el.current.style.zIndex = String(++topZ);
  }, []);
  const paint = (next: Rect) => {
    const style = el.current?.style;
    if (!style) return;
    style.translate = `${next.x}px ${next.y}px`;
    style.width = `${next.w}px`;
    style.height = `${next.h}px`;
  };
  const begin = (kind: "move" | "size") => (event: React.PointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    if (kind === "move" && (event.target as HTMLElement).closest("button:not(.pf-win__grip), a, input, select, textarea")) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { kind, x: event.clientX, y: event.clientY, start: shown, last: shown };
    el.current?.classList.add(kind === "move" ? "is-moving" : "is-sizing");
  };
  const follow = (event: React.PointerEvent) => {
    const current = drag.current;
    if (!current) return;
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    current.last = clampRect(
      current.kind === "move"
        ? { ...current.start, x: current.start.x + dx, y: current.start.y + dy }
        : { ...current.start, w: current.start.w + dx, h: current.start.h + dy },
      board,
      min,
    );
    paint(current.last);
  };
  const finish = () => {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    el.current?.classList.remove("is-moving", "is-sizing");
    const next = current.last;
    const snap = 28;
    onRect(clampRect({
      ...next,
      x: next.x < snap ? GAP : next.x + next.w > board.w - snap ? board.w - next.w - GAP : next.x,
      y: next.y < snap ? GAP : next.y + next.h > board.h - snap ? board.h - next.h - GAP : next.y,
    }, board, min));
  };
  const nudge = (event: React.KeyboardEvent) => {
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
    };
    const step = delta[event.key];
    if (!step) return;
    event.preventDefault();
    const [dx, dy] = step.map((value) => value * (event.shiftKey ? 48 : 24));
    onRect(clampRect(event.shiftKey ? { ...shown, w: shown.w + dx, h: shown.h + dy } : { ...shown, x: shown.x + dx, y: shown.y + dy }, board, min));
  };

  return (
    <section
      ref={el}
      className="pf-win"
      aria-label={title}
      style={{ translate: `${shown.x}px ${shown.y}px`, width: shown.w, height: shown.h }}
      onPointerDownCapture={() => { if (el.current) el.current.style.zIndex = String(++topZ); }}
    >
      <header
        className="pf-win__bar"
        onPointerDown={begin("move")}
        onPointerMove={follow}
        onPointerUp={finish}
        onPointerCancel={finish}
        onDoubleClick={(event) => {
          if (!(event.target as HTMLElement).closest("button:not(.pf-win__grip)")) onReset();
        }}
      >
        <button type="button" className="pf-win__grip" aria-label={`Move ${title}. Arrows move, Shift and arrows resize.`} onKeyDown={nudge}>
          <i aria-hidden="true" />
        </button>
        <h2>{title}</h2>
        <button type="button" className="pf-win__close" aria-label={`Hide ${title}`} onClick={onClose}>
          <Icon name="close" size={16} />
        </button>
      </header>
      <div className="pf-win__body">{children}</div>
      <span
        className="pf-win__size"
        aria-hidden="true"
        onPointerDown={begin("size")}
        onPointerMove={follow}
        onPointerUp={finish}
        onPointerCancel={finish}
      />
    </section>
  );
}

const ORDER: Id[] = ["chat", "plan", "map", "profile", "budget", "exchange", "sketch", "videos", "trending"];
const GROUPS: Id[][] = [
  ["chat", "plan", "map"],
  ["profile", "budget", "exchange"],
  ["sketch", "videos", "trending"],
];

/** A bone canvas. Chat, the plan, the map and the sketchbook float on it. */
export function CanvasBoard({
  chat,
  plan,
  map,
  budget,
  exchange,
  profile,
  sketch,
  videos,
  trending,
}: {
  chat: ReactNode;
  plan: ReactNode;
  map: ReactNode;
  budget: ReactNode;
  exchange: ReactNode;
  profile: ReactNode;
  sketch: ReactNode;
  videos: ReactNode;
  trending: ReactNode;
}) {
  const { t } = useI18n();
  const boardRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const barDrag = useRef<{ x: number; y: number; originX: number; originY: number } | null>(null);
  const [barMoving, setBarMoving] = useState(false);
  const labels: Record<Id, string> = {
    chat: t("chat.label"),
    plan: t("panels.plan"),
    map: t("map.label"),
    budget: t("budget.tool"),
    exchange: t("budget.exchange"),
    profile: t("settings.profile"),
    sketch: t("panels.sketch"),
    videos: t("videos.title"),
    trending: t("trending.eyebrow"),
  };
  const barLabel: Record<Id, string> = {
    ...labels,
    map: t("panels.bar.map"),
    budget: t("panels.bar.budget"),
    profile: t("settings.group.profile"),
    videos: t("panels.bar.watch"),
  };
  const board = useBoardSize(boardRef);
  const [saved, setSaved] = useState<Saved>(readSaved);
  const bar = saved.bar ?? BAR_HOME;
  const placeBar = (point: { x: number; y: number }) => {
    const node = barRef.current;
    const width = node?.offsetWidth ?? 280;
    const height = node?.offsetHeight ?? 44;
    const limit = board ?? { w: 1200, h: 800 };
    return {
      x: Math.min(Math.max(8, point.x), Math.max(8, limit.w - width - 8)),
      y: Math.min(Math.max(8, point.y), Math.max(8, limit.h - height - 8)),
    };
  };
  const content: Record<Id, ReactNode> = { chat, plan, map, budget, exchange, profile, sketch, videos, trending };

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(saved));
    } catch {
      /* the layout still applies for this visit */
    }
  }, [saved]);

  const base = board ? defaults(board) : null;
  const rectFor = (id: Id) => {
    if (!board || !base) return null;
    return clampRect(saved.rects[id] ?? base[id], board, MIN[id]);
  };

  const toggle = (id: Id) => setSaved((current) => ({ ...current, open: { ...current.open, [id]: !current.open[id] } }));
  const close = (id: Id) => setSaved((current) => ({ ...current, open: { ...current.open, [id]: false } }));
  const move = (id: Id, rect: Rect) => setSaved((current) => ({ ...current, rects: { ...current.rects, [id]: rect } }));
  const resetOne = (id: Id) => setSaved((current) => {
    const next = { ...current.rects };
    delete next[id];
    return { ...current, rects: next };
  });
  const resetAll = () => setSaved(freshSaved());

  return (
    <div className="pf-canvas">
      <div className="pf-canvas__board" ref={boardRef}>
        <div
          ref={barRef}
          className={`pf-shell__bar${barMoving ? " is-moving" : ""}`}
          role="toolbar"
          aria-label={t("panels.surfaces")}
          style={{ left: bar.x, top: bar.y }}
          onPointerDown={(event) => {
            if (event.button !== 0 || (event.target as HTMLElement).closest("button")) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            barDrag.current = { x: event.clientX, y: event.clientY, originX: bar.x, originY: bar.y };
            setBarMoving(true);
          }}
          onPointerMove={(event) => {
            const drag = barDrag.current;
            if (!drag) return;
            setSaved((current) => ({
              ...current,
              bar: placeBar({ x: drag.originX + event.clientX - drag.x, y: drag.originY + event.clientY - drag.y }),
            }));
          }}
          onPointerUp={() => {
            barDrag.current = null;
            setBarMoving(false);
          }}
          onPointerCancel={() => {
            barDrag.current = null;
            setBarMoving(false);
          }}
        >
          <span className="pf-shell__drag" aria-hidden="true" title="Drag the toolbar" />
          {GROUPS.map((group, index) => (
            <span className="pf-shell__group" key={group[0]} role="group">
              {index > 0 ? <span className="pf-shell__split" aria-hidden="true" /> : null}
              {group.map((id) => (
                <button
                  key={id}
                  type="button"
                  className="pf-shell__toggle"
                  aria-pressed={saved.open[id]}
                  onClick={() => toggle(id)}
                >
                  <span>{barLabel[id]}</span>
                </button>
              ))}
            </span>
          ))}
          <button type="button" className="pf-shell__toggle pf-shell__toggle--quiet" onClick={resetAll}>
            {t("panels.reset")}
          </button>
        </div>
        {board && ORDER.map((id) => {
          if (!saved.open[id]) return null;
          const rect = rectFor(id);
          if (!rect) return null;
          return (
            <Window
              key={id}
              title={labels[id]}
              rect={rect}
              board={board}
              min={MIN[id]}
              onRect={(next) => move(id, next)}
              onClose={() => close(id)}
              onReset={() => resetOne(id)}
            >
              {content[id]}
            </Window>
          );
        })}
      </div>
    </div>
  );
}
