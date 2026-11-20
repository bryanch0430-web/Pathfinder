/*
 * The guide cast: six companions, one per Platonic solid.
 *
 * Each guide is the same wireframe plate of its solid — edges where a face turns
 * away from the viewer are dashed, as in a geometry textbook. The marks are ours
 * (see lib/credits.ts), so any surface can render them without licensing baggage.
 * Keep every guide reference going through this file.
 */
export interface Guide {
  id: string;
  name: string;
  image: string;
  line: string;
}

export const GUIDES: Guide[] = [
  {
    id: 'tetra', name: 'Tetra-san', image: '/guides/tetra.svg',
    line: 'Cool-headed planner. Quiet corners, zero wasted steps.',
  },
  {
    id: 'hexa', name: 'Hexa-san', image: '/guides/hexa.svg',
    line: 'Wide-eyed explorer who has seen centuries of sights.',
  },
  {
    id: 'octa', name: 'Octa-san', image: '/guides/octa.svg',
    line: 'Tiny critic with big opinions on where to eat.',
  },
  {
    id: 'dode', name: 'Dode-san', image: '/guides/dode.svg',
    line: 'Always has a plan. Budget in check, no surprises.',
  },
  {
    id: 'icosa', name: 'Icosa-san', image: '/guides/icosa.svg',
    line: 'Shy, but knows every hidden gem nobody posts about.',
  },
  {
    id: 'sphere', name: 'Sphere-san', image: '/guides/sphere.svg',
    line: 'Night owl. Late bites, city lights, slow mornings.',
  },
];

export const DEFAULT_GUIDE = GUIDES[0];

export const guideById = (id?: string | null): Guide => GUIDES.find((guide) => guide.id === id) || DEFAULT_GUIDE;

/** A stable guide for anyone with no picture of their own: same seed, same guide, nothing stored. */
export const guideForSeed = (seed: string): Guide => {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return GUIDES[h % GUIDES.length];
};

const PICK_KEY = 'tripverse-companion-guide';

/** The guide riding along on every page (the floating companion, the hero cast). */
export const companionGuide = (): Guide => {
  try { return guideById(localStorage.getItem(PICK_KEY)); } catch { return DEFAULT_GUIDE; }
};
export const setCompanionGuide = (id: string) => {
  try { localStorage.setItem(PICK_KEY, id); } catch { /* storage blocked: lasts until reload */ }
  window.dispatchEvent(new CustomEvent('tripverse-companion-guide', { detail: id }));
};
