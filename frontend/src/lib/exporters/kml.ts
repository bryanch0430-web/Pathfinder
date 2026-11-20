/* KML 2.2 for Google My Maps and Google Earth: a folder per day with its places and a path. */
import type { TripDocument } from '../../services/tripService';
import { tripTitle } from './download';
import { locatedStops, type PlacePoints, xml } from './points';

const lonLat = ({ lat, lon }: { lat: number; lon: number }) => `${lon.toFixed(6)},${lat.toFixed(6)}`;

export function toKml(doc: TripDocument, points: PlacePoints): string {
  const folders = doc.days.map((day) => ({ day, stops: locatedStops(day, points) })).filter(({ stops }) => stops.length)
    .map(({ day, stops }) => [
      `    <Folder><name>${xml(`Day ${day.day} · ${day.base}`)}</name>`,
      ...stops.map(({ item, point }) => `      <Placemark><name>${xml(item.name)}</name>`
        + `<description>${xml([item.category, item.tip].filter(Boolean).join(' · '))}</description>`
        + `<Point><coordinates>${lonLat(point)}</coordinates></Point></Placemark>`),
      ...(stops.length > 1 ? [`      <Placemark><name>${xml(`Day ${day.day} route`)}</name><LineString><tessellate>1</tessellate>`
        + `<coordinates>${stops.map(({ point }) => lonLat(point)).join(' ')}</coordinates></LineString></Placemark>`] : []),
      '    </Folder>',
    ].join('\n'));
  return ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">',
    `  <Document><name>${xml(tripTitle(doc))}</name>`, ...folders, '  </Document>', '</kml>', ''].join('\n');
}
