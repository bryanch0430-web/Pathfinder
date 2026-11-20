/*
 * Doodle icons: single-stroke paths on a 24×24 grid, drawn by hand for Pathfinder (CC0).
 * The renderer runs them through Rough.js, so they wobble like the rest of the page.
 */
export const DOODLES = {
  // Place categories (backend DAY_CATEGORIES)
  sight: 'M3 6c6-2 12-2 18 0 M5 9.5h14 M7 6v15 M17 6v15 M12 9.5V6',
  food: 'M3 12h18c0 5-4 8-9 8s-9-3-9-8z M14 3l-4 8 M19 4l-6 7',
  nature: 'M12 21v-7 M12 3c-4 0-6 3-6 6s3 5 6 5 6-2 6-5-2-6-6-6z M9 17l3-2 3 2',
  experience: 'M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6l-5.4 2.9 1.2-6-4.5-4.2 6.1-.7z',
  shopping: 'M5 8h14l-1 13H6z M9 8V6a3 3 0 0 1 6 0v2',
  nightlife: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
  transit: 'M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4z M14 7v10',
  stay: 'M3 19V6 M3 14h18v5 M21 14v-2a3 3 0 0 0-3-3h-7v5 M5 11.5h4',
  other: 'M12 21c-4-5-7-8.5-7-12a7 7 0 0 1 14 0c0 3.5-3 7-7 12z M10 9h4',
  // Ways of getting around
  plane: 'M3 12h17 M12 12L7 4h2l7 8 M12 12l-5 8h2l7-8 M4 12l-1-3h2l2 3',
  train: 'M7 3h10a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3z M4 11h16 M8 18l-2 3 M16 18l2 3',
  car: 'M3 16v-4l2-5h14l2 5v4z M3 12h18 M6 16v2.5 M18 16v2.5',
  bus: 'M5 3h14v14H5z M5 10h14 M7 17v3 M17 17v3 M9 13.5h6',
  walk: 'M13 4a1.5 1.5 0 1 0 0 .1 M12 8l-2 6 3 3v4 M12 8l3 3 3 1 M10 14l-3 7 M11 9l-3 3',
  ferry: 'M3 18c2 2 4 2 6 0 2 2 4 2 6 0 2 2 4 2 6 0 M5 15l1-5h12l1 5 M9 10V6h6v4',
  bike: 'M3 17a3 3 0 1 0 6 0 3 3 0 1 0-6 0 M15 17a3 3 0 1 0 6 0 3 3 0 1 0-6 0 M6 17l4-7h6l2 7 M10 10l3 7 M9 7h3',
  // Weather and calendar
  sun: 'M8 12a4 4 0 1 0 8 0 4 4 0 1 0-8 0 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  partly: 'M5 11a3.5 3.5 0 1 1 6-3 M3 5l1 1 M8 2v1.5 M9 20h9a3.5 3.5 0 0 0 0-7 4.5 4.5 0 0 0-8.6-1A3.5 3.5 0 0 0 9 20z',
  cloud: 'M7 18h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1.4A4 4 0 0 0 7 18z',
  fog: 'M4 8h16 M3 12h18 M5 16h14 M7 20h10',
  rain: 'M7 14h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1.4A4 4 0 0 0 7 14z M8 17l-1 3 M12 17l-1 3 M16 17l-1 3',
  snow: 'M7 14h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1.4A4 4 0 0 0 7 14z M7 17l2 2 M9 17l-2 2 M15 17l2 2 M17 17l-2 2',
  storm: 'M7 14h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6-1.4A4 4 0 0 0 7 14z M12 15l-2 4h4l-2 4',
  flag: 'M5 21V4 M5 4h12l-2 4 2 4H5',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
} as const;

export type DoodleName = keyof typeof DOODLES;

const CATEGORY = new Set(['sight', 'food', 'nature', 'experience', 'shopping', 'nightlife', 'transit', 'stay', 'other']);
export const categoryDoodle = (category: string): DoodleName => (CATEGORY.has(category) ? category : 'other') as DoodleName;

/** Free-text leg modes ("Shinkansen", "domestic flight") and brief travel modes to a doodle. */
export function modeDoodle(mode: string | null | undefined): DoodleName | null {
  const text = (mode || '').toLowerCase();
  if (!text) return null;
  if (/fl(y|ight)|air|plane/.test(text)) return 'plane';
  if (/ferry|boat|ship|cruise/.test(text)) return 'ferry';
  if (/bus|coach/.test(text)) return 'bus';
  if (/bike|cycl/.test(text)) return 'bike';
  if (/walk|foot/.test(text)) return 'walk';
  if (/car|drive|driving|taxi|road|cab/.test(text)) return 'car';
  if (/train|rail|shinkansen|metro|subway|transit|tram/.test(text)) return 'train';
  return null;
}
