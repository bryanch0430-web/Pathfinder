/*
 * Everything the Credits page lists. Credits live only here.
 * credits.test.ts fails when a photo used under src/ is missing from the lists below.
 */
export interface CreditRow { name: string; href?: string; note: string; licence: string }
export interface PhotoCredit { name: string; url: string }
export interface PhotoSet { site: string; href: string; licence: string; credits: PhotoCredit[] }
export interface CreditGroup { id: string; title: string; intro: string; rows?: CreditRow[]; photos?: PhotoSet[] }

// File names exactly as downloaded: the photographer and photo id are in the name.
export const UNSPLASH_FILES = [
  'alexandru-bogdan-ghita-UeYkqQh4PoI-unsplash.jpg', 'ben-klewais-nLE3eLaQA6A-unsplash.jpg',
  'bilderboken-rlwE8f8anOc-unsplash.jpg', 'brady-bellini-t5dGNNQVwg8-unsplash.jpg',
  'caleb-JmuyB_LibRo-unsplash.jpg', 'christoph-schulz-7tb-b37yHx4-unsplash.jpg',
  'datingscout-RlQ29vvbU2Q-unsplash.jpg', 'david-emrich-VCM99u6HltA-unsplash.jpg',
  'fredy-martinez-frd7WNzipdU-unsplash.jpg', 'james-wilkinson-FMuorhl0EHY-unsplash.jpg',
  'jay-wennington-N_Y88TWmGwA-unsplash.jpg', 'jj-ying-9Qwbfa_RM94-unsplash.jpg',
  'kate-trysh-U3CntDq16yY-unsplash.jpg', 'matthieu-buhler-PaFHv0Zi71E-unsplash.jpg',
  'mountain-girl-WfT3o1KhnwQ-unsplash.jpg', 'pedro-lastra-5g8dJvtYRYA-unsplash.jpg',
  'pema-g-lama-6cfK0SEtpbY-unsplash.jpg', 'philipp-trubchenko-oOTo9nR7f9Q-unsplash.jpg',
  'pierre-blache-VMNG8BYFQfs-unsplash.jpg', 'shigeki-wakabayashi-6nuz52vsbWc-unsplash.jpg',
  'vojtech-bruzek-Yrxr3bsPdS0-unsplash.jpg', 'will-goodman-1EikowqH9fs-unsplash.jpg',
];
export const PEXELS_FILES = [
  'pexels-815774834-19259314.jpg', 'pexels-cruz-in-portugal-22037151.jpg', 'pexels-hikaique-1563234.jpg',
  'pexels-lorenzomessinaph-34666834.jpg', 'pexels-marceloverfe-16338751.jpg', 'pexels-popovkin-27401407.jpg',
  'pexels-ugurcan-ozmen-61083217-28553917.jpg', 'pexels-zeynep-yilmaz-327514331-31922059.jpg',
];

const titleCase = (slug: string) => slug.split('-').filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');

/**
 * Unsplash files are `<photographer>-<11-character id>-unsplash.jpg`, Pexels files
 * `pexels-<photographer>[-<user id>]-<photo id>.jpg`. Names come from the slug, so a handle
 * reads as a handle ("Lorenzomessinaph"); a Pexels slug that is only a number has no name.
 */
export function photoCredit(file: string): PhotoCredit | null {
  const unsplash = file.match(/^(.+)-([\w-]{11})-unsplash\.jpg$/);
  if (unsplash) return { name: titleCase(unsplash[1]), url: `https://unsplash.com/photos/${unsplash[2]}` };
  const pexels = file.match(/^pexels-(.+)-(\d+)\.jpg$/);
  if (pexels) {
    const name = titleCase(pexels[1].replace(/(^|-)\d+(?=-|$)/g, ''));
    return { name: name || 'Pexels contributor', url: `https://www.pexels.com/photo/${pexels[2]}/` };
  }
  return null;
}

const credits = (files: string[]) => files.map(photoCredit).filter((c): c is PhotoCredit => c !== null);

