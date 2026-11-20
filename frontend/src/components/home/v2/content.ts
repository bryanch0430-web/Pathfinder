/*
 * Shared content for the v2 marketing surface.
 *
 * Every string here is specific and contextual — real places, real-shaped
 * numbers, plain language. No placeholder names, no round marketing figures,
 * no "seamless"/"elevate" copy.
 */

/*
 * Photography resolves (alias @media) to `src/assets/photos/`, the committed subset of `media/web/`:
 * 1800px-wide, quality-82 derivatives
 * of the originals (72.8 MB -> 12.0 MB across the set). The full-resolution
 * sources stay in `media/` for reprocessing.
 */
import fuji from '@media/web/hero-bg.jpg';
import fujiCutout from '@media/hero-bg-transparent.png';
import pagoda from '@media/web/3rd.jpg';
import parasail from '@media/web/1st.jpg';
import cityPlan from '@media/web/4th.jpg';
import sydney from '@media/web/caleb-JmuyB_LibRo-unsplash.jpg';
import dubaiTower from '@media/web/christoph-schulz-7tb-b37yHx4-unsplash.jpg';
import dubaiMarina from '@media/web/kate-trysh-U3CntDq16yY-unsplash.jpg';
import prague from '@media/web/fredy-martinez-frd7WNzipdU-unsplash.jpg';
import budapest from '@media/web/philipp-trubchenko-oOTo9nR7f9Q-unsplash.jpg';
import porto from '@media/web/will-goodman-1EikowqH9fs-unsplash.jpg';
import newYork from '@media/web/pierre-blache-VMNG8BYFQfs-unsplash.jpg';
import luxembourg from '@media/web/pedro-lastra-5g8dJvtYRYA-unsplash.jpg';
import alpineLake from '@media/web/datingscout-RlQ29vvbU2Q-unsplash.jpg';
import kyotoGarden from '@media/web/david-emrich-VCM99u6HltA-unsplash.jpg';
import seoulPalace from '@media/web/brady-bellini-t5dGNNQVwg8-unsplash.jpg';
import tokyoAlley from '@media/web/matthieu-buhler-PaFHv0Zi71E-unsplash.jpg';
import izakaya from '@media/web/pema-g-lama-6cfK0SEtpbY-unsplash.jpg';
import osakaStreet from '@media/web/shigeki-wakabayashi-6nuz52vsbWc-unsplash.jpg';
import blossomFuji from '@media/web/jj-ying-9Qwbfa_RM94-unsplash.jpg';
import monoCity from '@media/web/james-wilkinson-FMuorhl0EHY-unsplash.jpg';
import contrail from '@media/web/ben-klewais-nLE3eLaQA6A-unsplash.jpg';
import poolVilla from '@media/web/bilderboken-rlwE8f8anOc-unsplash.jpg';
import hotelRoom from '@media/web/vojtech-bruzek-Yrxr3bsPdS0-unsplash.jpg';
import dinner from '@media/web/jay-wennington-N_Y88TWmGwA-unsplash.jpg';
import platedFood from '@media/web/alexandru-bogdan-ghita-UeYkqQh4PoI-unsplash.jpg';
import temple from '@media/web/mountain-girl-WfT3o1KhnwQ-unsplash.jpg';

export const img = {
  fuji,
  fujiCutout,
  pagoda,
  parasail,
  cityPlan,
  sydney,
  dubaiTower,
  dubaiMarina,
  prague,
  budapest,
  porto,
  newYork,
  luxembourg,
  alpineLake,
  kyotoGarden,
  seoulPalace,
  tokyoAlley,
  izakaya,
  osakaStreet,
  blossomFuji,
  monoCity,
  contrail,
  poolVilla,
  hotelRoom,
  dinner,
  platedFood,
  temple,
};

export interface Destination {
  city: string;
  country: string;
  code: string;
  image: string;
  nights: number;
  note: string;
}

export const DESTINATIONS: Destination[] = [
  { city: 'Kyoto', country: 'Japan', code: 'UKY', image: kyotoGarden, nights: 4, note: 'Temple districts, kaiseki, autumn maples' },
  { city: 'Porto', country: 'Portugal', code: 'OPO', image: porto, nights: 3, note: 'Douro riverfront, tiled facades, port cellars' },
  { city: 'Prague', country: 'Czechia', code: 'PRG', image: prague, nights: 3, note: 'Old Town rooftops, Vltava crossings' },
  { city: 'Seoul', country: 'South Korea', code: 'ICN', image: seoulPalace, nights: 5, note: 'Palace grounds, night markets, Bukchon' },
  { city: 'Budapest', country: 'Hungary', code: 'BUD', image: budapest, nights: 3, note: 'Thermal baths, Buda hill, ruin bars' },
  { city: 'Sydney', country: 'Australia', code: 'SYD', image: sydney, nights: 6, note: 'Harbour walks, coastal track, ferries' },
];

