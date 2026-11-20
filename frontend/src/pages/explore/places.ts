/*
 * What the Explore page shows. Every description says what its photograph
 * shows; months are general travel knowledge, labelled as "good months", not
 * a forecast. Images are WebP derivatives made by `scripts/explore_assets.py`.
 */

export type Kind = 'Cities' | 'Nature' | 'Culture' | 'Food & nights';

export interface Place {
  id: string;
  city: string;
  country: string;
  kind: Kind;
  title: string;
  description: string;
  nights: number;
  months: string;
  coords: string;
  /** What lands in the planner's message box, ready to send. */
  prompt: string;
}

export interface Mood {
  id: string;
  name: string;
  /** The italic second clause, in the house headline style. */
  turn: string;
  line: string;
  images: [string, string];
  alts: [string, string];
  places: string[];
}

export const placeImage = (id: string) => `/images/explore/places/${id}.webp`;
export const moodImage = (name: string) => `/images/explore/moods/${name}.webp`;

export const PLACES: Place[] = [
  {
    id: 'kyoto-garden', city: 'Kyoto', country: 'Japan', kind: 'Culture',
    title: 'Temple gardens and red bridges',
    description: 'Moss gardens, still water, and a vermilion bridge under maples turning at the edge of the season.',
    nights: 4, months: 'Apr · Nov', coords: '35.01° N 135.77° E',
    prompt: 'Plan 4 days in Kyoto: temple gardens, quiet mornings and good food.',
  },
  {
    id: 'porto-douro', city: 'Porto', country: 'Portugal', kind: 'Cities',
    title: 'The Douro from Ribeira',
    description: 'Terracotta roofs stacked down to the waterfront, rabelo boats tied along the quay.',
    nights: 3, months: 'May – Sep', coords: '41.14° N 8.61° W',
    prompt: 'Plan 3 days in Porto along the Douro, with the riverside and the old town.',
  },
  {
    id: 'tokyo-alley', city: 'Tokyo', country: 'Japan', kind: 'Cities',
    title: 'Back streets after the rain',
    description: 'Narrow lanes lit by signage, the quiet grid behind the crossings everyone photographs.',
    nights: 5, months: 'Mar – May · Oct', coords: '35.69° N 139.70° E',
    prompt: 'Plan 5 days in Tokyo, away from the obvious crossings: back streets, small bars, local food.',
  },
  {
    id: 'alpine-lake', city: 'Alpine lakes', country: 'Central Europe', kind: 'Nature',
    title: 'A lake village under the ridge',
    description: 'Pasture running to the shoreline, a single jetty, and the ferry that connects the far bank.',
    nights: 4, months: 'Jun – Sep', coords: '47.48° N 11.76° E',
    prompt: 'Plan 4 slow days in an alpine lake village, with easy walks and a ferry across the lake.',
  },
  {
    id: 'prague-rooftops', city: 'Prague', country: 'Czechia', kind: 'Cities',
    title: 'Spires over the Old Town',
    description: 'Gothic towers catching first light above a roofscape of copper and clay.',
    nights: 3, months: 'May – Sep', coords: '50.09° N 14.42° E',
    prompt: 'Plan 3 days in Prague: the Old Town early, the castle, and the quieter districts.',
  },
  {
    id: 'seoul-palace', city: 'Seoul', country: 'South Korea', kind: 'Culture',
    title: 'Palace eaves and painted beams',
    description: 'Dancheong paintwork under deep eaves, the grounds quiet before the gates open.',
    nights: 5, months: 'Apr – May · Oct', coords: '37.58° N 126.98° E',
    prompt: 'Plan 5 days in Seoul with the palaces, hanok lanes and night markets.',
  },
  {
    id: 'fuji-blossom', city: 'Fujiyoshida', country: 'Japan', kind: 'Nature',
    title: 'Fuji through the blossom',
    description: 'The cone framed by branches for the ten days a year the timing actually works.',
    nights: 2, months: 'Late Mar – early Apr', coords: '35.36° N 138.73° E',
    prompt: 'Plan 2 days near Mount Fuji in cherry blossom season.',
  },
  {
    id: 'budapest-aerial', city: 'Budapest', country: 'Hungary', kind: 'Cities',
    title: 'Above the Buda rooftops',
    description: 'Tiled church roofs and the river beyond, best walked in the hour before dusk.',
    nights: 3, months: 'Apr – Jun · Sep', coords: '47.50° N 19.03° E',
    prompt: 'Plan 3 days in Budapest: Buda at dusk, the baths, and the river.',
  },
  {
    id: 'sydney-harbour', city: 'Sydney', country: 'Australia', kind: 'Cities',
    title: 'The harbour from the air',
    description: 'Ferries crossing between the bridge and the shells, the coastal track heading south.',
    nights: 6, months: 'Sep – Nov · Mar', coords: '33.86° S 151.22° E',
    prompt: 'Plan 6 days in Sydney with harbour ferries and the coastal walk.',
  },
  {
    id: 'izakaya-lane', city: 'Osaka', country: 'Japan', kind: 'Food & nights',
    title: 'Lanterns along the izakaya lane',
    description: 'Paper lanterns, hand-written menus, and counters that seat eight people at most.',
    nights: 2, months: 'Year-round', coords: '34.67° N 135.50° E',
    prompt: 'Plan 2 days in Osaka built around food: izakaya lanes, counters and street snacks.',
  },
  {
    id: 'pagoda-fuji', city: 'Fujiyoshida', country: 'Japan', kind: 'Culture',
    title: 'Five storeys and a volcano',
    description: 'The pagoda above the town, with the mountain doing the rest of the work behind it.',
    nights: 2, months: 'Nov – Feb (clearest)', coords: '35.40° N 138.80° E',
    prompt: 'Plan 2 days around Fujiyoshida with the Chureito Pagoda at sunrise.',
  },
  {
    id: 'luxembourg-dusk', city: 'Luxembourg', country: 'Luxembourg', kind: 'Cities',
    title: 'River town at blue hour',
    description: 'Bridges lit along the valley floor, the old quarter stacked on the bluff above.',
    nights: 2, months: 'May – Sep', coords: '49.61° N 6.13° E',
    prompt: 'Plan 2 days in Luxembourg City, with the old quarter and the valley walks.',
  },
  {
    id: 'dubai-marina', city: 'Dubai', country: 'UAE', kind: 'Cities',
    title: 'Towers on the marina',
    description: 'A skyline built in twenty years, best read from the water at the end of the day.',
    nights: 3, months: 'Nov – Mar', coords: '25.08° N 55.14° E',
    prompt: 'Plan 3 days in Dubai in the cooler months, with the marina and the old creek.',
  },
  {
    id: 'osaka-night', city: 'Osaka', country: 'Japan', kind: 'Food & nights',
    title: 'One lantern, one door',
    description: 'A dark side street, a single lit entrance, and a hand-written sign by the door.',
    nights: 3, months: 'Year-round', coords: '34.69° N 135.50° E',
    prompt: 'Plan 3 days in Osaka around small restaurants and late, quiet streets.',
  },
];

