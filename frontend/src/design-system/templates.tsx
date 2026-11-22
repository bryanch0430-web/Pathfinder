import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type Ref } from "react";
import { useI18n } from "../lib/i18n";
import { Icon } from "./icons";

/** Saved dock state. A narrow viewport uses a sheet and does not read this. */
const PANELS_KEY = "pathfinder-planner-panels";
/** v2: the left column is the day list and the right column is the chat. */
const WIDTHS_KEY = "pathfinder-planner-widths-v2";
const CHAT_MIN = 168;
const MAP_MIN = 320;
const PLAN_MIN = 320;
const CHAT_DEFAULT = 200;
const MAP_DEFAULT = 420;
const FRAME_PAD = 32;

type Panels = { chat: boolean; map: boolean };
type Widths = { chat: number; map: number };
type Sheet = "chat" | "map" | "sketch" | "videos" | "trending" | "budget" | "exchange" | "profile" | null;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function readWidths(): Widths {
  try {
    const raw = localStorage.getItem(WIDTHS_KEY);
    if (!raw) return { chat: CHAT_DEFAULT, map: MAP_DEFAULT };
    const parsed = JSON.parse(raw) as Partial<Widths>;
    return {
      chat: clamp(Number(parsed.chat) || CHAT_DEFAULT, CHAT_MIN, 280),
      map: clamp(Number(parsed.map) || MAP_DEFAULT, MAP_MIN, 640),
    };
  } catch {
    return { chat: CHAT_DEFAULT, map: MAP_DEFAULT };
  }
}

/** Three columns stay up while the plan can keep 320px. Below that, one sheet. */
function canDock(viewport: number) {
  return viewport >= FRAME_PAD + CHAT_MIN + MAP_MIN + 24 + PLAN_MIN;
}

function fitWidths(viewport: number, panels: Panels, widths: Widths): Widths {
  const gaps = 12 * ((panels.chat ? 1 : 0) + (panels.map ? 1 : 0));
  const room = Math.max(0, viewport - FRAME_PAD - gaps - PLAN_MIN);
  const chat = panels.chat ? widths.chat : 0;
  const map = panels.map ? widths.map : 0;
  const used = chat + map;
  if (used === 0 || used <= room) return widths;
  const scale = room / used;
  return {
    chat: panels.chat ? Math.max(CHAT_MIN, Math.floor(widths.chat * scale)) : widths.chat,
    map: panels.map ? Math.max(MAP_MIN, Math.floor(widths.map * scale)) : widths.map,
  };
}

function readPanels(): Panels {
  try {
    const raw = localStorage.getItem(PANELS_KEY);
    if (!raw) return { chat: true, map: true };
    const parsed = JSON.parse(raw) as Partial<Panels>;
    return { chat: parsed.chat !== false, map: parsed.map !== false };
  } catch {
    return { chat: true, map: true };
  }
}

function ColumnGrip({
  label,
  side,
  onDrag,
  onReset,
}: {
  label: string;
  side: "chat" | "map";
  onDrag: (dx: number) => void;
  onReset: () => void;
}) {
  const last = useRef(0);
  return (
    <button
      type="button"
      className={`pf-col-grip pf-col-grip--${side}`}
      aria-label={label}
      onDoubleClick={onReset}
      onPointerDown={(event) => {
        last.current = event.clientX;
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
        const dx = event.clientX - last.current;
        last.current = event.clientX;
        if (dx !== 0) onDrag(dx);
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 48 : 24;
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          onDrag(-step);
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          onDrag(step);
        } else if (event.key === "Home") {
          event.preventDefault();
          onReset();
        }
      }}
    />
  );
}

type WidgetId = "profile" | "budget" | "exchange" | "sketch" | "videos" | "trending";

/** Share of the screen. The window is never a fixed pixel box. */
const WIDGET_SHARE: Record<WidgetId, { w: number; h: number }> = {
  profile: { w: 0.4, h: 0.78 },
  budget: { w: 0.38, h: 0.64 },
  exchange: { w: 0.36, h: 0.56 },
  sketch: { w: 0.56, h: 0.72 },
  videos: { w: 0.42, h: 0.7 },
  trending: { w: 0.36, h: 0.74 },
};