export const GROUPS: CreditGroup[] = [
  {
    id: 'data', title: 'Data and maps',
    intro: 'Live data behind the weather, holidays, exchange rates and map views. When a source is down, that part quietly hides.',
    rows: [
      { name: 'MET Norway', href: 'https://api.met.no/', note: 'Weather forecast for trip days inside the forecast window.', licence: 'CC BY 4.0' },
      { name: 'NASA POWER', href: 'https://power.larc.nasa.gov/', note: 'Typical weather for the month, for trips further out than a forecast reaches.', licence: 'U.S. government data' },
      { name: 'Nager.Date', href: 'https://date.nager.at/', note: 'Public holidays in the destination country.', licence: 'Free public API' },
      { name: 'Frankfurter', href: 'https://frankfurter.dev/', note: 'Exchange rates, from European Central Bank reference rates.', licence: 'Free public API' },
      { name: 'OpenStreetMap contributors', href: 'https://www.openstreetmap.org/copyright', note: 'Place lookup, and the coordinates in GPX and KML exports.', licence: 'ODbL' },
      { name: 'OpenFreeMap', href: 'https://openfreemap.org/', note: 'The everyday map tiles.', licence: '© OpenMapTiles, OpenStreetMap data' },
      { name: 'Google Maps', href: 'https://developers.google.com/maps', note: 'Satellite and on-demand map views.', licence: 'Google Maps Platform terms' },
    ],
  },
  {
    id: 'places', title: 'Places and travel tips',
    intro: 'Place photos and descriptions come from Wikimedia projects. Only freely licensed photos are used, and each one carries its author and licence wherever it appears.',
    rows: [
      { name: 'Wikimedia Commons', href: 'https://commons.wikimedia.org/', note: 'Place photos.', licence: 'Free licence, per photo' },
      { name: 'Wikipedia', href: 'https://www.wikipedia.org/', note: 'Place summaries, nearby landmarks and stations.', licence: 'CC BY-SA 4.0' },
      { name: 'Wikivoyage', href: 'https://www.wikivoyage.org/', note: 'Sights, food, drink, places to stay and shops in Around here.', licence: 'CC BY-SA 4.0' },
      { name: 'Wikidata', href: 'https://www.wikidata.org/', note: 'Matches listings to their Wikipedia articles.', licence: 'CC0' },
      { name: 'Traveler posts', note: 'Reddit, Quora and TripAdvisor forum posts found through web search. Tips are paraphrased and link back to the post.', licence: "Each site's terms" },
    ],
  },
  {
    id: 'photography', title: 'Photography',
    intro: 'Photographs on the home, sign-in and sign-up pages.',
    photos: [
      { site: 'Unsplash', href: 'https://unsplash.com/license', licence: 'Free to use under the Unsplash licence.', credits: credits(UNSPLASH_FILES) },
      { site: 'Pexels', href: 'https://www.pexels.com/license/', licence: 'Free to use under the Pexels licence.', credits: credits(PEXELS_FILES) },
    ],
  },
  {
    id: 'fonts', title: 'Fonts',
    intro: 'All open-source fonts under the SIL Open Font License.',
    rows: [
      { name: 'Instrument Serif', href: 'https://fonts.google.com/specimen/Instrument+Serif', note: 'Headlines.', licence: 'OFL 1.1' },
      { name: 'Geist and Geist Mono', href: 'https://vercel.com/font', note: 'Text, numbers and labels.', licence: 'OFL 1.1' },
      { name: 'Lato', href: 'https://fonts.google.com/specimen/Lato', note: 'Text on some older screens.', licence: 'OFL 1.1' },
      { name: 'Jockey One', href: 'https://fonts.google.com/specimen/Jockey+One', note: 'The large hero heading.', licence: 'OFL 1.1' },
      { name: 'Caveat', href: 'https://fonts.google.com/specimen/Caveat', note: 'Handwriting in the sketchbook and the PDF.', licence: 'OFL 1.1' },
      { name: 'Yomogi', href: 'https://fonts.google.com/specimen/Yomogi', note: 'Handwriting for Japanese names in the sketchbook and the PDF.', licence: 'OFL 1.1' },
    ],
  },
  {
    id: 'code', title: 'Code and AI models',
    intro: 'The libraries you can see at work. Smaller packages are used under their own licences, listed in the project\'s package files.',
    rows: [
      { name: 'React', href: 'https://react.dev/', note: 'The interface.', licence: 'MIT' },
      { name: 'GSAP', href: 'https://gsap.com/', note: 'Motion and page transitions.', licence: 'GSAP standard "no charge" licence' },
      { name: 'Rough.js', href: 'https://roughjs.com/', note: 'Hand-drawn shapes in the sketchbook.', licence: 'MIT' },
      { name: 'perfect-freehand', href: 'https://github.com/steveruizok/perfect-freehand', note: 'Pen-style highlights and underlines.', licence: 'MIT' },
      { name: 'three.js and React Three Fiber', href: 'https://threejs.org/', note: 'The 3D route view.', licence: 'MIT' },
      { name: 'MapLibre GL JS', href: 'https://maplibre.org/', note: 'The everyday map.', licence: 'BSD-3-Clause' },
      { name: 'Framer Motion', href: 'https://www.framer.com/motion/', note: 'Interface animation.', licence: 'MIT' },
      { name: 'Lenis', href: 'https://lenis.darkroom.engineering/', note: 'Smooth scrolling.', licence: 'MIT' },
      { name: 'jsPDF', href: 'https://github.com/parallax/jsPDF', note: 'Builds the trip PDF.', licence: 'MIT' },
      { name: 'svg2pdf.js', href: 'https://github.com/yWorks/svg2pdf.js', note: 'Turns the sketchbook pages into vector PDF pages.', licence: 'MIT' },
      { name: 'LangGraph', href: 'https://www.langchain.com/langgraph', note: 'Runs the trip planning agent.', licence: 'MIT' },
      { name: 'FastAPI', href: 'https://fastapi.tiangolo.com/', note: 'The server behind the planner.', licence: 'MIT' },
      { name: 'Groq', href: 'https://groq.com/', note: 'Language models for chat and itineraries.', licence: 'Provider terms' },
      { name: 'Google Gemini', href: 'https://ai.google.dev/', note: 'Language models for chat and itineraries.', licence: 'Provider terms' },
    ],
  },
  {
    id: 'art', title: 'Drawings and characters',
    intro: 'What we drew ourselves, and what we did not.',
    rows: [
      { name: 'Sketchbook doodles', note: 'The small icons on sketchbook pages, drawn for Pathfinder.', licence: 'CC0' },
      { name: 'Pathfinder mark and icons', note: 'The logo and the interface icon set, drawn for Pathfinder.', licence: 'Original work' },
      {
        name: 'Guide marks',
        note: 'The six guide emblems — tetrahedron, cube, octahedron, dodecahedron, icosahedron and sphere — drawn for Pathfinder as wireframe plates and shipped as SVG in public/guides.',
        licence: 'Original work',
      },
    ],
  },
];