export const KINDS: Kind[] = ['Cities', 'Nature', 'Culture', 'Food & nights'];

export const MOODS: Mood[] = [
  {
    id: 'slow-mornings', name: 'Slow mornings', turn: 'before the crowds.',
    line: 'Places that are best at first light, when the gates have just opened and the paths are empty.',
    images: ['prayer-flags-sunrise', 'sunlit-water'],
    alts: ['Prayer flags over a mountain village at sunrise', 'A traveller sitting by still water in low sun'],
    places: ['kyoto-garden', 'seoul-palace', 'alpine-lake'],
  },
  {
    id: 'salt-and-wind', name: 'Salt and wind', turn: 'a coast to walk.',
    line: 'Harbours, cliffs and river mouths, with a ferry or a coastal path in most days.',
    images: ['surfers-cliff', 'beach-leap'],
    alts: ['Two surfers on a cliff above the sea', 'Two friends leaping on a beach'],
    places: ['sydney-harbour', 'porto-douro'],
  },
  {
    id: 'high-places', name: 'High places', turn: 'thin air, long views.',
    line: 'Mountains you can see from the window, and days planned around the weather on them.',
    images: ['alpine-jacket', 'valley-rain-run'],
    alts: ['A hiker in a white jacket below snow peaks', 'Two friends running through a misty valley'],
    places: ['fuji-blossom', 'pagoda-fuji', 'alpine-lake'],
  },
  {
    id: 'old-stone', name: 'Old stone', turn: 'walked, not driven.',
    line: 'Old towns made for walking, linked by short trains, with towers worth the climb.',
    images: ['gulls-and-ramparts', 'crosswalk-shadows'],
    alts: ['Gulls over an old harbour rampart', 'Long shadows of people on a striped crossing'],
    places: ['prague-rooftops', 'budapest-aerial', 'luxembourg-dusk'],
  },
  {
    id: 'rain-and-lanterns', name: 'Rain and lanterns', turn: 'better wet.',
    line: 'Cities that come alive when it rains: covered lanes, counter seats and a lantern at every door.',
    images: ['pink-umbrellas', 'headlamp-night'],
    alts: ['Two people with pink umbrellas on a wet street', 'A traveller with a headlamp on a dark night'],
    places: ['tokyo-alley', 'izakaya-lane', 'osaka-night'],
  },
  {
    id: 'after-dark', name: 'After dark', turn: 'the second half of the day.',
    line: 'Skylines and river towns that are at their best after sunset.',
    images: ['big-moon', 'camp-by-the-sea'],
    alts: ['A crowd watching a huge rising moon', 'A tent glowing by the sea at dusk'],
    places: ['dubai-marina', 'luxembourg-dusk', 'izakaya-lane'],
  },
];

export const placeById = (id: string) => PLACES.find((place) => place.id === id)!;

/** Opens the planner with the place's prompt typed into the message box (not sent). */
export function planFrom(place: Place | undefined, open: () => void) {
  try {
    if (place) sessionStorage.setItem('tripverse-draft', place.prompt);
  } catch {
    // Storage blocked: the planner just opens empty.
  }
  open();
}
