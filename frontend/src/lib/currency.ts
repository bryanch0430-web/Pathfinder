/** Currency choices shown in the trip form and the exchange widget. */
import type { FxSnapshot } from "../design-system/types";

export const DEFAULT_CURRENCIES = [
  "HKD", "TWD", "JPY", "USD", "EUR", "GBP", "CNY", "SGD", "THB", "KRW",
  "AUD", "CAD", "NZD", "CHF", "INR", "IDR", "MYR", "PHP", "VND", "AED",
  "SAR", "QAR", "TRY", "ZAR", "EGP", "MXN", "BRL", "CZK", "PLN", "SEK",
  "NOK", "DKK", "ILS",
];

const LABELS: Record<string, string> = {
  AED: "UAE dirham", AUD: "Australian dollar", BRL: "Brazilian real", CAD: "Canadian dollar",
  CHF: "Swiss franc", CNY: "Chinese yuan", CZK: "Czech koruna", DKK: "Danish krone",
  EGP: "Egyptian pound", EUR: "Euro", GBP: "British pound", HKD: "Hong Kong dollar",
  IDR: "Indonesian rupiah", ILS: "Israeli shekel", INR: "Indian rupee", JPY: "Japanese yen",
  KRW: "South Korean won", MXN: "Mexican peso", MYR: "Malaysian ringgit", NOK: "Norwegian krone",
  NZD: "New Zealand dollar", PHP: "Philippine peso", PLN: "Polish zloty", QAR: "Qatari riyal",
  SAR: "Saudi riyal", SEK: "Swedish krona", SGD: "Singapore dollar", THB: "Thai baht",
  TRY: "Turkish lira", TWD: "New Taiwan dollar", USD: "US dollar", VND: "Vietnamese dong",
  ZAR: "South African rand",
};

export function currencyLabel(code: string): string {
  return LABELS[code.toUpperCase()] ?? "";
}

/** All codes the FX snapshot knows, plus the current one, with a stable fallback list. */
export function currencyCodes(fx?: FxSnapshot | null, current = ""): string[] {
  const fromRates = Object.keys(fx?.rates ?? {}).filter((code) => /^[A-Z]{3}$/.test(code));
  const extra = current.trim().toUpperCase();
  return [...new Set([...fromRates, ...(extra ? [extra] : []), ...DEFAULT_CURRENCIES])].sort();
}

/** A datalist renders this as the searchable label while the input stores the code. */
export function currencyOptionLabel(code: string): string {
  const label = currencyLabel(code);
  return label ? `${code} — ${label}` : code;
}
