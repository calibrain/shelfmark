import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Language } from '../types/index.js';
import {
  LANGUAGE_OPTION_ALL,
  LANGUAGE_OPTION_DEFAULT,
  getReleaseSearchLanguageParams,
} from '../utils/languageFilters.js';

const supportedLanguages: Language[] = [
  { code: 'en', language: 'English' },
  { code: 'de', language: 'German' },
  { code: 'hu', language: 'Hungarian' },
];

describe('languageFilters release search params', () => {
  it('omits languages when only default selection is active', () => {
    const result = getReleaseSearchLanguageParams(
      [LANGUAGE_OPTION_DEFAULT],
      supportedLanguages,
      ['en'],
    );

    assert.equal(result, undefined);
  });

  it('preserves explicit all-languages selection', () => {
    const result = getReleaseSearchLanguageParams(
      [LANGUAGE_OPTION_ALL],
      supportedLanguages,
      ['en'],
    );

    assert.deepEqual(result, [LANGUAGE_OPTION_ALL]);
  });

  it('resolves explicit language selections to codes', () => {
    const result = getReleaseSearchLanguageParams(
      ['de', 'hu'],
      supportedLanguages,
      ['en'],
    );

    assert.deepEqual(result, ['de', 'hu']);
  });
});
