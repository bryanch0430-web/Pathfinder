/**
 * Plan display language.
 *
 * The English plan is the single source of truth. A translation only replaces
 * prose (summary, day titles and notes, item notes, gap messages); numbers,
 * times, prices, ids, URLs and coordinates always come from
 * the English plan. Place names follow the trusted `names` from the sources and
 * are never taken from a model.
 */
import type { Locale } from "./i18n";
import type { PlanLocale, PlanTranslation, TripPlan } from "../design-system/types";

/** The plan locale a UI language implies: 繁 shows the plan in Traditional Chinese; EN and 简 keep it English. */
export function planLocaleFor(locale: Locale): PlanLocale {
  return locale === "zh-Hant" ? "zh-Hant" : "en";
}

export function isPlanLocale(value: unknown): value is PlanLocale {
  return value === "en" || value === "zh-Hant";
}

/**
 * The name to show for a stop, hotel or restaurant.
 * en → the trusted English name, else the original;
 * zh-Hant → the trusted Chinese name followed by the original, else the original.
 * Mirrors `display_name` in backend/app/domain/place_names.py exactly.
 */
export function displayName(entity: { name: string; names?: Record<string, string> }, locale: PlanLocale): string {
  const names = entity.names ?? {};
  const { name } = entity;
  if (locale === "zh-Hant") {
    const chinese = names["zh-Hant"] ?? "";
    if (chinese && chinese !== name) return `${chinese} (${name})`;
    return chinese || name;
  }
  return names.en || name;
}

/** Keep a translated string only when it has text; an empty field keeps the English. */
function pick(translated: string | undefined, english: string): string {
  return translated && translated.trim() ? translated : english;
}

/** True when `translation` is a checked translation of exactly this plan version. */
export function translationApplies(plan: TripPlan, locale: PlanLocale, translation: PlanTranslation | null): boolean {
  return (
    locale !== "en" &&
    translation !== null &&
    translation.status === "translated" &&
    translation.locale === locale &&
    translation.plan_version === plan.version
  );
}

/**
 * The plan as shown: display names applied, and the translated prose laid over
 * the English plan when a translation of this exact version is in hand.
 */
export function localizePlan(plan: TripPlan, locale: PlanLocale, translation: PlanTranslation | null): TripPlan {
  const fields = translationApplies(plan, locale, translation) && translation ? translation.fields : {};
  const items = fields.items ?? {};
  const days = fields.days ?? {};
  const gaps = fields.gaps ?? {};
  return {
    ...plan,
    summary: pick(fields.summary, plan.summary),
    days: plan.days.map((day) => ({
      ...day,
      title: pick(days[day.date]?.title, day.title),
      note: pick(days[day.date]?.note, day.note),
      items: day.items.map((item) => ({
        ...item,
        name: displayName(item, locale),
        notes: pick(items[item.id]?.notes, item.notes),
      })),
    })),
    hotels: plan.hotels.map((hotel) => ({
      ...hotel,
      name: displayName(hotel, locale),
      notes: pick(items[hotel.id]?.notes, hotel.notes),
    })),
    data_quality: {
      ...plan.data_quality,
      gaps: plan.data_quality.gaps.map((gap, index) => ({ ...gap, message: pick(gaps[String(index)], gap.message) })),
    },
  };
}

/** Why the English plan is shown while the view is set to another language; null when nothing needs saying. */
export type PlanLanguageNotice = "translating" | "fallback" | "unavailable" | "failed" | null;

export function planLanguageNotice(
  locale: PlanLocale,
  translation: PlanTranslation | null,
  loading: boolean,
  failed: boolean,
): PlanLanguageNotice {
  if (locale === "en") return null;
  if (loading) return "translating";
  if (failed) return "failed";
  if (!translation) return null;
  if (translation.status === "fallback") return "fallback";
  if (translation.status === "unavailable") return "unavailable";
  return null;
}
