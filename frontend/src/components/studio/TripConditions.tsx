/*
 * TripConditions: weather, public holidays and exchange rates for the trip's dates, read from the
 * trip document's `enrichment`. Self-contained, so the studio's info rail can mount it as-is.
 * Every figure carries its label ("Forecast" / "Typical for October") and every source is credited.
 * Renders nothing when there is nothing to show: a failed source simply hides its part.
 */
import type { ReactNode } from 'react';
import type { TripEnrichment, TripNote } from '../../services/tripService';
import '../../styles/trip-conditions.css';

type Weather = TripEnrichment['weather'][number];
type Glyph = NonNullable<Weather['condition']> | 'typical';

interface Props {
  enrichment: TripEnrichment | null | undefined;
  /** Highlights that day and narrows the heads-up list to it. */
  selectedDay?: number | null;
  onSelectDay?: (day: number) => void;
}

const CLOUD_HIGH = 'M7 14h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.2 5.1 4.5 4.5 0 0 0 7 14z';
const GLYPHS: Record<Glyph, ReactNode> = {
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" /></>,
  partly: <><circle cx="8" cy="8" r="3" /><path d="M8 2.5v1.2M2.5 8h1.2M4.1 4.1l.9.9M11.9 4.1l-.9.9" /><path d="M9 20h8a3.5 3.5 0 0 0 .5-6.96A4.8 4.8 0 0 0 8.4 12.2 3.9 3.9 0 0 0 9 20z" /></>,
  cloud: <path d="M7 18h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.2 9.1 4.5 4.5 0 0 0 7 18z" />,
  fog: <path d="M4 8h16M6 12h14M4 16h12M8 20h10" />,
  rain: <><path d={CLOUD_HIGH} /><path d="M9 17l-1 3M13 17l-1 3M17 17l-1 3" /></>,
  snow: <><path d={CLOUD_HIGH} /><path d="M8.5 18.5h.01M12.5 20.5h.01M16.5 18.5h.01" strokeWidth="2.6" /></>,
  storm: <><path d={CLOUD_HIGH} /><path d="M13 15l-2.5 3.5h3L11 22" /></>,
  typical: <path d="M10 14.5V5a2 2 0 1 1 4 0v9.5a4 4 0 1 1-4 0zM12 10v7" />,
};

const dayDate = (iso: string, withWeekday = true) => new Date(`${iso}T00:00`)
  .toLocaleDateString(undefined, withWeekday ? { weekday: 'short', day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short' });
const countryName = (code: string) => {
  try {
    return new Intl.DisplayNames(undefined, { type: 'region' }).of(code) || code;
  } catch {
    return code;
  }
};
const money = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: amount < 1 ? 3 : 2 }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
};
const temps = (w: Weather) => w.kind === 'forecast' && w.temp_min !== null && w.temp_max !== null
  ? `${Math.round(w.temp_min)}–${Math.round(w.temp_max)}°`
  : w.temp_mean !== null ? `~${Math.round(w.temp_mean)}°` : '';
const rain = (w: Weather) => w.rain_mm === null ? ''
  : w.kind === 'typical' ? `${w.rain_mm} mm/day`
  : w.rain_mm < 0.5 ? 'dry' : `${Math.round(w.rain_mm)} mm`;

function WeatherGlyph({ w }: { w: Weather }) {
  return <svg className="tv-cond__glyph" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{GLYPHS[w.kind === 'typical' ? 'typical' : w.condition || 'cloud']}</svg>;
}

export function TripConditions({ enrichment, selectedDay = null, onSelectDay }: Props) {
  if (!enrichment) return null;
  const { weather, holidays, exchange, sources } = enrichment;
  const rates = exchange ? Object.entries(exchange.rates) : [];
  if (!weather.length && !holidays.length && !rates.length) return null;

  const focus = (day: number) => selectedDay === null || selectedDay === day;
  const headsUp: { day: number; note: TripNote }[] = [
    ...holidays.filter((h) => focus(h.day)).map((h) => ({ day: h.day, note: {
      tone: 'warn' as const,
      text: `${h.name}${h.local_name && h.local_name !== h.name ? ` (${h.local_name})` : ''}: a public holiday in ${countryName(h.country)}`
        + `${h.regional ? ', in some regions' : ''}. Some places may close, and popular spots get busier.`,
    } })),
    ...weather.filter((w) => focus(w.day)).flatMap((w) => w.notes
      .filter((n) => selectedDay !== null || n.tone === 'warn')
      .map((note) => ({ day: w.day, note }))),
  ].sort((a, b) => a.day - b.day);

  return (
    <section className="tv-cond" aria-label="Weather, holidays and money">
      {weather.length > 0 && (
        <div className="tv-cond__block">
          <h3>Weather</h3>
          <ol className="tv-cond__days">
            {weather.map((w) => {
              const Row = onSelectDay ? 'button' : 'div';
              return <li key={w.day}>
                <Row className="tv-cond__day" data-tone={w.badge?.tone}
                  {...(onSelectDay ? { type: 'button' as const, onClick: () => onSelectDay(w.day), 'aria-pressed': selectedDay === w.day } : {})}>
                  <WeatherGlyph w={w} />
                  <span className="tv-cond__when"><strong>Day {w.day}</strong><small>{dayDate(w.date)}</small></span>
                  <span className="tv-cond__figures"><strong>{temps(w)}</strong><small>{rain(w)}</small></span>
                  <span className="tv-cond__label">{w.label}{w.badge ? <em>{w.badge.text}</em> : null}</span>
                </Row>
              </li>;
            })}
          </ol>
        </div>
      )}

      {headsUp.length > 0 && (
        <div className="tv-cond__block">
          <h3>Heads-up{selectedDay !== null ? ` · Day ${selectedDay}` : ''}</h3>
          <ul className="tv-cond__notes">
            {headsUp.slice(0, 8).map(({ day, note }, index) => (
              <li key={`${day}-${index}`} data-tone={note.tone}>
                {selectedDay === null && <b>Day {day}</b>}{note.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {exchange && rates.length > 0 && (
        <div className="tv-cond__block">
          <h3>Money</h3>
          <dl className="tv-cond__rates">
            {rates.map(([code, rate]) => {
              // Quote the foreign side in a round amount that converts to at least 1 of the trip currency.
              const amount = 10 ** Math.max(0, Math.ceil(Math.log10(rate)) + 1);
              return <div key={code}>
                <dt>{money(amount, code)}</dt><dd>≈ {money(amount / rate, exchange.base)}</dd>
              </div>;
            })}
          </dl>
          <p className="tv-cond__fine">Reference rates from {dayDate(exchange.date, false)}. Card and cash rates differ a little.</p>
        </div>
      )}

      {sources.length > 0 && (
        <p className="tv-cond__credits">
          {sources.map((s, index) => <span key={s.url}>{index ? ' · ' : ''}{s.covers}: <a href={s.url} target="_blank" rel="noopener noreferrer">{s.name}</a></span>)}
        </p>
      )}
    </section>
  );
}
