import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../../lib/i18n";
import type { TripPlan } from "../types";
import { planToDocument } from "./fromPlan";
import { layoutSketch, type Orientation } from "./layout";
import { SketchPageSvg } from "./render";
import "./sketch.css";

const UPRIGHT = "(max-width: 700px) and (orientation: portrait)";

function useOrientation(): Orientation {
  const [upright, setUpright] = useState(() => window.matchMedia(UPRIGHT).matches);
  useEffect(() => {
    const list = window.matchMedia(UPRIGHT);
    const update = () => setUpright(list.matches);
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, []);
  return upright ? "portrait" : "landscape";
}

/** The trip as hand-drawn pages: an overview, then one page for each day. */
export function Sketchbook({ plan }: { plan: TripPlan | null }) {
  const { t } = useI18n();
  const orientation = useOrientation();
  const pages = useMemo(
    () => (plan ? layoutSketch(planToDocument(plan), orientation) : []),
    [plan, orientation],
  );
  const [index, setIndex] = useState(0);
  const safe = pages.length ? Math.min(index, pages.length - 1) : 0;
  const page = pages[safe];

  useEffect(() => {
    setIndex(0);
  }, [plan?.trip_id]);

  if (!plan || !page) {
    return (
      <div className="pf-sketch-empty">
        <p>{t("sketch.empty")}</p>
      </div>
    );
  }

  const turn = (next: number) => setIndex(Math.min(pages.length - 1, Math.max(0, next)));

  return (
    <div
      className="tv-sketch"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight") turn(safe + 1);
        if (event.key === "ArrowLeft") turn(safe - 1);
      }}
    >
      <div className="tv-sketch__stage">
        <SketchPageSvg page={page} className="tv-sketch__page" />
      </div>
      <div className="tv-sketch__bar">
        <span />
        <div className="tv-sketch__nav">
          <button type="button" className="pf-sketch-nav" onClick={() => turn(safe - 1)} disabled={safe === 0} aria-label={t("sketch.previous")}>
            ←
          </button>
          <span className="tv-sketch__count">{page.title}</span>
          <button type="button" className="pf-sketch-nav" onClick={() => turn(safe + 1)} disabled={safe === pages.length - 1} aria-label={t("sketch.next")}>
            →
          </button>
        </div>
        <span className="pf-sketch-pages">{safe + 1} / {pages.length}</span>
      </div>
    </div>
  );
}
