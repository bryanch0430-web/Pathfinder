/*
 * ExportMenu: take the trip anywhere. Every file is built in the browser from the trip document
 * (lib/exporters). GPX and KML first locate the places on the server (OpenStreetMap), which is slow
 * only the first time. `pdf` is filled in by the PDF export.
 *
 * A finished file saves straight away while the click that asked for it is still fresh. When the
 * work took longer than that (see download()), the menu shows a "Save …" link instead of claiming
 * a download the browser may have blocked. The link stays either way, to save the file again.
 */
import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../services/apiClient';
import { getTripBudget, type TripDocument } from '../../services/tripService';
import { budgetCsv } from '../../lib/exporters/csv';
import { download, fileBase, fileUrl } from '../../lib/exporters/download';
import { toGpx } from '../../lib/exporters/gpx';
import { datedFrom, toIcs } from '../../lib/exporters/ics';
import { toKml } from '../../lib/exporters/kml';
import { dayDirectionsUrl } from '../../lib/exporters/maps';
import { type LatLon, type PlacePoints, pointsRequest } from '../../lib/exporters/points';
import '../../styles/export-menu.css';

interface Props {
  document: TripDocument | null;
  /** The PDF file, built by the PDF export; the entry only shows when provided. The menu shows
   *  "Preparing PDF…" until it settles, and a rejection's message as the status. */
  pdf?: () => Promise<Blob>;
}

