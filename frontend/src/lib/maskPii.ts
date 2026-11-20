/** The same labels the server puts in front of a model. Display only. */

const EMAIL = /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g;
const PHONE = /\+?\d[\d\s\-()]{6,}\d/g;
const CARD = /\b\d[\d -]{11,21}\d\b/g;
const HKID = /(?<![A-Z0-9])[A-Z]{1,2}(?:\s*\d){6,7}(?!\d)(?:\s*\(?\s*[0-9A]\s*\)?)?/gi;

function luhn(digits: string): boolean {
  if (digits.length < 13 || digits.length > 19) return false;
  let total = 0;
  for (let index = 0; index < digits.length; index += 1) {
    let value = Number(digits[digits.length - 1 - index]);
    if (index % 2 === 1) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  }
  return total % 10 === 0;
}

function isHkPhone(raw: string): boolean {
  if (/^\d{4}[-\s]\d{1,2}[-\s]\d{1,2}$/.test(raw.trim())) return false;
  const digits = raw.replace(/\D/g, '');
  let local = '';
  if (digits.startsWith('00852')) local = digits.slice(5);
  else if (digits.startsWith('852') && digits.length > 8) local = digits.slice(3);
  else if (digits.length === 8) local = digits;
  else return false;
  return local.length === 8 && '23456789'.includes(local[0]);
}

/** Hide personal details in a traveller's own bubble. */
export function maskForDisplay(text: string): string {
  if (!text) return text;
  return text
    .replace(EMAIL, '[EMAIL]')
    .replace(CARD, (match) => {
      const digits = match.replace(/\D/g, '');
      return luhn(digits) ? `[CARD ****${digits.slice(-4)}]` : match;
    })
    .replace(PHONE, (match) => (isHkPhone(match) ? '[PHONE]' : match))
    .replace(HKID, '[HKID]');
}
