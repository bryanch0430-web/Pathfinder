/*
 * The text half of the trip PDF, as plain rows: the day-by-day plan with tips and
 * sources, weather and holidays, the budget, and the credits. Pure, so it's tested
 * without a browser; pdf.ts lays the rows out.
 */
import type { TripDocument } from '../../services/tripService';
import { dayTitle, money } from '../../components/sketch/layout';

export type RowKind = 'title' | 'heading' | 'label' | 'item' | 'meta' | 'tip' | 'text' | 'gap';
export interface Row { kind: RowKind; text: string; link?: string }

const SLOTS: [string, string][] = [['morning', 'Morning'], ['afternoon', 'Afternoon'], ['evening', 'Evening']];
const SOURCES = ['reddit', 'quora', 'tripadvisor'];

const hostOf = (url: string) => {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; }
};
export const sourceName = (url: string) => SOURCES.find((name) => url.toLowerCase().includes(name)) ?? hostOf(url) ?? 'source';

/** Fonts, libraries and art used to make the PDF itself (data sources come from the trip). */
export const MADE_WITH: Row[] = [
  { kind: 'text', text: 'Handwriting: Caveat (Impallari Type) and Yomogi (Satsuyako), SIL Open Font License 1.1.' },
  { kind: 'text', text: 'Drawing: Rough.js (MIT) and perfect-freehand (MIT). PDF: jsPDF (MIT) and svg2pdf.js (MIT).' },
  { kind: 'text', text: 'Doodle icons and the six guide solids are drawn for Pathfinder.' },
];

export function planRows(doc: TripDocument): Row[] {
  const rows: Row[] = doc.is_final
    ? [{ kind: 'title', text: 'Final trip plan' }, { kind: 'meta', text: 'Confirmed itinerary · read-only snapshot' }, { kind: 'title', text: 'Day by day' }]
    : [{ kind: 'title', text: 'Day by day' }];
  const cur = doc.budget.currency;
  for (const day of doc.days) {
    rows.push({ kind: 'heading', text: `${dayTitle(day.day, day.date)}${day.base ? ` · ${day.base}` : ''}` });
    const facts = [
      day.hours ? `about ${Math.round(day.hours)} h of plans` : null,
      day.est_cost ? `about ${money(day.est_cost, cur)}` : null,
    ].filter(Boolean).join(' · ');
    if (facts) rows.push({ kind: 'meta', text: facts });
    if (day.travel_hours != null && day.items.length) rows.push({ kind: 'meta', text:
      `Travel: about ${day.travel_hours} h from ${day.base} through the places and back${day.travel_distance_km ? ` · ${day.travel_distance_km} km straight line` : ''}${day.unverified_legs ? ` · ${day.unverified_legs} unverified legs` : ` · ${day.travel_basis === 'road' ? 'road time' : 'distance estimate'}`}` });
    doc.route_warnings?.filter((warning) => warning.day === day.day).forEach((warning) =>
      rows.push({ kind: 'meta', text: `Travel check (${warning.severity}): ${warning.message}` }));

    const weather = doc.enrichment?.weather.find((entry) => entry.day === day.day);
    if (weather) {
      const temps = weather.temp_min != null && weather.temp_max != null ? `${Math.round(weather.temp_min)}–${Math.round(weather.temp_max)}°C`
        : weather.temp_mean != null ? `~${Math.round(weather.temp_mean)}°C` : null;
      rows.push({ kind: 'meta', text: ['Weather', weather.label, temps, weather.badge?.text].filter(Boolean).join(' · ') });
      weather.notes.forEach((note) => rows.push({ kind: 'meta', text: note.text }));
    }
    doc.enrichment?.holidays.filter((entry) => entry.day === day.day)
      .forEach((entry) => rows.push({ kind: 'meta', text: `Public holiday: ${entry.name}${entry.local_name && entry.local_name !== entry.name ? ` (${entry.local_name})` : ''}` }));

    if (!day.items.length) rows.push({ kind: 'text', text: 'A free day: nothing planned yet.' });
    const groups: [string, typeof day.items][] = [
      ...SLOTS.map(([slot, label]) => [label, day.items.filter((item) => item.time_of_day === slot)] as [string, typeof day.items]),
      ['Any time', day.items.filter((item) => !SLOTS.some(([slot]) => slot === item.time_of_day))],
    ];
    for (const [label, items] of groups) {
      if (!items.length) continue;
      rows.push({ kind: 'label', text: label });
      for (const item of items) {
        const meta = [
          item.option ? 'optional' : null, item.area, item.duration_hours ? `${item.duration_hours} h` : null,
          item.est_cost ? money(item.est_cost, cur) : item.est_cost === 0 ? 'free' : null,
        ].filter(Boolean).join(' · ');
        rows.push({ kind: 'item', text: meta ? `${item.name} — ${meta}` : item.name });
        if (item.tip) rows.push({ kind: 'tip', text: `Tip: ${item.tip}` });
        if (item.source_url) rows.push({ kind: 'tip', text: `Source: ${sourceName(item.source_url)}`, link: item.source_url });
      }
    }
    rows.push({ kind: 'gap', text: '' });
  }

  const { target, entered, projected, is_locked, locked_total } = doc.budget;
  const total = is_locked ? locked_total ?? entered : projected;
  rows.push({ kind: 'title', text: is_locked ? 'Final budget' : 'Budget' });
  if (target != null) rows.push({ kind: 'item', text: `Budget: ${money(target, cur)}` });
  if (!is_locked && entered) rows.push({ kind: 'item', text: `Entered costs: ${money(entered, cur)}` });
  rows.push({ kind: 'item', text: `${is_locked ? 'Final total' : 'Total'}: ${total ? money(total, cur) : 'not costed yet'}${target != null && total > target ? ` (over by ${money(total - target, cur)})` : ''}` });
  const costed = doc.days.filter((day) => day.est_cost);
  if (costed.length) {
    rows.push({ kind: 'label', text: 'By day' });
    costed.forEach((day) => rows.push({ kind: 'meta', text: `Day ${day.day}${day.base ? ` · ${day.base}` : ''}: ${money(day.est_cost!, cur)}` }));
  }
  const rates = doc.enrichment?.exchange;
  if (rates) {
    const shown = Object.entries(rates.rates).slice(0, 4).map(([code, rate]) => `1 ${rates.base} = ${rate} ${code}`).join(' · ');
    if (shown) rows.push({ kind: 'meta', text: `Exchange rates (${rates.date}): ${shown}` });
  }
  rows.push({ kind: 'gap', text: '' });

  rows.push({ kind: 'title', text: 'Credits' });
  for (const source of doc.enrichment?.sources ?? []) {
    rows.push({ kind: 'text', text: `${source.name}: ${source.covers}`, link: source.url });
  }
  const community = new Set(doc.days.flatMap((day) => day.items.flatMap((item) => (item.source_url ? [sourceName(item.source_url)] : []))));
  if (community.size) rows.push({ kind: 'text', text: `Traveler tips from ${[...community].join(', ')} (linked beside each tip).` });
  rows.push(...MADE_WITH);
  return rows;
}
