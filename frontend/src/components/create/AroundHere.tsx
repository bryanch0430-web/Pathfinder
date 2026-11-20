/*
 * AroundHere — what to see, eat and where to stay near a stop, plus landmarks and stations.
 * Owned by the map session. Shown in the stop detail panel; the studio's info rail can mount it too.
 * Listings come from Wikivoyage and landmarks from Wikipedia, each credited. No ratings: there is
 * no free, legitimate source for them. "Add to day" appears only when the host passes onAddPlace.
 */
import { useMemo, useState } from 'react';
import { useAroundHere, type AroundItem, type AroundTab } from '../../services/aroundHere';

interface Props {
  lat: number;
  lon: number;
  /** The day the stop belongs to, passed back with "Add to day". */
  day?: number | null;
  onAddPlace?: (name: string, day: number | null) => void;
}

const TABS: { id: AroundTab; label: string }[] = [
  { id: 'see', label: 'Things to see' }, { id: 'food', label: 'Food' }, { id: 'stay', label: 'Stay' }, { id: 'landmarks', label: 'Landmarks & transit' },
];
const KIND: Record<AroundItem['kind'], string> = {
  see: 'Sight', do: 'Activity', buy: 'Shopping', eat: 'Eat', drink: 'Drinks', sleep: 'Stay', landmark: 'Landmark', transit: 'Transit',
};
const PREVIEW = 5;

const revealed = (event: React.SyntheticEvent<HTMLImageElement>) => event.currentTarget.classList.add('is-loaded');
const revealIfReady = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth) img.classList.add('is-loaded'); };
// airDistanceKm rounds to whole km, too coarse for "120 m away".
function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number) {
  const rad = Math.PI / 180;
  const arc = Math.sin((lat2 - lat1) * rad / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin((lon2 - lon1) * rad / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(arc));
}
const km = (value: number) => value < 1 ? `${Math.round(value * 1000 / 10) * 10} m` : `${value.toFixed(1)} km`;

export function AroundHere({ lat, lon, day = null, onAddPlace }: Props) {
  const { data, loading } = useAroundHere(lat, lon);
  const [picked, setPicked] = useState<AroundTab | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [added, setAdded] = useState<Set<string>>(() => new Set());

  // The server measures from its ~1 km cell; distances here are from the stop itself.
  const byTab = useMemo(() => {
    const groups: Record<AroundTab, (AroundItem & { away: number | null })[]> = { see: [], food: [], stay: [], landmarks: [] };
    for (const item of data?.items || []) {
      const away = item.lat != null && item.lon != null ? distanceKm(lat, lon, item.lat, item.lon) : null;
      groups[item.tab].push({ ...item, away });
    }
    Object.values(groups).forEach((list) => list.sort((a, b) => (a.away ?? Infinity) - (b.away ?? Infinity)));
    return groups;
  }, [data, lat, lon]);
  const tab = picked && byTab[picked].length ? picked : TABS.find((item) => byTab[item.id].length)?.id || 'see';
  const items = byTab[tab];
  const shown = expanded ? items : items.slice(0, PREVIEW);

  return <section className="tv-sd__block tv-ah" aria-label="Around here">
    <h4>Around here <span>{loading ? 'searching…' : data ? `${data.items.length} places` : ''}</span></h4>
    {loading ? <ul className="tv-ah__list" aria-hidden="true">{[0, 1, 2].map((i) => <li key={i} className="tv-ah__card is-ghost"><i /><div><b /><b /></div></li>)}</ul>
      : !data ? <p className="tv-sd__note">Couldn't reach Wikivoyage or Wikipedia just now. Try this stop again later.</p>
        : !data.items.length ? <p className="tv-sd__note">No listed places near this stop.</p>
          : <>
            <div className="tv-ah__tabs" role="tablist" aria-label="Kinds of places">
              {TABS.map((item) => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id}
                disabled={!byTab[item.id].length} className={tab === item.id ? 'is-active' : ''}
                onClick={() => { setPicked(item.id); setExpanded(false); }}>
                {item.label}<em>{byTab[item.id].length}</em>
              </button>)}
            </div>
            <ul className="tv-ah__list" role="tabpanel" aria-label={TABS.find((item) => item.id === tab)?.label}>
              {shown.map((item) => <li key={item.id} className="tv-ah__card">
                <div className={`tv-ah__media is-${item.tab}`}>
                  {item.image ? <img src={item.image} alt="" loading="lazy" decoding="async" onLoad={revealed} ref={revealIfReady} />
                    : <span aria-hidden="true">{item.name.replace(/^the\s+/i, '').charAt(0)}</span>}
                </div>
                <div className="tv-ah__body">
                  <strong>{item.name}{item.alt && <small>{item.alt}</small>}</strong>
                  <p className="tv-ah__meta">{[item.away !== null ? km(item.away) : null, KIND[item.kind], item.hours].filter(Boolean).join(' · ')}</p>
                  {item.blurb && <p className="tv-ah__blurb">{item.blurb}</p>}
                  {item.price && <p className="tv-ah__price">{item.price} <small>Listed price, may be outdated</small></p>}
                  <div className="tv-ah__links">
                    {onAddPlace && <button type="button" disabled={added.has(item.id)} onClick={() => {
                      onAddPlace(item.name, day);
                      setAdded((prev) => new Set(prev).add(item.id));
                    }}>{added.has(item.id) ? 'Added' : day ? `Add to day ${day}` : 'Add to plan'}</button>}
                    {item.website && <a href={item.website} target="_blank" rel="noopener noreferrer">Website ↗</a>}
                    <a href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(item.lat != null ? `${item.lat},${item.lon}` : item.name)}`}
                      target="_blank" rel="noopener noreferrer">Map ↗</a>
                    {item.source_url && <a href={item.source_url} target="_blank" rel="noopener noreferrer">{item.source} ↗</a>}
                  </div>
                  {item.image && item.image_credit && <p className="tv-ah__credit">
                    <a href={item.image_credit.file_page || undefined} target="_blank" rel="noopener noreferrer">
                      {['Photo: Wikimedia Commons', item.image_credit.author, item.image_credit.license].filter(Boolean).join(' · ')}
                    </a>
                  </p>}
                </div>
              </li>)}
            </ul>
            {items.length > PREVIEW && <button type="button" className="tv-ah__more" onClick={() => setExpanded((value) => !value)}>
              {expanded ? 'Show fewer' : `Show all ${items.length}`}
            </button>}
            <p className="tv-ah__sources">
              {!!data.guides.length && <>Listings: {data.guides.map((guide, i) => <span key={guide.url}>{i ? ', ' : ''}
                <a href={guide.url} target="_blank" rel="noopener noreferrer">Wikivoyage {guide.title}</a></span>)} · CC BY-SA 4.0. </>}
              Landmarks: Wikipedia · CC BY-SA 4.0.
            </p>
          </>}
  </section>;
}
