import { describe, expect, it } from 'vitest';
import { prefillFromChat, readBrief } from './readBrief';

const sample = 'Plan a 10-day cultural journey to Japan focusing on traditional temples, tea ceremonies, vibrant street food in Osaka, and modern art in Tokyo with a budget of $3,500.';

describe('readBrief', () => {
  it('reads the trip facts and leaves the day count out of the party size', () => {
    const found = readBrief(sample);
    expect(found.destination).toBe('Japan');
    expect(found.days).toBe(10);
    expect(found.budget).toBe(3500);
    expect(found.currency).toBe('USD');
    expect(found.places).toEqual(['Osaka', 'Tokyo']);
    expect(found.interests).toEqual(['traditional temples', 'tea ceremonies', 'vibrant street food', 'modern art']);
    expect(found.adults).toBeUndefined();
  });

  it('does not keep a party size that was copied from the day count', () => {
    const values = prefillFromChat({
      destination: 'Japan',
      duration_days: 10,
      planning_preferences: { pace: 'balanced', interests: [], avoid: [], adults: 10 },
    }, sample);
    expect(values.budget_amount).toBe(3500);
    expect(values.currency).toBe('USD');
    expect(values.planning_preferences?.adults).toBeUndefined();
    expect(values.places_to_visit).toEqual(['Osaka', 'Tokyo']);
  });
});
