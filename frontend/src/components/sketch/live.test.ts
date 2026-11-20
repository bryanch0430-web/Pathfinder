import { describe, expect, it } from 'vitest';
import type { CopilotState } from '../create/CopilotPanel';
import { base, tokyo } from './fixtures';
import { layoutSketch } from './layout';
import { describeChange, diffPages, withCopilot } from './live';

const copilot = (items: string[][]): CopilotState => ({
  status: 'building', current_day: 1, currency: 'JPY', budget: 100000, pool: [], last_suggestions: [],
  profile: { travel_mode: 'walk', pace: 'balanced' },
  days: items.map((names, i) => ({ day: i + 1, base: 'Kyoto', items: names.map((name) => ({ name, category: 'food', est_cost: 1200, duration_hours: 1, why: `${name} tip` })) })),
  summary: { committed: 104000, baseline: 90000, remaining: -4000, over: true, by_day: [{ day: 1, cost: 2400, hours: 2, capacity_hours: 8 }] },
});

describe('withCopilot', () => {
  const doc = base({ mode: 'agent', start_date: '2026-10-12', days: [], budget: { currency: 'JPY', target: null, planned: null, entered: 0, projected: 0 } });

  it('maps streamed days like the backend, with dates from the start date', () => {
    const live = withCopilot(doc, copilot([['Nishiki Market', 'Pontocho'], []]));
    expect(live.days.map((d) => [d.day, d.date, d.items.map((i) => i.name)])).toEqual([
      [1, '2026-10-12', ['Nishiki Market', 'Pontocho']], [2, '2026-10-13', []],
    ]);
    expect(live.days[0]).toMatchObject({ est_cost: 2400, hours: 2 });
    expect(live.days[0].items[0]).toMatchObject({ tip: 'Nishiki Market tip', time_of_day: null, option: false });
    expect(live.budget).toMatchObject({ target: 100000, planned: 104000 });
  });

  it('leaves one-shot trips and missing copilot state alone', () => {
    expect(withCopilot(tokyo, null)).toBe(tokyo);
    expect(withCopilot({ ...tokyo, mode: 'one_shot' }, copilot([['X']]))).toEqual({ ...tokyo, mode: 'one_shot' });
  });
});

describe('diffPages', () => {
  const doc = (names: string[][]) => withCopilot(base({ mode: 'agent', days: [] }), copilot(names));

  it('reports only the new place on its page, and keeps other cards untouched', () => {
    const before = layoutSketch(doc([['Nishiki Market', 'Gion walk'], ['Arashiyama']]));
    const after = layoutSketch(doc([['Kagurazaka', 'Nishiki Market', 'Gion walk'], ['Arashiyama']]));
    const changes = diffPages(before, after).filter((change) => change.page.kind === 'day');
    expect(changes.map((c) => c.page.day)).toEqual([1]);
    expect(changes[0].addedPlaces).toEqual(['Kagurazaka']);
    expect(changes[0].added.some((id) => id.startsWith('d1-p-nishiki'))).toBe(false);
    expect(describeChange(changes[0])).toBe('Added Kagurazaka to day 1.');
  });

  it('describes removals and swaps', () => {
    const before = layoutSketch(doc([['Nishiki Market', 'Gion walk']]));
    const removed = diffPages(before, layoutSketch(doc([['Gion walk']]))).find((c) => c.page.day === 1)!;
    expect(describeChange(removed)).toBe('Took Nishiki Market off day 1.');
    const swapped = diffPages(before, layoutSketch(doc([['Gion walk', 'Pontocho']]))).find((c) => c.page.day === 1)!;
    expect(describeChange(swapped)).toBe('Swapped Nishiki Market for Pontocho on day 1.');
  });

  it('finds nothing when the layout is unchanged', () => {
    const pages = layoutSketch(tokyo);
    expect(diffPages(pages, layoutSketch({ ...tokyo }))).toEqual([]);
  });

  it('keeps duplicate names apart', () => {
    const ids = layoutSketch(doc([['Ramen', 'Ramen']]))[1].elements.filter((el) => el.kind === 'box').map((el) => el.id);
    expect(ids).toEqual(['d1-p-ramen', 'd1-p-ramen-2']);
  });
});
