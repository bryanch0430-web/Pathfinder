import type { TripDocument } from '../../services/tripService';

export const tripTitle = (doc: TripDocument) =>
  `${doc.destination || 'Trip'}${doc.duration_days ? ` · ${doc.duration_days} ${doc.duration_days === 1 ? 'day' : 'days'}` : ''}`;

/** "kyoto-3-days" style base name for every exported file. */
export const fileBase = (doc: TripDocument) =>
  tripTitle(doc).normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'trip';

/** An object URL for a finished export. Whoever holds it revokes it; the Export menu keeps it for its "Save" link. */
export const fileUrl = (content: string | Blob, type: string) =>
  URL.createObjectURL(typeof content === 'string' ? new Blob([content], { type }) : content);

/**
 * Save a file straight away (no dialog, no new tab), but only while the click that asked for it still counts.
 * About five seconds after a click the browser stops treating what follows as the user's doing, and a download
 * started then is an "automatic" one it may block without a word. That is how the instant JSON export arrived
 * while the slower ones (a first place lookup, a cold budget fetch) did not. Returns whether it saved; when it
 * didn't, the caller shows a link to the same URL, and clicking that is a fresh click.
 */
export function download(filename: string, url: string): boolean {
  if (navigator.userActivation && !navigator.userActivation.isActive) return false;
  const link = Object.assign(document.createElement('a'), { href: url, download: filename, rel: 'noopener' });
  document.body.append(link);
  link.click();
  link.remove();
  return true;
}