export function ExportMenu({ document: doc, pdf }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  // The last finished export. `saved` is false when the browser wouldn't take it as the user's download.
  const [file, setFile] = useState<{ name: string; url: string; saved: boolean } | null>(null);
  const fileLink = useRef<HTMLAnchorElement>(null);
  // For a trip planned without dates: the day it starts, picked here so the calendar has something to hang on.
  const [startDate, setStartDate] = useState('');
  const points = useRef<{ key: string; value: PlacePoints } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const entries = () => [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]') ?? [])];

  useEffect(() => {
    if (!open) return;
    entries()[0]?.focus(); // keyboard users land in the menu, not back on the page
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent) {
        if (event.key !== 'Escape') return;
        setOpen(false);
        trigger.current?.focus();
      } else if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    // Close when Tab moves focus elsewhere. A null target is focus dropping off an entry that just got
    // disabled while an export runs; keep the menu open so its status stays visible.
    const leave = (event: FocusEvent) => {
      if (event.relatedTarget && !root.current?.contains(event.relatedTarget as Node)) setOpen(false);
    };
    const container = root.current;
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', close);
    container?.addEventListener('focusout', leave);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', close);
      container?.removeEventListener('focusout', leave);
    };
  }, [open]);

  useEffect(() => () => { if (file) URL.revokeObjectURL(file.url); }, [file]);
  // A file waiting for its click: put the keyboard on the link.
  useEffect(() => { if (file && !file.saved) fileLink.current?.focus(); }, [file]);

  // Arrow keys, Home and End move between the entries; Tab still works as usual.
  const onPanelKey = (event: React.KeyboardEvent) => {
    const list = entries();
    const index = list.indexOf(window.document.activeElement as HTMLElement);
    const next = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: list.length - 1 }[event.key];
    if (next === undefined || !list.length) return;
    event.preventDefault();
    list[(next + list.length) % list.length].focus();
  };

  if (!doc) return null;
  const base = fileBase(doc);
  const placeCount = doc.days.reduce((sum, day) => sum + day.items.length, 0);
  const dayLinks = doc.days.flatMap((day) => {
    const url = dayDirectionsUrl(day, doc.travel_mode);
    return url ? [{ day, url }] : [];
  });

  /** `task` builds the file: its name, its content, and optionally a note for the status line. */
  const run = async (label: string, task: () => Promise<{ name: string; content: string | Blob; type: string; note?: string }>) => {
    setBusy(label);
    setStatus('');
    setFile(null);
    try {
      const { name, content, type, note } = await task();
      const url = fileUrl(content, type);
      const saved = download(name, url);
      setFile({ name, url, saved });
      setStatus(`${label} ${saved ? 'downloaded' : 'is ready'}.${note ? ` ${note}` : ''}`);
      if (!saved) setOpen(true); // the file needs its click, so the link must be on screen
    } catch (error) {
      setStatus(error instanceof Error ? error.message : `${label} could not be created.`);
    } finally {
      setBusy(null);
    }
  };

  const locate = async () => {
    const request = pointsRequest(doc);
    const key = JSON.stringify(request);
    if (points.current?.key !== key) {
      setStatus('Finding your places on the map. The first time can take up to a minute.');
      const response = await apiFetch<{ points: ({ id: string } & LatLon)[] }>('/api/exports/points', { method: 'POST', body: key });
      if (!response.ok || !response.data) throw new Error(response.error || 'Could not find the places on the map.');
      points.current = { key, value: Object.fromEntries(response.data.points.map(({ id, lat, lon }) => [id, { lat, lon }])) };
    }
    const found = Object.keys(points.current.value).length;
    if (!found) throw new Error('None of the places could be found on the map yet.');
    return { value: points.current.value, note: `${found} of ${placeCount} places located (© OpenStreetMap contributors).` };
  };

  const mapFile = (label: string, extension: string, type: string, build: (p: PlacePoints) => string) => () =>
    run(label, async () => {
      const { value, note } = await locate();
      return { name: `${base}.${extension}`, content: build(value), type, note };
    });

  const ics = doc.start_date ? toIcs(doc) : startDate ? toIcs(datedFrom(doc, startDate)) : null;
  const disabled = busy !== null;

  return (
    <div className="tv-export" ref={root}>
      <button type="button" ref={trigger} className="tv-export__trigger" aria-expanded={open} aria-controls="tv-export-panel" onClick={() => { setOpen((value) => !value); if (!busy) setStatus(''); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
        </svg>
        Export
      </button>
      {open && (
        <div className="tv-export__panel" id="tv-export-panel" ref={panel} role="group" aria-label="Export this trip" onKeyDown={onPanelKey}>
          {pdf && <button type="button" disabled={disabled} onClick={() => run('PDF', async () => ({ name: `${base}.pdf`, content: await pdf(), type: 'application/pdf' }))}>
            <strong>PDF</strong><small>Every page, ready to print or share</small>
          </button>}
          <button type="button" disabled={disabled || !ics} onClick={() => run('Calendar', async () => ({ name: `${base}.ics`, content: ics!, type: 'text/calendar' }))}>
            <strong>Calendar (.ics)</strong>
            <small>{ics ? 'Google, Apple or Outlook Calendar' : 'Pick the day the trip starts to get a calendar'}</small>
          </button>
          {!doc.start_date && (
            <label className="tv-export__date">
              Trip starts on
              <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </label>
          )}
          <button type="button" disabled={disabled || !placeCount} onClick={mapFile('GPX', 'gpx', 'application/gpx+xml', (p) => toGpx(doc, p))}>
            <strong>GPX</strong><small>Organic Maps, Maps.me and GPS apps</small>
          </button>
          <button type="button" disabled={disabled || !placeCount} onClick={mapFile('KML', 'kml', 'application/vnd.google-earth.kml+xml', (p) => toKml(doc, p))}>
            <strong>KML</strong><small>Google My Maps and Google Earth</small>
          </button>
          <button type="button" disabled={disabled} onClick={() => run('Budget', async () => ({
            name: `${base}-budget.csv`, content: budgetCsv(await getTripBudget(doc.trip_id)), type: 'text/csv;charset=utf-8',
          }))}>
            <strong>Budget (.csv)</strong><small>Excel, Numbers or Google Sheets</small>
          </button>
          <button type="button" disabled={disabled} onClick={() => run('Trip data', async () => ({
            name: `${base}.json`, content: JSON.stringify(doc, null, 2), type: 'application/json',
          }))}>
            <strong>Everything (.json)</strong><small>The whole trip, for backups and other tools</small>
          </button>
          {dayLinks.length > 0 && (
            <div className="tv-export__days">
              <span>Directions in Google Maps</span>
              <ul>
                {dayLinks.map(({ day, url }) => (
                  <li key={day.day}><a href={url} target="_blank" rel="noopener noreferrer">Day {day.day} · {day.base}</a></li>
                ))}
              </ul>
            </div>
          )}
          <p className="tv-export__status" role="status" aria-live="polite">
            {busy ? `Preparing ${busy}…` : status}
            {!busy && file && (
              <a ref={fileLink} href={file.url} download={file.name} className={file.saved ? '' : 'is-waiting'}>
                {file.saved ? 'Save it again' : `Save ${file.name}`}
              </a>
            )}
          </p>
        </div>
      )}
    </div>
  );
}
