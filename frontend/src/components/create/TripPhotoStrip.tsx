/*
 * TripPhotoStrip — a filmstrip of the trip's stops with credited Wikimedia photos.
 * Owned by the map session; mounted by Session 2's TripPreviewCard. Every photo carries its
 * credit, as the licenses require, so hosts never have to add one.
 */
import { useMemo } from 'react';
import type { Coordinates, ItineraryGraph } from './itineraryGraph';
import { useStopCoordinates } from './stopLocations';
import { creditLine, usePlaceMedia } from '../../services/placeMedia';
import { dayLabel } from './spatialModel';

interface Props {
  graph: ItineraryGraph;
  /** Known stop coordinates. When omitted, the strip locates stops itself (needs `destination`). */
  coordinates?: Record<string, Coordinates>;
  destination?: string;
  tripId?: string;
  onSelectStop?: (nodeId: string) => void;
}

const revealed = (event: React.SyntheticEvent<HTMLImageElement>) => event.currentTarget.classList.add('is-loaded');
const revealIfReady = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth) img.classList.add('is-loaded'); };

export function TripPhotoStrip({ graph, coordinates, destination, tripId, onSelectStop }: Props) {
  const located = useStopCoordinates(coordinates ? null : graph, destination, tripId).coordinates;
  const points = coordinates || located;
  const stops = useMemo(() => graph.nodes.filter((node) => node.kind === 'stop'), [graph]);
  // Only located stops are looked up, so the server's 20 km check can reject namesakes.
  const targets = useMemo(() => stops.flatMap((node) => points[node.id]
    ? [{ id: node.id, name: node.name, lat: points[node.id].lat, lon: points[node.id].lon }] : []), [stops, points]);
  const media = usePlaceMedia(targets);
  if (!stops.length) return null;

  return <ol className="tv-photostrip" aria-label="Photos of the stops on this trip">
    {stops.map((node) => {
      const photo = media[node.id];
      const Tile = onSelectStop ? 'button' : 'div';
      return <li key={node.id}>
        <Tile {...(onSelectStop ? { type: 'button' as const, onClick: () => onSelectStop(node.id) } : {})}
          className={`tv-photostrip__tile ${photo ? '' : 'is-empty'}`}>
          {photo && <img src={photo.image} alt={node.name} loading="lazy" decoding="async" onLoad={revealed} ref={revealIfReady} />}
          <span className="tv-photostrip__label"><small>{dayLabel(node)}</small><strong>{node.name}</strong></span>
        </Tile>
        {photo && <p className="tv-photostrip__credit" title={creditLine(photo)}>
          {photo.credit.file_page
            ? <a href={photo.credit.file_page} target="_blank" rel="noopener noreferrer">{creditLine(photo)}</a>
            : creditLine(photo)}
        </p>}
      </li>;
    })}
  </ol>;
}
