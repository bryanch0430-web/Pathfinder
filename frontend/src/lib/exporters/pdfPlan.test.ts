import { describe, expect, it } from 'vitest';
import { base, tokyo } from '../../components/sketch/fixtures';
import { fontFor } from './pdfFonts';
import { planRows, sourceName } from './pdfPlan';

describe('planRows', () => {
  const rows = planRows(tokyo);
  const texts = rows.map((row) => row.text);

  it('groups each day by time of day, with tips and linked sources', () => {
    const day1 = texts.slice(texts.indexOf('Day 1 · Mon 12 Oct · Tokyo'), texts.indexOf('Day 2 · Tue 13 Oct · Tokyo'));
    expect(day1).toContain('Weather · forecast · 16–21°C');
    expect(day1).toContain('Public holiday: Sports Day (スポーツの日)');
    expect(day1.filter((text) => ['Morning', 'Afternoon', 'Evening'].includes(text))).toEqual(['Morning', 'Afternoon', 'Evening']);
    expect(day1).toContain('Senso-ji — Asakusa · 1.5 h');
    expect(rows.find((row) => row.text === 'Source: reddit')?.link).toBe('https://www.reddit.com/r/JapanTravel/x');
  });

  it('puts untimed items under "Any time" and notes free days', () => {
    const day3 = texts.slice(texts.indexOf('Day 3 · Wed 14 Oct · Kyoto'), texts.indexOf('Day 4 · Thu 15 Oct · Kyoto'));
    expect(day3).toContain('Any time');
    expect(day3).toContain('Pontocho dinner — optional');
    expect(texts).toContain('A free day: nothing planned yet.');
  });

  it('totals the budget and flags going over', () => {
    expect(texts).toContain('Total: ¥131,000 (over by ¥11,000)');
    expect(texts).toContain('Day 1 · Tokyo: ¥14,000');
  });

  it('prints a locked budget as one final total', () => {
    const final = planRows(base({ budget: { currency: 'JPY', target: 10000, planned: 9000,
      entered: 9000, projected: 9000, is_locked: true, locked_total: 9000 } }));
    const labels = final.map((row) => row.text);
    expect(labels).toContain('Final budget');
    expect(labels).toContain('Final total: ¥9,000');
    expect(labels).not.toContain('Spent so far: ¥9,000');
  });

  it('marks the confirmed itinerary as final in the complete PDF', () => {
    const labels = planRows(base({ is_final: true })).map((row) => row.text);
    expect(labels.slice(0, 3)).toEqual(['Final trip plan', 'Confirmed itinerary · read-only snapshot', 'Day by day']);
  });

  it('includes travel time and a flagged day in the PDF', () => {
    const draft = base({ days: [{ ...tokyo.days[0], travel_hours: 4.2,
      travel_distance_km: 78.3, travel_basis: 'distance estimate', unverified_legs: 0 }],
    route_warnings: [{ day: 1, severity: 'tight', message: 'About 10 h exceeds this day',
      travel_hours: 4.2, distance_km: 78.3, basis: 'distance estimate' }] });
    const lines = planRows(draft).map((row) => row.text);
    expect(lines.some((line) => line.includes('Travel: about 4.2 h'))).toBe(true);
    expect(lines).toContain('Travel check (tight): About 10 h exceeds this day');
  });

  it('credits the community sources and the tools', () => {
    const credits = texts.slice(texts.indexOf('Credits'));
    expect(credits.some((text) => text.startsWith('Traveler tips from reddit, quora'))).toBe(true);
    expect(credits.some((text) => text.includes('jsPDF'))).toBe(true);
  });

  it('works for a trip with no days or budget', () => {
    const empty = planRows(base({}));
    expect(empty.map((row) => row.text)).toContain('Total: not costed yet');
  });

  it('names sources by site', () => {
    expect(sourceName('https://www.quora.com/q')).toBe('quora');
    expect(sourceName('https://www.japan-guide.com/e/e3900.html')).toBe('japan-guide.com');
  });
});

describe('fontFor', () => {
  it('uses Helvetica for plain Latin, Caveat for wider Latin, Yomogi for Japanese', () => {
    expect(fontFor('Senso-ji · ¥3,000 — 1.5 h')).toBe('helvetica');
    expect(fontFor('Kyōto')).toBe('Caveat');
    expect(fontFor('₹1,200')).toBe('Caveat');
    expect(fontFor('嵐山 竹林の小径')).toBe('Yomogi');
  });
});
