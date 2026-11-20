/*
 * SketchEditor: draw on a sketch page with Excalidraw (MIT). The real page (SketchPageSvg, with
 * the app's fonts and colours) sits under a transparent Excalidraw canvas and follows its pan
 * and zoom, so scene coordinates are page coordinates and a saved drawing lines up exactly.
 * Only the traveler's own elements are saved. Lazy: this module is the only Excalidraw entry.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { Excalidraw } from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';
import { SketchPageSvg } from '../render';
import type { SketchPage } from '../types';
import { type SketchScene, saveSketchNote } from './notes';
import '../../../styles/sketch-notes.css';

interface Props {
  tripId: string;
  page: SketchPage;
  scene: SketchScene | undefined;
  onClose: () => void;
}

type Api = { getSceneElements: () => readonly Record<string, unknown>[]; getFiles: () => Record<string, Record<string, unknown>> };

export default function SketchEditor({ tripId, page, scene, onClose }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const underlay = useRef<HTMLDivElement>(null);
  const api = useRef<Api | null>(null);
  const [view, setView] = useState<{ scrollX: number; scrollY: number; zoom: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dark = !!document.querySelector('.tv2-app.is-dark');

  // Fit the page in the canvas once, before Excalidraw mounts with that view.
  useLayoutEffect(() => {
    const rect = box.current!.getBoundingClientRect();
    const zoom = Math.min(rect.width / page.w, rect.height / page.h) * 0.94;
    setView({ zoom, scrollX: (rect.width / zoom - page.w) / 2, scrollY: (rect.height / zoom - page.h) / 2 });
  }, [page]);

  // Excalidraw draws scene point (x, y) at ((x + scrollX) · zoom, (y + scrollY) · zoom): move the page the same way.
  const follow = (scrollX: number, scrollY: number, zoom: number) => {
    if (underlay.current) underlay.current.style.transform = `scale(${zoom}) translate(${scrollX}px, ${scrollY}px)`;
  };

  const save = async (clear = false) => {
    if (!api.current || saving) return;
    setSaving(true);
    setError('');
    try {
      await saveSketchNote(tripId, page.id, clear ? { elements: [], files: {} }
        : { elements: [...api.current.getSceneElements()], files: api.current.getFiles() });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Your drawing could not be saved.');
      setSaving(false);
    }
  };

  return (
    <div className="tv-sketch-editor" role="dialog" aria-modal="true" aria-label={`Draw on ${page.title}`}>
      <header className="tv-sketch-editor__bar">
        <div><span className="tv-label">DRAW ON THE SKETCH</span><strong>{page.title}</strong></div>
        {error && <p className="tv-sketch-editor__error" role="alert">{error}</p>}
        <span className="tv-app__bar-spacer" />
        {scene && <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" disabled={saving} onClick={() => void save(true)}>Clear drawing</button>}
        <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" disabled={saving} onClick={onClose}>Cancel</button>
        <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" disabled={saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save drawing'}</button>
      </header>
      <div className="tv-sketch-editor__canvas" ref={box}>
        {view && <>
          <div className="tv-sketch-editor__page" ref={underlay} style={{ width: page.w, height: page.h,
            transform: `scale(${view.zoom}) translate(${view.scrollX}px, ${view.scrollY}px)` }}>
            <SketchPageSvg page={page} className="tv-sketch__page" />
          </div>
          <div className="tv-sketch-editor__excalidraw">
            <Excalidraw
              excalidrawAPI={(instance) => { api.current = instance as unknown as Api; }}
              theme={dark ? 'dark' : 'light'}
              initialData={{
                elements: (scene?.elements ?? []) as never,
                files: (scene?.files ?? {}) as never,
                appState: { viewBackgroundColor: 'transparent', scrollX: view.scrollX, scrollY: view.scrollY,
                  zoom: { value: view.zoom }, currentItemStrokeColor: '#e03131', currentItemRoughness: 1 } as never,
              }}
              onScrollChange={(scrollX, scrollY, zoom) => follow(scrollX, scrollY, zoom.value)}
              UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false,
                changeViewBackgroundColor: false, toggleTheme: false } }}
            />
          </div>
        </>}
      </div>
    </div>
  );
}
