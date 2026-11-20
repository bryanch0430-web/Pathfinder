/*
 * Calendar download from the studio bar: stops, stays and the route's legs.
 * A trip with no start date asks for one, then builds the file from that day.
 */
import { useState } from 'react';
import type { TripDocument } from '../../services/tripService';
import { download, fileBase, fileUrl } from '../../lib/exporters/download';
import { datedFrom, toIcs } from '../../lib/exporters/ics';

export function CalendarButton({ document: doc }: { document: TripDocument | null }) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState('');
  if (!doc) return null;

  const save = (file: string) => {
    const url = fileUrl(file, 'text/calendar');
    download(`${fileBase(doc)}.ics`, url);
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <span className="tv-calendar">
      <button
        type="button"
        className="tv-btn tv-btn--ghost tv-btn--sm"
        title="Download stops, stays and transport as a calendar (.ics)"
        onClick={() => {
          const file = doc.start_date ? toIcs(doc) : start ? toIcs(datedFrom(doc, start)) : null;
          if (file) save(file);
          else setOpen(true);
        }}
      >
        Calendar
      </button>
      {open && !doc.start_date && (
        <label className="tv-calendar__date">
          Starts
          <input
            type="date"
            value={start}
            onChange={(event) => {
              const value = event.target.value;
              setStart(value);
              const file = value ? toIcs(datedFrom(doc, value)) : null;
              if (file) {
                save(file);
                setOpen(false);
              }
            }}
          />
        </label>
      )}
    </span>
  );
}