/*
 * Everything below is one real trip, not a mock-up: the 3-day Kyoto plan made while writing the guide
 * (`frontend/scripts/capture_guide.py`, screenshots in /guide). The message, the opening of the reply, the days,
 * the public holiday and the suggested budget are what the app produced. Re-check them after a re-shoot.
 */
export const AGENT_TRANSCRIPT = [
  { role: 'user' as const, text: 'Plan 3 days in Kyoto from Delhi. I love food and old temples.' },
  { role: 'agent' as const, text: 'I’ve put together a balanced, three-day itinerary for your trip to Kyoto. Since you’re traveling in mid-October, you’ll be there during the pleasant autumn season, though October is part of the rainy season, so I’ve built in some flexibility.' },
];

/** The stages the planner reports while it drafts (its own labels), with this trip's details. */
export const AGENT_STEPS = [
  { label: 'Read your brief', detail: '3 days · 2 travelers · food, temples', state: 'done' as const },
  { label: 'Researched possible stops', detail: 'Kyoto, from Delhi', state: 'done' as const },
  { label: 'Checked your dates', detail: 'Typical for October · Sports Day on day 1', state: 'done' as const },
  { label: 'Writing your day-by-day draft', detail: 'Day 2 of 3', state: 'active' as const },
  { label: 'Drawing the sketchbook', detail: 'Queued', state: 'idle' as const },
];

export const ITINERARY_ROWS = [
  { day: 'D1', place: 'Fushimi Inari Taisha, Takashimaya food hall', mode: 'Mon 12 Oct, holiday', cost: '2' },
  { day: 'D2', place: 'Arashiyama Bamboo Grove, Kiyomizu-dera, Nishiki Market', mode: 'Tue 13 Oct', cost: '8' },
  { day: 'D3', place: 'Okochi-sanso Villa, Nishijin, Uji', mode: 'Wed 14 Oct', cost: '4' },
];

/** The budget planner's suggested amounts for that trip (estimates from traveler reports, not quotes). */
export const BUDGET_ROWS = [
  { label: 'Flights, Delhi and back', amount: '~₹60,000' },
  { label: 'Stay, 2 nights', amount: '~₹22,000' },
  { label: 'Activities', amount: '~₹8,420' },
  { label: 'Food, 3 days', amount: '~₹4,500' },
  { label: 'Local travel', amount: '~₹3,600' },
];

/*
 * Each panel shows the app itself: crops of the guide's screenshots (public/home, made by
 * `frontend/scripts/home_crops.py` from public/guide). Re-run that script after a re-shoot.
 */
export const CAPABILITIES = [
  {
    n: '01',
    title: 'Ask for a change and it happens',
    body: 'Say “move Nishiki Market to day 2” and the plan changes, the studio follows, and a receipt under the reply lists exactly what changed. If a day gets too full or the budget is passed, it tells you instead of refusing.',
    icon: 'graph' as const,
    image: '/home/cap-changes.png',
    alt: 'A chat with two receipts: Moved Nishiki Market from day 1 to day 2, then Budget target set to 150,000 INR',
    caption: 'Every change leaves a receipt',
  },
  {
    n: '02',
    title: 'A guide who draws your trip',
    body: 'Pick a guide and they sketch each day on paper as the plan takes shape: the places, the order, the weather, the public holiday. Change something in the chat and the page is redrawn while you watch.',
    icon: 'spark' as const,
    image: '/home/cap-sketch.png',
    alt: 'A hand-drawn sketchbook page for one day of the trip, with its places, weather and a holiday flag',
    caption: 'Drawn live, page by page',
  },
  {
    n: '03',
    title: 'The itinerary is a map, not a list',
    body: 'Every stop sits on a real map and in a 3D route you can orbit, with photos, what is nearby, and the forecast or the season’s typical weather for your dates.',
    icon: 'layers' as const,
    image: '/home/cap-map.png',
    alt: 'The Trip Studio map tab with the route into Kyoto and a weather heads-up for October',
    caption: 'Map and 3D in one studio',
  },
  {
    n: '04',
    title: 'A budget you can see, not guess',
    body: 'Set a target in the brief, or just say it. The budget planner suggests typical costs from traveler reports, row by row, and nothing counts toward your total until you accept it.',
    icon: 'wallet' as const,
    image: '/home/cap-budget.png',
    alt: 'The budget planner with a projected trip cost of about 98,520 rupees against a 150,000 target and suggested amounts per row',
    caption: 'Suggested first, then yours to accept',
  },
];

