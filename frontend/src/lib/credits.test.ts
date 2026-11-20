import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { GROUPS, PEXELS_FILES, UNSPLASH_FILES, photoCredit } from './credits';

describe('photoCredit', () => {
  it('reads the photographer and id out of Unsplash file names, ids with - and _ included', () => {
    expect(photoCredit('christoph-schulz-7tb-b37yHx4-unsplash.jpg'))
      .toEqual({ name: 'Christoph Schulz', url: 'https://unsplash.com/photos/7tb-b37yHx4' });
    expect(photoCredit('caleb-JmuyB_LibRo-unsplash.jpg'))
      .toEqual({ name: 'Caleb', url: 'https://unsplash.com/photos/JmuyB_LibRo' });
  });

  it('drops Pexels user ids from the name and keeps the photo id', () => {
    expect(photoCredit('pexels-ugurcan-ozmen-61083217-28553917.jpg'))
      .toEqual({ name: 'Ugurcan Ozmen', url: 'https://www.pexels.com/photo/28553917/' });
    expect(photoCredit('pexels-815774834-19259314.jpg'))
      .toEqual({ name: 'Pexels contributor', url: 'https://www.pexels.com/photo/19259314/' });
  });

  it('parses every listed file, and nothing else', () => {
    [...UNSPLASH_FILES, ...PEXELS_FILES].forEach((file) => expect(photoCredit(file), file).not.toBeNull());
    expect(photoCredit('hero-bg.jpg')).toBeNull();
  });
});

describe('credits stay complete', () => {
  it('lists every Unsplash and Pexels photo the source refers to', () => {
    const srcDir = fileURLToPath(new URL('..', import.meta.url));
    const used = new Set<string>();
    for (const rel of readdirSync(srcDir, { recursive: true }) as string[]) {
      if (!/\.(tsx?|css)$/.test(rel) || /credits(\.test)?\.ts$/.test(rel)) continue;
      const text = readFileSync(`${srcDir}/${rel}`, 'utf8');
      for (const [file] of text.matchAll(/[\w-]+-unsplash\.jpg|pexels-[\w-]+\.jpg/g)) used.add(file);
    }
    expect(used.size).toBeGreaterThan(0);
    const credited = new Set([...UNSPLASH_FILES, ...PEXELS_FILES]);
    expect([...used].filter((file) => !credited.has(file)), 'photos used but not credited').toEqual([]);
  });

  it('gives every group an id, title and something to show', () => {
    for (const group of GROUPS) {
      expect(group.rows?.length || group.photos?.length, group.id).toBeGreaterThan(0);
    }
    expect(new Set(GROUPS.map((g) => g.id)).size).toBe(GROUPS.length);
  });
});
