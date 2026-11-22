import { describe, expect, it } from 'vitest';

import { buildManualSearchBook } from '../utils/manualSearchBook';

describe('buildManualSearchBook', () => {
  it('searches the sources with the trimmed query', () => {
    expect(buildManualSearchBook('  Тіні забутих предків ', 42)).toEqual({
      id: 'manual_42',
      title: 'Тіні забутих предків',
      author: '',
      provider: 'manual',
      provider_id: 'manual_42',
      search_title: 'Тіні забутих предків',
    });
  });

  it('returns null for a blank query', () => {
    expect(buildManualSearchBook('   ')).toBeNull();
  });
});
