import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { buildQueryTargets, getDefaultQueryTargetKey } from '../utils/queryTargets.js';

describe('queryTargets', () => {
  it('builds direct-mode query targets', () => {
    const targets = buildQueryTargets({ searchMode: 'direct' });

    assert.deepEqual(
      targets.map((target) => target.key),
      ['general', 'isbn', 'author', 'title'],
    );
  });

  it('builds universal query targets from provider fields', () => {
    const targets = buildQueryTargets({
      searchMode: 'universal',
      metadataSearchFields: [
        {
          key: 'author',
          label: 'Author',
          type: 'TextSearchField',
          description: 'Search by author name',
        },
        {
          key: 'hardcover_list',
          label: 'List',
          type: 'DynamicSelectSearchField',
          options_endpoint: '/api/metadata/field-options?provider=hardcover&field=hardcover_list',
          description: 'Browse books from a list',
        },
      ],
      manualSearchAllowed: true,
    });

    assert.deepEqual(
      targets.map((target) => target.key),
      ['general', 'author', 'hardcover_list', 'manual'],
    );
    assert.equal(targets[1]?.source, 'provider-field');
    assert.equal(targets[3]?.source, 'manual');
  });

  it('falls back to general when choosing a default target', () => {
    assert.equal(getDefaultQueryTargetKey([]), 'general');
  });
});