/** Photography for the infinite marquee strip. */
export const MARQUEE = [
  { src: kyotoGarden, label: 'Kyoto' },
  { src: porto, label: 'Porto' },
  { src: prague, label: 'Prague' },
  { src: seoulPalace, label: 'Seoul' },
  { src: budapest, label: 'Budapest' },
  { src: sydney, label: 'Sydney' },
  { src: tokyoAlley, label: 'Tokyo' },
  { src: luxembourg, label: 'Luxembourg' },
  { src: osakaStreet, label: 'Osaka' },
  { src: newYork, label: 'New York' },
  { src: dubaiMarina, label: 'Dubai' },
  { src: blossomFuji, label: 'Fujiyoshida' },
];

/** Pathfinder artwork with a live screenshot of each planning step. */
export const STEP_IMAGES = [
  '/home/how-it-works/destination.png',
  '/home/how-it-works/build-together.png',
  '/home/how-it-works/trip-studio.png',
  '/home/how-it-works/take-with-you.png',
];

export const STEPS = [
  {
    n: '01',
    title: 'Say where you are going',
    body: 'Tell your guide the place, or just the mood. It asks three quick things (where from, where to, how many days). Dates, budget, pace and interests are optional.',
  },
  {
    n: '02',
    title: 'Get a draft, or build it together',
    body: 'Generate the whole itinerary in one go, or plan one day at a time while your guide suggests places that fit the day and the budget.',
  },
  {
    n: '03',
    title: 'Open the Trip Studio',
    body: 'One page for the whole trip: the day plan, a hand-drawn sketchbook, a map, a 3D route, and the weather and public holidays for your dates.',
  },
  {
    n: '04',
    title: 'Change it by asking, then take it with you',
    body: 'Say “move the market to day 1” and it is done, with a receipt of what changed. Export a PDF, a calendar, map files or the budget as a spreadsheet.',
  },
];

export const FAQ = [
  {
    q: 'What makes this different from asking a chatbot for an itinerary?',
    a: 'A chatbot hands you a wall of text. Pathfinder keeps the trip as a day-by-day plan it can change: ask it to move or add a place and the plan, the map and the sketchbook all update, with a receipt of what changed. It also brings in the forecast, public holidays and a budget for your dates.',
  },
  {
    q: 'Can I edit the plan after it is generated?',
    a: 'Yes, and editing is the point. Say what you want in the same chat (“move Nishiki Market to day 1”, “make day 2 lighter”) and it is applied and saved straight away. When your guide suggests a change instead, a plain “yes” applies it.',
  },
  {
    q: 'Does it book anything on my behalf?',
    a: 'No. Pathfinder plans and organises the trip; you book the flights, stays and tickets yourself. Costs in the budget planner are estimates from traveler reports until you enter your own.',
  },
  {
    q: 'What happens to a trip I started but did not finish?',
    a: 'It stays in your trip library with its conversation, plan and budget, so you can reopen it and carry on. You can also take it with you: a PDF with the sketch pages, a calendar file, GPX and KML for map apps, and the budget as a spreadsheet.',
  },
  {
    q: 'Do I need an account to try it?',
    a: 'No. You can plan as a guest and use everything, including the studio and the exports. Sign in to keep your trips and open them on another device.',
  },
];

export const FOOTER_NAV = [
  {
    heading: 'Product',
    links: [
      { label: 'How it works', href: '#how-it-works' },
      { label: 'Explore destinations', href: '/explore', key: 'explore' },
      { label: 'Plan a trip', href: '#plan', key: 'plan' },
      { label: 'Destinations', href: '#destinations' },
    ],
  },
  {
    heading: 'Your trips',
    links: [
      { label: 'My trips', href: '/trips' },
      { label: 'Sign in', href: '/login' },
      { label: 'Create an account', href: '/signup' },
    ],
  },
  {
    heading: 'Help',
    links: [
      { label: 'How to use Pathfinder', href: '/guide' },
      { label: 'What the agent does', href: '#capabilities' },
      { label: 'Questions', href: '#questions' },
    ],
  },
];
