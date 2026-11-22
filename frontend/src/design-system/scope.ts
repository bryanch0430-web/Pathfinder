import type { HotelStay, PlanItem } from "./types";

/**
 * The hotel is a plan record, not a day item, but the chat scope, the map
 * selection and the place card all work on `PlanItem`. This adapter gives the
 * stay the same shape so "About: The Salisbury" behaves like any other stop.
 */
export function hotelScopeItem(hotel: HotelStay): PlanItem {
  return {
    id: hotel.id,
    type: "hotel",
    name: hotel.name,
    start: hotel.check_in.slice(5),
    end: hotel.check_out.slice(5),
    geo: hotel.geo,
    address: hotel.area,
    notes: hotel.notes,
    source_refs: hotel.source_refs,
    data_status: hotel.data_status,
    price: hotel.price_per_night,
    price_kind: hotel.price_kind,
    booking_url: hotel.booking_url,
    locked: hotel.locked,
    confirmed: hotel.confirmed,
    tags: ["stay"],
  };
}

/** True when the selected scope id belongs to a stay rather than a day item. */
export function isHotelScope(id: string | null, hotels: HotelStay[]): boolean {
  return Boolean(id) && hotels.some((hotel) => hotel.id === id);
}
