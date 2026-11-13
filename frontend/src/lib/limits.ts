/**
 * What the server keeps of a trip's details. A request over these is refused with 422, so the form stops at them
 * (a test in backend/tests keeps the numbers in step with TRIP_LINES_MAX, TRIP_LINE_MAX_LENGTH and
 * TRIP_FIELD_MAX_LENGTH in backend/app/domain/models.py).
 */

/** Most preferences, and most hard constraints, of one trip. */
export const TRIP_LINES_MAX = 50;
/** Most cities one trip can visit. */
export const TRIP_STOPS_MAX = 8;
/** Longest preference or constraint, in characters. */
export const TRIP_LINE_MAX_LENGTH = 200;
/** Longest destination, departure city or other text of the form, in characters. */
export const TRIP_FIELD_MAX_LENGTH = 120;
