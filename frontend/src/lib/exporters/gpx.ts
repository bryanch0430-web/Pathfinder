/* GPX 1.1 for Organic Maps, Maps.me and GPS apps: a waypoint per place, a route per day. */
import type { TripDocument } from '../../services/tripService';
import { tripTitle } from './download';
import { locatedStops, type PlacePoints, xml } from './points';

const coord = (value: number) => value.toFixed(6);

export function toGpx(doc: TripDocument, points: PlacePoints): string {
  const days = doc.days.map((day) => ({ day, stops: locatedStops(day, points) })).filter(({ stops }) => stops.length);
  // GPX requires every <wpt> before any <rte>.
  const waypoints = days.flatMap(({ day, stops }) => stops.map(({ item, point }) =>
    `  <wpt lat="${coord(point.lat)}" lon="${coord(point.lon)}"><name>${xml(item.name)}</name>`
    + `<desc>${xml(`Day ${day.day} · ${day.base}${item.tip ? ` · ${item.tip}` : ''}`)}</desc><type>${xml(item.category)}</type></wpt>`));
  const routes = days.filter(({ stops }) => stops.length > 1).map(({ day, stops }) =>
    `  <rte><name>${xml(`Day ${day.day} · ${day.base}`)}</name>\n`
    + stops.map(({ item, point }) => `    <rtept lat="${coord(point.lat)}" lon="${coord(point.lon)}"><name>${xml(item.name)}</name></rtept>`).join('\n')
    + '\n  </rte>');
  return ['<?xml version="1.0" encoding="UTF-8"?>',
    '<gpx version="1.1" creator="Pathfinder" xmlns="http://www.topografix.com/GPX/1/1">',
    `  <metadata><name>${xml(tripTitle(doc))}</name></metadata>`, ...waypoints, ...routes, '</gpx>', ''].join('\n');
}
