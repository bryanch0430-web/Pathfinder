/**
 * Interface language: EN, Simplified Chinese (简) and Traditional Chinese (繁).
 *
 * `I18nProvider` holds the chosen locale and persists it under
 * `pathfinder.locale`. Any component, however deep in the design system, reads
 * the locale and the `t()` translator with `useI18n()`; there is no prop
 * drilling. Without a provider (Storybook, a test) the context falls back to
 * English, so every component still renders on its own.
 *
 * Only static interface text is translated. AI-generated content, place names
 * and the traveller's own data are shown as they came.
 */
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { EN, type MessageKey, type Messages } from "./locales/en";
import { ZH_HANS } from "./locales/zh-Hans";
import { ZH_HANT } from "./locales/zh-Hant";
import { storageKey } from "./storage";

export type Locale = "en" | "zh-Hans" | "zh-Hant";
export type { MessageKey };

/** Values for `{name}` placeholders in a message. */
export type MessageParams = Record<string, string | number>;
export type Translate = (key: MessageKey, params?: MessageParams) => string;

const DICTIONARIES: Record<Locale, Messages> = {
  en: EN,
  "zh-Hans": ZH_HANS,
  "zh-Hant": ZH_HANT,
};

const KEY = storageKey("locale");

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && value in DICTIONARIES;
}

/** A translator for one locale; exported for code that runs outside React. */
export function translator(locale: Locale): Translate {
  const dictionary = DICTIONARIES[locale];
  return (key, params) => {
    const template = dictionary[key] ?? EN[key] ?? key;
    if (!params) return template;
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
      name in params ? String(params[name]) : match,
    );
  };
}

function storedLocale(): Locale {
  try {
    const stored = localStorage.getItem(KEY);
    return isLocale(stored) ? stored : "en";
  } catch {
    return "en";
  }
}

export interface I18nValue {
  locale: Locale;
  setLocale: (value: string) => void;
  t: Translate;
}

const I18nContext = createContext<I18nValue>({
  locale: "en",
  setLocale: () => undefined,
  t: translator("en"),
});

export function I18nProvider({ children, locale: fixed }: { children: ReactNode; locale?: Locale }) {
  const [locale, setLocaleState] = useState<Locale>(() => fixed ?? storedLocale());
  useEffect(() => {
    if (fixed) setLocaleState(fixed);
  }, [fixed]);
  useEffect(() => {
    if (!fixed) {
      try {
        localStorage.setItem(KEY, locale);
      } catch {
        // Storage blocked: the choice lasts for this page only.
      }
    }
    document.documentElement.lang = locale;
  }, [locale, fixed]);
  const setLocale = useCallback((value: string) => {
    if (isLocale(value)) setLocaleState(value);
  }, []);
  const value = useMemo<I18nValue>(() => ({ locale, setLocale, t: translator(locale) }), [locale, setLocale]);
  return createElement(I18nContext.Provider, { value }, children);
}

/** The current locale and its translator; English when no provider is mounted. */
export function useI18n(): I18nValue {
  return useContext(I18nContext);
}
