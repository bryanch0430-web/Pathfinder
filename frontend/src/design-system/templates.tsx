import { useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { useI18n } from "../lib/i18n";
import { CanvasBoard } from "./CanvasBoard";
import { Icon } from "./icons";

/** Saved dock state. A narrow viewport uses a sheet and does not read this. */
const PANELS_KEY = "pathfinder-planner-panels";
const WIDTHS_KEY = "pathfinder-planner-widths";
const CHAT_MIN = 240;
const MAP_MIN = 280;
const PLAN_MIN = 320;
const CHAT_DEFAULT = 340;
const MAP_DEFAULT = 400;
const FRAME_PAD = 32;

type Panels = { chat: boolean; map: boolean };
type Widths = { chat: number; map: number };
type Sheet = "chat" | "map" | "sketch" | "videos" | "trending" | null;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function readWidths(): Widths {
  try {
    const raw = localStorage.getItem(WIDTHS_KEY);
    if (!raw) return { chat: CHAT_DEFAULT, map: MAP_DEFAULT };
    const parsed = JSON.parse(raw) as Partial<Widths>;
    return {
      chat: clamp(Number(parsed.chat) || CHAT_DEFAULT, CHAT_MIN, 760),
      map: clamp(Number(parsed.map) || MAP_DEFAULT, MAP_MIN, 880),
    };
  } catch {
    return { chat: CHAT_DEFAULT, map: MAP_DEFAULT };
  }
}

/** Three columns stay up while the plan can keep 320px. Below that, one sheet. */
function canDock(viewport: number, panels: Panels) {
  const chat = panels.chat ? CHAT_MIN : 0;
  const map = panels.map ? MAP_MIN : 0;
  const gaps = 12 * ((panels.chat ? 1 : 0) + (panels.map ? 1 : 0));
  return viewport >= FRAME_PAD + chat + map + gaps + PLAN_MIN;
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
}: {
  header: ReactNode;
  left: ReactNode;
  centre: ReactNode;
  right: ReactNode;
  sketch?: ReactNode;
  videos?: ReactNode;
  trending?: ReactNode;
}) {
  const { t } = useI18n();
  const [panels, setPanels] = useState<Panels>(readPanels);
  const [widths, setWidths] = useState<Widths>(readWidths);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [width, setWidth] = useState(() => (typeof window === "undefined" ? 1280 : window.innerWidth));
  const chatCloseRef = useRef<HTMLButtonElement>(null);
  const mapCloseRef = useRef<HTMLButtonElement>(null);
  const plannerRef = useRef<HTMLDivElement>(null);
  const dock = canDock(width, panels);
  const shown = fitWidths(width, panels, widths);

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
    if (dock || !sheet) return;
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
        {dock ? (
          <CanvasBoard
            chat={left}
            plan={centre}
            map={right}
            sketch={sketch ?? null}
            videos={videos ?? null}
            trending={trending ?? null}
          />
        ) : null}
        {dock ? null : <div className="pf-shell__bar" role="toolbar" aria-label={t("panels.surfaces")}>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={chatPressed}
            aria-controls="pf-panel-chat"
            onClick={toggleChat}
          >
            <Icon name="sparkles" size={15} />
            <span>{t("chat.label")}</span>
          </button>
          <button
            type="button"
            className="pf-shell__toggle"
            aria-pressed={mapPressed}
            aria-controls="pf-panel-map"
            onClick={toggleMap}
          >
            <Icon name="map" size={15} />
            <span>{t("map.label")}</span>
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
        </div>}

        {dock ? null : <div
          ref={plannerRef}
          className="pf-planner"
          data-dock={dock ? "true" : "false"}
          data-chat={dock && panels.chat ? "open" : "closed"}
          data-map={dock && panels.map ? "open" : "closed"}
          style={{ ["--pf-w-chat" as string]: `${shown.chat}px`, ["--pf-w-map" as string]: `${shown.map}px` }}
        >
          {dock ? (
            <div className="pf-planner__left" id="pf-panel-chat" hidden={!panels.chat}>
              <DockBar label={t("chat.label")} hideLabel={t("panels.hideChat")} onHide={toggleChat} />
              <div className="pf-dock__body">{left}</div>
            </div>
          ) : null}
          <div className="pf-planner__centre">{centre}</div>
          {dock ? (
            <div className="pf-planner__right" id="pf-panel-map" hidden={!panels.map}>
              <DockBar label={t("map.label")} hideLabel={t("panels.hideMap")} onHide={toggleMap} />
              <div className="pf-dock__body">{right}</div>
            </div>
          ) : null}
        </div>}

        {!dock && sheet ? (
          <button type="button" className="pf-surface-scrim" aria-label={t("panels.close")} onClick={() => setSheet(null)} />
        ) : null}

        {!dock ? (
          <div
            className="pf-surface-sheet"
            id="pf-panel-chat"
            role="dialog"
            aria-modal={sheet === "chat"}
            aria-label={t("chat.label")}
            hidden={sheet !== "chat"}
          >
            <DockBar
              label={t("chat.label")}
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
            aria-label={t("map.label")}
            hidden={sheet !== "map"}
          >
            <DockBar
              label={t("map.label")}
              hideLabel={t("panels.close")}
              onHide={() => setSheet(null)}
              buttonRef={mapCloseRef}
            />
            <div className="pf-dock__body">{right}</div>
          </div>
        ) : null}

        {!dock ? (
          <div
            className="pf-surface-sheet"
            id="pf-panel-sketch"
            role="dialog"
            aria-modal={sheet === "sketch"}
            aria-label={t("panels.sketch")}
            hidden={sheet !== "sketch"}
          >
            <DockBar label={t("panels.sketch")} hideLabel={t("panels.close")} onHide={() => setSheet(null)} />
            <div className="pf-dock__body">{sketch}</div>
          </div>
        ) : null}

        {!dock ? (
          <div className="pf-surface-sheet" role="dialog" aria-modal={sheet === "videos"} aria-label={t("videos.title")} hidden={sheet !== "videos"}>
            <DockBar label={t("videos.title")} hideLabel={t("panels.close")} onHide={() => setSheet(null)} />
            <div className="pf-dock__body">{videos}</div>
          </div>
        ) : null}

        {!dock ? (
          <div className="pf-surface-sheet" role="dialog" aria-modal={sheet === "trending"} aria-label={t("trending.eyebrow")} hidden={sheet !== "trending"}>
            <DockBar label={t("trending.eyebrow")} hideLabel={t("panels.close")} onHide={() => setSheet(null)} />
            <div className="pf-dock__body">{trending}</div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
