import { describe, expect, it } from 'vitest';

import type { SeriesGapRequestResult, SeriesGapSeries, SeriesGapVolume } from '../services/api';
import {
  formatPosition,
  missingItems,
  missingItemsForFormat,
  selectionKey,
  seriesHasGaps,
  summarizeRequestResult,
  toggleItem,
  withItems,
} from '../utils/seriesGaps';

const volume = (overrides: Partial<SeriesGapVolume> = {}): SeriesGapVolume => ({
  position: 1,
  title: 'Dungeon Crawler Carl',
  provider_id: '1',
  year: 2020,
  statuses: { ebook: 'owned', audiobook: 'missing' },
  ...overrides,
});

const series = (overrides: Partial<SeriesGapSeries> = {}): SeriesGapSeries => ({
  key: 'dungeon crawler carl',
  name: 'Dungeon Crawler Carl',
  author: 'Matt Dinniman',
  status: 'ok',
  message: null,
  hardcover: { id: 77, name: 'Dungeon Crawler Carl', author: 'Matt Dinniman' },
  formats: {
    ebook: { collected: true, owned: 1, requested: 0, missing: 1 },
    audiobook: { collected: true, owned: 0, requested: 0, missing: 2 },
  },
  volumes: [
    volume(),
    volume({
      position: 2.5,
      title: 'Backstage at the Pinnacle',
      provider_id: '25',
      statuses: { ebook: 'missing', audiobook: 'missing' },
    }),
  ],
  ...overrides,
});

describe('missingItems', () => {
  it('lists every missing volume and format pair in series order', () => {
    expect(missingItems(series())).toEqual([
      { series_id: 77, provider_id: '1', content_type: 'audiobook' },
      { series_id: 77, provider_id: '25', content_type: 'ebook' },
      { series_id: 77, provider_id: '25', content_type: 'audiobook' },
    ]);
  });

  it('offers nothing for a series Hardcover does not list', () => {
    expect(missingItems(series({ hardcover: null }))).toEqual([]);
  });

  it('leaves a format the library does not collect out of select-all', () => {
    const only = series({
      formats: {
        ebook: { collected: true, owned: 1, requested: 0, missing: 1 },
        audiobook: { collected: false, owned: 0, requested: 0, missing: 2 },
      },
    });
    expect(missingItems(only)).toEqual([
      { series_id: 77, provider_id: '25', content_type: 'ebook' },
    ]);
  });

  it('still lists that format on its own, so it can be requested', () => {
    const only = series({
      formats: {
        ebook: { collected: true, owned: 1, requested: 0, missing: 1 },
        audiobook: { collected: false, owned: 0, requested: 0, missing: 2 },
      },
    });
    expect(missingItemsForFormat(only, 'audiobook')).toEqual([
      { series_id: 77, provider_id: '1', content_type: 'audiobook' },
      { series_id: 77, provider_id: '25', content_type: 'audiobook' },
    ]);
  });
});

describe('seriesHasGaps', () => {
  it('reads the per-format summaries', () => {
    expect(seriesHasGaps(series())).toBe(true);
    expect(
      seriesHasGaps(
        series({
          formats: { ebook: { collected: true, owned: 2, requested: 1, missing: 0 } },
        }),
      ),
    ).toBe(false);
  });
});

describe('seriesHasGaps with a format that is not collected', () => {
  it('counts what the library has none of as a gap', () => {
    expect(
      seriesHasGaps(
        series({
          formats: {
            ebook: { collected: true, owned: 2, requested: 0, missing: 0 },
            audiobook: { collected: false, owned: 0, requested: 0, missing: 2 },
          },
        }),
      ),
    ).toBe(true);
  });
});

describe('selection', () => {
  const item = { series_id: 77, provider_id: '25', content_type: 'ebook' as const };

  it('toggles one item in and out without touching the rest', () => {
    const other = { series_id: 77, provider_id: '1', content_type: 'audiobook' as const };
    const start = withItems({}, [other], true);

    const added = toggleItem(start, item);
    expect(Object.keys(added).toSorted()).toEqual(
      [selectionKey(other), selectionKey(item)].toSorted(),
    );

    const removed = toggleItem(added, item);
    expect(Object.keys(removed)).toEqual([selectionKey(other)]);
    expect(start).toEqual(withItems({}, [other], true));
  });

  it('adds and removes many at once', () => {
    const all = missingItems(series());
    const selected = withItems({}, all, true);
    expect(Object.keys(selected)).toHaveLength(3);
    expect(withItems(selected, all, false)).toEqual({});
  });
});

describe('formatPosition', () => {
  it('shows whole numbers plainly and keeps a novella fraction', () => {
    expect(formatPosition(3)).toBe('3');
    expect(formatPosition(2.5)).toBe('2.5');
    expect(formatPosition(null)).toBe('?');
  });
});

const result = (overrides: Partial<SeriesGapRequestResult>): SeriesGapRequestResult => ({
  created: 0,
  skipped: 0,
  errors: 0,
  items: [],
  ...overrides,
});

describe('summarizeRequestResult', () => {
  it('counts what happened in one line', () => {
    expect(summarizeRequestResult(result({ created: 1 }))).toBe('Requested 1 volume');
    expect(
      summarizeRequestResult(
        result({
          created: 2,
          skipped: 2,
          errors: 1,
          items: [
            {
              series_id: 77,
              provider_id: '1',
              content_type: 'ebook',
              title: null,
              result: 'requested',
            },
            {
              series_id: 77,
              provider_id: '2',
              content_type: 'ebook',
              title: null,
              result: 'owned',
            },
            {
              series_id: 77,
              provider_id: '3',
              content_type: 'ebook',
              title: null,
              result: 'error',
            },
          ],
        }),
      ),
    ).toBe('Requested 2 volumes, 1 already requested, 1 already in the library, 1 error');
  });
});
