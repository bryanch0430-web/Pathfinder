/** Chat masking and the settings preview. A preview sample is not stored. */

export type RedactionFlags = {
  hkid: boolean;
  hk_phone: boolean;
  email: boolean;
  card: boolean;
  patterns: {
    hkid: string;
    hk_phone: string;
    email: string;
    card: string;
  };
};

export const DEFAULT_PATTERNS: RedactionFlags["patterns"] = {
  hkid: "(?<![A-Z0-9])[A-Z]{1,2}(?:\\s*\\d){6}(?!\\d)(?:\\s*\\(?\\s*[0-9A]\\s*\\)?)?",
  hk_phone: "\\+?\\d[\\d\\s\\-()]{6,}\\d",
  email: "\\b[\\w.+-]+@[\\w-]+\\.[\\w.-]+\\b",
  card: "\\b\\d[\\d -]{11,21}\\d\\b",
};

const DEFAULTS: RedactionFlags = {
  hkid: true,
  hk_phone: true,
  email: true,
  card: true,
  patterns: { ...DEFAULT_PATTERNS },
};
let flags: RedactionFlags = {
  hkid: true,
  hk_phone: true,
  email: true,
  card: true,
  patterns: { ...DEFAULT_PATTERNS },
};

const DATE = /^\d{4}[-\s]\d{1,2}[-\s]\d{1,2}$/;

export function setRedaction(next: Partial<RedactionFlags> | null | undefined): void {
  flags = {
    ...DEFAULTS,
    ...(next ?? {}),
    patterns: { ...DEFAULT_PATTERNS, ...(next?.patterns ?? {}) },
  };
}

export function redactionFlags(): RedactionFlags {
  return { ...flags };
}

function luhn(digits: string): boolean {
  if (digits.length < 13 || digits.length > 19) return false;
  let total = 0;
  const reverse = digits.split("").reverse();
  reverse.forEach((char, index) => {
    let value = Number(char);
    if (index % 2 === 1) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  });
  return total % 10 === 0;
}

function isHkPhone(raw: string): boolean {
  if (DATE.test(raw.trim())) return false;
  const digits = raw.replace(/\D/g, "");
  let local = digits;
  if (digits.startsWith("00852")) local = digits.slice(5);
  else if (digits.startsWith("852") && digits.length > 8) local = digits.slice(3);
  else if (digits.length !== 8) return false;
  return local.length === 8 && "23456789".includes(local[0] ?? "");
}

function compile(source: string | undefined, fallback: string): RegExp {
  try {
    return new RegExp((source ?? "").trim() || fallback, "g");
  } catch {
    return new RegExp(fallback, "g");
  }
}

function apply(text: string, chosen: RedactionFlags, mark: (kind: string, raw: string, digits: string) => string): string {
  let next = text;
  const patterns = chosen.patterns ?? DEFAULT_PATTERNS;
  if (chosen.email) next = next.replace(compile(patterns.email, DEFAULT_PATTERNS.email), (raw) => mark("email", raw, raw));
  if (chosen.card) {
    next = next.replace(compile(patterns.card, DEFAULT_PATTERNS.card), (raw) => {
      const digits = raw.replace(/\D/g, "");
      return luhn(digits) ? mark("card", raw, digits) : raw;
    });
  }
  if (chosen.hk_phone) {
    next = next.replace(compile(patterns.hk_phone, DEFAULT_PATTERNS.hk_phone), (raw) => (
      isHkPhone(raw) ? mark("phone", raw, raw.replace(/\D/g, "")) : raw
    ));
  }
  if (chosen.hkid) next = next.replace(compile(patterns.hkid, DEFAULT_PATTERNS.hkid), (raw) => mark("hkid", raw, raw));
  return next;
}

export function maskTyped(text: string, chosen: RedactionFlags = flags): string {
  if (!text) return text;
  return apply(text, chosen, (kind, _raw, digits) => {
    if (kind === "email") return "[EMAIL]";
    if (kind === "phone") return "[PHONE]";
    if (kind === "card") return `[CARD ****${digits.slice(-4)}]`;
    return "[HKID]";
  });
}