function fitShare(share: { w: number; h: number }) {
  const w = Math.round(Math.min(window.innerWidth - 32, Math.max(320, window.innerWidth * share.w)));
  const h = Math.round(Math.min(window.innerHeight - 80, Math.max(240, window.innerHeight * share.h)));
  return {
    left: Math.max(16, Math.round((window.innerWidth - w) / 2)),
    top: Math.max(56, Math.round((window.innerHeight - h) / 2)),
    w,
    h,
  };
}

function isWidget(sheet: Sheet): sheet is WidgetId {
  return sheet === "profile" || sheet === "budget" || sheet === "exchange" || sheet === "sketch" || sheet === "videos" || sheet === "trending";
}

function WidgetPop({
  label,
  onHide,
  children,
  share,
}: {
  label: string;
  onHide: () => void;
  children: ReactNode;
  share: { w: number; h: number };
}) {
  const { t } = useI18n();
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ mode: "move" | "size"; x: number; y: number; left: number; top: number; w: number; h: number } | null>(null);
  const shaped = useRef(false);
  const [box, setBox] = useState(() => fitShare(share));
  useEffect(() => {
    const onResize = () => {
      if (!shaped.current) setBox(fitShare(share));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [share]);
  const [min, setMin] = useState(false);
  const [large, setLarge] = useState(false);
  const remembered = useRef(box);

  const place = (next: typeof box) => {
    const w = Math.min(Math.max(next.w, 280), window.innerWidth - 24);
    const h = Math.min(Math.max(next.h, 160), window.innerHeight - 24);
    setBox({
      w,
      h,
      left: Math.min(Math.max(8, next.left), Math.max(8, window.innerWidth - w - 8)),
      top: Math.min(Math.max(8, next.top), Math.max(8, window.innerHeight - 44)),
    });
  };

  const begin = (mode: "move" | "size") => (event: ReactPointerEvent) => {
    if (event.button !== 0) return;
    if (mode === "move" && (event.target as HTMLElement).closest("button")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const current = large ? { left: 24, top: 72, w: window.innerWidth - 48, h: window.innerHeight - 96 } : box;
    drag.current = { mode, x: event.clientX, y: event.clientY, ...current };
    shaped.current = true;
    if (large) setLarge(false);
    if (min && mode === "size") setMin(false);
  };
  const follow = (event: ReactPointerEvent) => {
    const current = drag.current;
    if (!current) return;
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    if (current.mode === "move") place({ ...box, left: current.left + dx, top: current.top + dy });
    else place({ ...box, w: current.w + dx, h: current.h + dy });
  };
  const finish = () => {
    drag.current = null;
  };

  const shown = large
    ? { left: 24, top: 72, w: Math.max(280, window.innerWidth - 48), h: Math.max(180, window.innerHeight - 96) }
    : box;

  return (
    <div
      ref={frame}
      className={`pf-pop${min ? " is-min" : ""}`}
      role="dialog"
      aria-label={label}
      style={{ left: shown.left, top: shown.top, width: shown.w, height: min ? undefined : shown.h }}
    >
      <div className="pf-pop__bar" onPointerDown={begin("move")} onPointerMove={follow} onPointerUp={finish} onPointerCancel={finish}>
        <span className="pf-dock__label">{label}</span>
        <span className="pf-pop__actions">
          <button type="button" className="pf-dock__close" aria-label={min ? t("panels.restore") : t("panels.minimize")} onClick={() => setMin((value) => !value)}>
            <Icon name={min ? "plus" : "minus"} size={14} />
          </button>
          <button
            type="button"
            className="pf-dock__close"
            aria-label={large ? t("panels.restore") : t("panels.enlarge")}
            onClick={() => {
              setMin(false);
              setLarge((value) => {
                if (!value) remembered.current = box;
                else place(remembered.current);
                return !value;
              });
            }}
          >
            <Icon name={large ? "layers" : "external"} size={14} />
          </button>
          <button type="button" className="pf-dock__close" aria-label={t("panels.close")} onClick={onHide}>
            <Icon name="close" size={16} />
          </button>
        </span>
      </div>
      <div className="pf-pop__body pf-dock__body">{children}</div>
      {min ? null : (
        <span className="pf-pop__size" aria-hidden="true" onPointerDown={begin("size")} onPointerMove={follow} onPointerUp={finish} onPointerCancel={finish} />
      )}
    </div>
  );
}

function DockBar({
  label,
  hideLabel,
  onHide,
  buttonRef,
}: {
  label: string;
  hideLabel: string;
  onHide: () => void;
  buttonRef?: Ref<HTMLButtonElement>;
}) {
  return (
    <div className="pf-dock__bar">
      <span className="pf-dock__label">{label}</span>
      <button ref={buttonRef} type="button" className="pf-dock__close" aria-label={hideLabel} onClick={onHide}>
        <Icon name="close" size={16} />
      </button>
    </div>
  );
}

export function PlannerTemplate({
  header,
  left,
  centre,
  right,
  sketch,
  videos,
  trending,
  budget,
  exchange,
  profile,
}: {
  header: ReactNode;
  left: ReactNode;
  centre: ReactNode;
  right: ReactNode;
  sketch?: ReactNode;
  videos?: ReactNode;
  trending?: ReactNode;
  budget?: ReactNode;
  exchange?: ReactNode;
  profile?: ReactNode;
}) {
  const { t } = useI18n();
  const [panels, setPanels] = useState<Panels>(readPanels);
  const [widths, setWidths] = useState<Widths>(readWidths);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [width, setWidth] = useState(() => (typeof window === "undefined" ? 1280 : window.innerWidth));
  const chatCloseRef = useRef<HTMLButtonElement>(null);
  const mapCloseRef = useRef<HTMLButtonElement>(null);
  const plannerRef = useRef<HTMLDivElement>(null);
  const dock = canDock(width);
  const shown = fitWidths(width, { chat: true, map: true }, widths);

  useEffect(() => {
    let frame = 0;
    const onResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setWidth(window.innerWidth));
    };
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(WIDTHS_KEY, JSON.stringify(widths));
    } catch {
      /* the widths still apply for this visit */
    }
  }, [widths]);

  useEffect(() => {
    if (!sheet) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSheet(null);
    };
    window.addEventListener("keydown", onKey);
    (sheet === "chat" ? chatCloseRef : mapCloseRef).current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [dock, sheet]);

  const writePanels = (next: Panels) => {
    setPanels(next);
    try {
      localStorage.setItem(PANELS_KEY, JSON.stringify(next));
    } catch {
      /* a private window can refuse storage; the panels still move */
    }
  };

  const toggleChat = () => {
    if (!dock) setSheet((current) => (current === "chat" ? null : "chat"));
    else writePanels({ ...panels, chat: !panels.chat });
  };

  const toggleMap = () => {
    if (!dock) setSheet((current) => (current === "map" ? null : "map"));
    else writePanels({ ...panels, map: !panels.map });
  };

  const chatPressed = dock ? panels.chat : sheet === "chat";
  const mapPressed = dock ? panels.map : sheet === "map";

  const nudge = (side: "chat" | "map", dx: number) => {
    setWidths((current) => {
      const space = plannerRef.current?.clientWidth ?? width;
      const other = side === "chat" ? (panels.map ? current.map : 0) : (panels.chat ? current.chat : 0);
      const gaps = 12 * ((panels.chat ? 1 : 0) + (panels.map ? 1 : 0));
      const min = side === "chat" ? CHAT_MIN : MAP_MIN;
      const max = Math.max(min, space - FRAME_PAD - gaps - other - PLAN_MIN);
      const signed = side === "chat" ? dx : -dx;
      const next = clamp((side === "chat" ? current.chat : current.map) + signed, min, max);
      return side === "chat" ? { ...current, chat: next } : { ...current, map: next };
    });
  };

  return (
    <div className="pf-app">
      {header}
      <div className="pf-shell" data-dock={dock ? "true" : "false"}>
        <div className="pf-shell__bar" role="toolbar" aria-label={t("panels.surfaces")}>
          {dock ? null : (
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={chatPressed}
            aria-controls="pf-panel-chat"
            onClick={toggleChat}
          >
            <Icon name="calendar" size={15} />
            <span>{t("day.strip")}</span>
          </button>
          )}
          {dock ? null : (
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={mapPressed}
            aria-controls="pf-panel-map"
            onClick={toggleMap}
          >
            <Icon name="sparkles" size={15} />
            <span>{t("chat.label")}</span>
          </button>
          )}
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={sheet === "profile"}
            onClick={() => setSheet((current) => (current === "profile" ? null : "profile"))}
          >
            <span>{t("settings.group.profile")}</span>
          </button>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={sheet === "budget"}
            onClick={() => setSheet((current) => (current === "budget" ? null : "budget"))}
          >
            <span>{t("budget.tool")}</span>
          </button>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={sheet === "exchange"}
            onClick={() => setSheet((current) => (current === "exchange" ? null : "exchange"))}
          >
            <span>{t("budget.exchange")}</span>
          </button>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={sheet === "sketch"}
            onClick={() => setSheet((current) => (current === "sketch" ? null : "sketch"))}
          >
            <span>{t("panels.sketch")}</span>
          </button>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={sheet === "videos"}
            onClick={() => setSheet((current) => (current === "videos" ? null : "videos"))}
          >
            <span>{t("videos.title")}</span>
          </button>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={sheet === "trending"}
            onClick={() => setSheet((current) => (current === "trending" ? null : "trending"))}
          >
            <span>{t("trending.eyebrow")}</span>
          </button>
        </div>

        <div
          ref={plannerRef}
          className="pf-planner"
          data-dock={dock ? "true" : "false"}
          data-chat={dock ? "open" : "closed"}
          data-map={dock ? "open" : "closed"}
          style={{ ["--pf-w-chat" as string]: `${shown.chat}px`, ["--pf-w-map" as string]: `${shown.map}px` }}
        >
          {dock ? (
            <div className="pf-planner__left" id="pf-panel-days">
              <div className="pf-dock__body">{left}</div>
            </div>
          ) : null}
          <div className="pf-planner__centre">{centre}</div>
          {dock ? (
            <div className="pf-planner__right" id="pf-panel-chat">
              <div className="pf-dock__body">{right}</div>
            </div>
          ) : null}
        </div>

        {sheet && !isWidget(sheet) ? (
          <button type="button" className="pf-surface-scrim" aria-label={t("panels.close")} onClick={() => setSheet(null)} />
        ) : null}

        {!dock ? (
          <div
            className="pf-surface-sheet"
            id="pf-panel-chat"
            role="dialog"
            aria-modal={sheet === "chat"}
            aria-label={t("day.strip")}
            hidden={sheet !== "chat"}
          >
            <DockBar
              label={t("day.strip")}
              hideLabel={t("panels.close")}
              onHide={() => setSheet(null)}
              buttonRef={chatCloseRef}
            />
            <div className="pf-dock__body">{left}</div>
          </div>
        ) : null}

        {!dock ? (
          <div
            className="pf-surface-sheet"
            id="pf-panel-map"
            role="dialog"
            aria-modal={sheet === "map"}
            aria-label={t("chat.label")}
            hidden={sheet !== "map"}
          >
            <DockBar
              label={t("chat.label")}
              hideLabel={t("panels.close")}
              onHide={() => setSheet(null)}
              buttonRef={mapCloseRef}
            />
            <div className="pf-dock__body">{right}</div>
          </div>
        ) : null}

        {isWidget(sheet) ? (
          <WidgetPop
            key={sheet}
            label={
              sheet === "profile" ? t("settings.profile")
              : sheet === "budget" ? t("budget.tool")
              : sheet === "exchange" ? t("budget.exchange")
              : sheet === "sketch" ? t("panels.sketch")
              : sheet === "videos" ? t("videos.title")
              : t("trending.eyebrow")
            }
            share={WIDGET_SHARE[sheet]}
            onHide={() => setSheet(null)}
          >
            {sheet === "profile" ? profile
              : sheet === "budget" ? budget
              : sheet === "exchange" ? exchange
              : sheet === "sketch" ? sketch
              : sheet === "videos" ? videos
              : trending}
          </WidgetPop>
        ) : null}
      </div>
    </div>
  );
}
