/** The planner's own "Lunch candidate from google-places" line. It is not a traveler note. */
export function isPendingChoice(notes: string | null | undefined): boolean {
  return !!notes && /\bcandidate from\b/i.test(notes);
}
