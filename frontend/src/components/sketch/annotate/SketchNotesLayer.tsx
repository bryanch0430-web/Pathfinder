/*
 * SketchNotesLayer: the traveler's saved drawing, over the live sketch page. It is its own SVG
 * with the page's viewBox, stacked on the page, so the guide's draw animation and the PDF (which
 * re-render the page) never see it. Excalidraw loads only when a page actually has a drawing.
 */
import { useEffect, useRef } from 'react';
import type { SketchPage } from '../types';
import { type SketchScene, useSketchNotes } from './notes';
import '../../../styles/sketch-notes.css';

interface Props { tripId: string; page: SketchPage }

/** Two invisible 1 px marks at the page corners, so the exported drawing's bounds start at the page origin. */
function cornerMarks(page: SketchPage) {
  const mark = (id: string, x: number, y: number) => ({ type: 'rectangle' as const, id, x, y, width: 1, height: 1, opacity: 0 });
  return [mark('tv-page-origin', 0, 0), mark('tv-page-corner', page.w - 1, page.h - 1)];
}

export function SketchNotesLayer({ tripId, page }: Props) {
  const scene: SketchScene | undefined = useSketchNotes(tripId)[page.id];
  const layer = useRef<SVGSVGElement>(null);
  const landscape = page.w > page.h; // notes are drawn on the landscape sheet; phones' upright pages skip them

  useEffect(() => {
    const host = layer.current;
    if (!host || !scene || !landscape) return;
    let live = true;
    void (async () => {
      const { exportToSvg, convertToExcalidrawElements } = await import('@excalidraw/excalidraw');
      const dark = !!host.closest('.is-dark');
      const svg = await exportToSvg({
        elements: [...convertToExcalidrawElements(cornerMarks(page)), ...scene.elements] as never,
        appState: { exportBackground: false, exportWithDarkMode: dark } as never,
        files: scene.files as never,
        exportPadding: 0,
      });
      if (!live) return;
      // ponytail: assumes the drawing stays on the page (the corner marks then fix its origin at 0,0);
      // strokes dragged past the edge shift the export, so they'd need Excalidraw's own bounds to align.
      svg.setAttribute('x', '0');
      svg.setAttribute('y', '0');
      host.replaceChildren(svg);
    })().catch(() => undefined); // a drawing that can't render never blocks the page
    return () => { live = false; host.replaceChildren(); };
  }, [scene, page, landscape]);

  if (!scene || !landscape) return null;
  return <svg ref={layer} className="tv-sketch-notes" viewBox={`0 0 ${page.w} ${page.h}`} preserveAspectRatio="xMidYMid meet"
    aria-hidden="true" xmlns="http://www.w3.org/2000/svg" />;
}
