import { describe, expect, it } from 'vitest';

import { buildRowEntries } from '../components/settings/fields/TableField';

describe('TableField row entries', () => {
  it('keeps keys stable when controlled row values are replaced', () => {
    const rowIds = ['row-0'];

    const [firstEntry] = buildRowEntries([{ url: 'https://example.com' }], rowIds);
    const [secondEntry] = buildRowEntries([{ url: 'https://example.com/changed' }], rowIds);

    expect(secondEntry.key).toBe(firstEntry.key);
  });

  it('keeps remaining row keys when a row is removed', () => {
    const rowIds = ['row-0', 'row-1'];

    const initialEntries = buildRowEntries(
      [{ url: 'https://one.test' }, { url: 'https://two.test' }],
      rowIds,
    );
    const remainingIds = rowIds.filter((_, index) => index !== 0);
    const [remainingEntry] = buildRowEntries([{ url: 'https://two.test' }], remainingIds);

    expect(initialEntries[0].key).not.toBe(initialEntries[1].key);
    expect(remainingEntry.key).toBe(initialEntries[1].key);
  });
});
