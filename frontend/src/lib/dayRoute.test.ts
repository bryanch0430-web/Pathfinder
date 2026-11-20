import { describe, expect, it } from 'vitest';
import { formatClock, placeOnRoute, pointAlong, routeChain, scheduleDay, splitPath } from './dayRoute';

describe('scheduleDay', () => {
  it('keeps stated clock times and packs an untimed morning', () => {
    const rows = scheduleDay([
      { name: 'Fushimi Inari', category: 'sight', start: '9:00', end: '11:00' },
      { name: 'Tofuku-ji', category: 'sight', time_of_day: 'morning' },
      { name: 'Nishiki', category: 'food', time_of_day: 'evening', start: '18:30', end: '20:00' },
    ]);
    expect(rows.map((row) => [row.name, formatClock(row.startMin), formatClock(row.endMin)])).toEqual([
      ['Fushimi Inari', '09:00', '11:00'],
      ['Tofuku-ji', '11:00', '12:30'],
      ['Nishiki', '18:30', '20:00'],
    ]);
  });

  it('fills a morning block when the plan never stated a clock', () => {
    const rows = scheduleDay([
      { name: 'Senso-ji', category: 'sight', time_of_day: 'morning', duration_hours: 1.5 },
      { name: 'Tsukiji', category: 'food', time_of_day: 'morning' },
    ]);
    expect(formatClock(rows[0].startMin)).toBe('09:00');
    expect(formatClock(rows[0].endMin)).toBe('10:30');
    expect(rows[1].startMin).toBe(rows[0].endMin);
  });
});

describe('routeChain', () => {
  it('leaves the stay and comes back after the last stop', () => {
    const chain = routeChain(scheduleDay([
      { name: 'Cross Hotel', category: 'stay' },
      { name: 'Fushimi Inari', category: 'sight', start: '09:00', end: '11:00' },
      { name: 'Nishiki', category: 'food', start: '12:30', end: '13:30' },
    ]));
    expect(chain.map((stop) => stop.name)).toEqual(['Cross Hotel', 'Fushimi Inari', 'Nishiki', 'Cross Hotel']);
    expect(formatClock(chain[0].startMin)).toBe('08:40');
    expect(formatClock(chain[0].endMin)).toBe('08:50');
    expect(formatClock(chain[3].startMin)).toBe('13:30');
    expect(chain[3].key.endsWith('-back')).toBe(true);
  });
});

describe('playback geometry', () => {
  const path = [{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }];

  it('walks the middle of a leg and splits travelled from ahead', () => {
    const mid = pointAlong(path, 0.5);
    expect(mid.lat).toBeCloseTo(0, 5);
    expect(mid.lng).toBeCloseTo(0.5, 1);
    const split = splitPath(path, 0.5);
    expect(split.done[0]).toEqual(path[0]);
    expect(split.done[split.done.length - 1].lng).toBeCloseTo(mid.lng, 5);
    expect(split.ahead[0].lng).toBeCloseTo(mid.lng, 5);
    expect(split.ahead[split.ahead.length - 1]).toEqual(path[1]);
  });

  it('sits on a stop during its window and moves in the gap after it', () => {
    const points = [{ start: 520, end: 530 }, { start: 540, end: 660 }];
    expect(placeOnRoute(points, 525)).toEqual({ at: 0, leg: null, t: 0 });
    const moving = placeOnRoute(points, 535);
    expect(moving.leg).toBe(0);
    expect(moving.t).toBeCloseTo(0.5, 5);
    expect(placeOnRoute(points, 600)).toEqual({ at: 1, leg: null, t: 0 });
  });
});
