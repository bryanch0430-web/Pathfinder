import { describe, expect, it } from 'vitest';
import type { TripDocument } from '../../services/tripService';
import { base, item, kyoto, tokyo } from './fixtures';
import { FRAMES, dayTitle, layoutSketch, slotItems, textWidth, wrap, type Orientation } from './layout';
import type { SketchElement, SketchPage } from './types';

const ORIENTATIONS: Orientation[] = ['landscape', 'portrait'];

type Rect = { x: number; y: number; w: number; h: number };

/** Every left-aligned line of text fits across the page (by the same width estimate the layout uses). */
function expectTextOnPage(page: SketchPage) {
  page.elements.forEach((el) => {
    if (el.kind !== 'text' || (el.anchor ?? 'start') !== 'start') return;
    el.lines.forEach((line) => expect(el.x + textWidth(line, el.size), `${page.id} ${el.id}`).toBeLessThanOrEqual(page.w - 8));
  });
}
const rectOf = (el: SketchElement): Rect | null => (el.kind === 'box' || el.kind === 'note' ? { x: el.x, y: el.y, w: el.w, h: el.h } : null);
const overlaps = (a: Rect, b: Rect) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

describe('layoutSketch', () => {
  it('matches the Tokyo and Kyoto fixtures', () => {
    expect(layoutSketch(tokyo)).toMatchSnapshot('tokyo');
    expect(layoutSketch(kyoto)).toMatchSnapshot('kyoto');
  });

  it('is deterministic with unique ids per page', () => {
    expect(layoutSketch(tokyo)).toEqual(layoutSketch(tokyo));
    for (const page of layoutSketch(tokyo)) {
      const ids = page.elements.map((el) => el.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it('titles day pages with the real date', () => {
    expect(layoutSketch(tokyo)[2].title).toBe('Day 2 · Tue 13 Oct');
    expect(dayTitle(3, null)).toBe('Day 3');
  });

  it.each(ORIENTATIONS)('never overlaps cards or notes, 1–8 items a day, and stays on the page (%s)', (orientation) => {
    const times = ['morning', 'afternoon', 'evening', null] as const;
    for (let count = 1; count <= 8; count++) {
      for (let shift = 0; shift < 4; shift++) {
        const items = Array.from({ length: count }, (_, i) => item(`Place number ${i} with a fairly long name indeed`, { area: 'Higashiyama district near the river', duration_hours: 2, est_cost: 1500,
          time_of_day: times[(i + shift) % 4], tip: i % 2 ? 'A tip that is long enough to wrap over several lines of the note' : null,
        }));
        const [, page] = layoutSketch(base({ days: [{ day: 1, date: '2026-10-12', base: 'Kyoto', est_cost: null, hours: null, items }] }), orientation);
        const rects = page.elements.map(rectOf).filter(Boolean) as Rect[];
        rects.forEach((a, i) => rects.slice(i + 1).forEach((b) => expect(overlaps(a, b)).toBe(false)));
        rects.forEach((r) => {
          expect(r.x).toBeGreaterThanOrEqual(0); expect(r.x + r.w).toBeLessThanOrEqual(page.w);
          expect(r.y + r.h).toBeLessThanOrEqual(page.h);
        });
        // Card text (three-line names and their meta) ends inside the card.
        page.elements.forEach((el) => {
          if (el.kind !== 'text' || !/-p-[\w-]+-(name|meta)$/.test(el.id)) return;
          const card = page.elements.find((box) => box.id === el.id.replace(/-(name|meta)$/, ''));
          if (card?.kind !== 'box') throw new Error(`no card for ${el.id}`);
          expect(el.y + (el.lines.length - 1) * el.size * 1.08).toBeLessThanOrEqual(card.y + card.h - 6);
        });
        const shownCards = page.elements.filter((el) => el.kind === 'box').length;
        const more = page.elements.filter((el) => el.kind === 'text' && el.lines[0].startsWith('+'))
          .reduce((sum, el) => sum + Number((el as { lines: string[] }).lines[0].match(/\d+/)![0]), 0);
        expect(shownCards + more).toBe(count);
        expectTextOnPage(page);
      }
    }
  });

  it('gives both orientations the same elements, so live drawing works in either', () => {
    const ids = (pages: SketchPage[]) => pages.map((page) => page.elements.map((el) => el.id));
    expect(ids(layoutSketch(tokyo, 'portrait'))).toEqual(ids(layoutSketch(tokyo)));
    expect(layoutSketch(tokyo, 'portrait').map((page) => [page.w, page.h])).toEqual(Array(5).fill([794, 1123]));
    layoutSketch(tokyo, 'portrait').forEach(expectTextOnPage);
    // Portrait has no room beside the subtitle: the holiday gets its own, untruncated line.
    expect(layoutSketch(tokyo, 'portrait')[1].elements.find((el) => el.id === 'd1-holiday')).toMatchObject({ x: 78, y: 154, lines: ['Sports Day (holiday)'] });
  });

  it('says "+N more" when a slot is too busy', () => {
    const items = Array.from({ length: 5 }, (_, i) => item(`Stop ${i}`, { time_of_day: 'morning' }));
    const [, page] = layoutSketch(base({ days: [{ day: 1, date: null, base: 'X', est_cost: null, hours: null, items }] }));
    expect(page.elements.some((el) => el.kind === 'text' && el.lines[0] === '+2 more')).toBe(true);
    expect(page.elements.filter((el) => el.kind === 'box')).toHaveLength(FRAMES.landscape.card.perSlot);
  });

  it.each(ORIENTATIONS)('handles a 30-day trip (%s)', (orientation) => {
    const days = Array.from({ length: 30 }, (_, i) => ({
      day: i + 1, date: null, base: `City ${Math.floor(i / 2)}`, est_cost: null, hours: null, items: [item('A'), item('B')],
    }));
    const pages = layoutSketch(base({ days, duration_days: 30 }), orientation);
    expect(pages).toHaveLength(31);
    // 15 stops fit on three rows: no overflow note, and every bubble is on the page.
    const bubbles = pages[0].elements.filter((el) => el.kind === 'ellipse');
    expect(bubbles).toHaveLength(15);
    bubbles.forEach((el) => el.kind === 'ellipse' && expect(el.cx + el.w / 2).toBeLessThanOrEqual(pages[0].w));
    bubbles.forEach((el) => el.kind === 'ellipse' && expect(el.cy + el.h / 2).toBeLessThan(FRAMES[orientation].stamps.top));
  });

  it('only writes notes and stamps from the document', () => {
    const pages = layoutSketch(tokyo);
    const tips = tokyo.days.flatMap((d) => d.items.map((i) => i.tip)).filter(Boolean) as string[];
    const notes = pages.flatMap((p) => p.elements).filter((el) => el.kind === 'note');
    expect(notes).toHaveLength(3);
    notes.forEach((note) => note.kind === 'note' && expect(tips.some((tip) => tip.startsWith(note.lines.join(' ').replace(/…$/, '')))).toBe(true));
    expect(notes.map((note) => note.kind === 'note' && note.tag)).toEqual(['reddit', 'quora', null]);
    const stamps = pages[0].elements.filter((el) => el.kind === 'stamp').map((el) => el.kind === 'stamp' && el.text);
    expect(stamps).toEqual(['loves food', 'loves temples', 'no museums ×']);
  });

  it('draws typical-month weather from its mean and rain', () => {
    const [, page] = layoutSketch(base({
      days: [{ day: 1, date: '2026-11-03', base: 'Tokyo', est_cost: null, hours: null, items: [] }],
      enrichment: { holidays: [], exchange: null, sources: [], weather: [{
        day: 1, date: '2026-11-03', base: 'Tokyo', kind: 'typical', label: 'Typical for November', condition: null,
        temp_min: null, temp_max: null, temp_mean: 12.7, rain_mm: 4, badge: { tone: 'info', text: 'Showers' }, notes: [],
      }] },
    }));
    expect(page.elements.find((el) => el.id === 'd1-wx')).toMatchObject({ name: 'rain' });
    expect(page.elements.find((el) => el.id === 'd1-wx-t')).toMatchObject({ lines: ['~13°C'] });
    expect(page.elements.find((el) => el.id === 'd1-wx-l')).toMatchObject({ lines: ['Typical for November ·', 'Showers'] });
  });

  it('circles the receipt total in red only when over budget', () => {
    const circled = (doc: TripDocument) => layoutSketch(doc)[0].elements.some((el) => el.id === 'rc-circle');
    expect(circled(tokyo)).toBe(true);
    expect(circled(kyoto)).toBe(false);
    // Every receipt line sits inside the receipt box.
    const els = layoutSketch(tokyo)[0].elements;
    const box = els.find((el) => el.id === 'rc-box');
    if (box?.kind !== 'box') throw new Error('no receipt');
    els.filter((el) => el.id.startsWith('rc-') && el.kind === 'text')
      .forEach((el) => el.kind === 'text' && expect(el.y).toBeLessThanOrEqual(box.y + box.h - 8));
  });

  it('highlights must-see items and draws flights as dashed arcs', () => {
    const els = layoutSketch(tokyo).flatMap((p) => p.elements);
    expect(els.filter((el) => el.kind === 'highlight').map((el) => el.id)).toEqual(['d3-p-fushimi-inari-taisha-hl']);
    const flight = layoutSketch(base({
      travel_mode: 'transit', legs: [{ source: 'Delhi', target: 'Goa', mode: 'flight', duration: null, distance: null, cost: null }],
      days: [{ day: 1, date: null, base: 'Delhi', est_cost: null, hours: null, items: [] }, { day: 2, date: null, base: 'Goa', est_cost: null, hours: null, items: [] }],
    }))[0].elements;
    expect(flight.find((el) => el.id === 'ov-leg-1')).toMatchObject({ dashed: true, curve: true });
    expect(flight.find((el) => el.id === 'ov-leg-1-mode')).toMatchObject({ name: 'plane' });
  });
});

describe('wrap', () => {
  it('wraps words and ends overflow with an ellipsis', () => {
    expect(wrap('Fushimi Inari Taisha', 200, 25, 2)).toEqual(['Fushimi Inari', 'Taisha']);
    const long = wrap('A very long place name that keeps going and going past two lines', 150, 25, 2);
    expect(long).toHaveLength(2);
    expect(long[1].endsWith('…')).toBe(true);
    long.forEach((line) => expect(textWidth(line, 25)).toBeLessThanOrEqual(150));
  });

  it('counts CJK as wide and splits unbroken text', () => {
    expect(textWidth('嵐山', 20)).toBe(40);
    const lines = wrap('嵐山竹林の小径嵐山竹林の小径', 100, 20, 2);
    lines.forEach((line) => expect(textWidth(line, 20)).toBeLessThanOrEqual(100));
  });
});

describe('slotItems', () => {
  it('sends untimed items to the emptiest slot', () => {
    const columns = slotItems([item('a', { time_of_day: 'morning' }), item('b'), item('c')]);
    expect(columns.map((column) => column.map(({ item: i }) => i.name))).toEqual([['a'], ['b'], ['c']]);
  });
});
