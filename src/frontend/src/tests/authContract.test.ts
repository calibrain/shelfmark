import { describe, expect, it } from 'vitest';

import type { AuthCheckResponse } from '../types';

describe('authenticated capability contract', () => {
  it.each(['download-capable', 'request-only'] as const)(
    'requires the %s capability from the bootstrap response',
    (library_capability) => {
      const response: AuthCheckResponse = {
        authenticated: true,
        auth_required: true,
        auth_mode: 'builtin',
        is_admin: false,
        library_capability,
      };

      expect(response.library_capability).toBe(library_capability);
    },
  );

  it('keeps administrator status independent from capability', () => {
    const response: AuthCheckResponse = {
      authenticated: true,
      auth_required: true,
      auth_mode: 'builtin',
      is_admin: true,
      library_capability: 'request-only',
    };

    expect(response.is_admin).toBe(true);
    expect(response.library_capability).toBe('request-only');
  });
});
