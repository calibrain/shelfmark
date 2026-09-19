import { describe, expect, it } from 'vitest';

import {
  describeApiKeyStatus,
  EXPIRY_OPTIONS,
  formatApiKeyTimestamp,
  splitApiKeys,
} from '../components/settings/users/apiKeysPanelModel';
import type { ApiKeySummary } from '../services/api';

const base: ApiKeySummary = {
  id: 1,
  name: 'laptop',
  key_prefix: 'smk_abcdefgh',
  created_at: '2026-09-19 01:00:00',
  expires_at: null,
  last_used_at: null,
  revoked_at: null,
};

describe('apiKeysPanelModel', () => {
  it('offers never plus three expiry choices', () => {
    expect(EXPIRY_OPTIONS.map((o) => o.value)).toEqual([null, 30, 90, 365]);
  });

  it('splits active from revoked, keeping order', () => {
    const revoked = { ...base, id: 2, revoked_at: '2026-09-19 02:00:00' };
    const { active, revoked: gone } = splitApiKeys([revoked, base]);
    expect(active.map((k) => k.id)).toEqual([1]);
    expect(gone.map((k) => k.id)).toEqual([2]);
  });

  it('describes status', () => {
    const now = new Date('2026-09-20T00:00:00Z');
    expect(describeApiKeyStatus(base, now)).toBe('Active');
    expect(describeApiKeyStatus({ ...base, expires_at: '2026-09-19 00:00:00' }, now)).toBe(
      'Expired',
    );
    expect(describeApiKeyStatus({ ...base, expires_at: '2026-12-01 00:00:00' }, now)).toBe(
      'Expires 2026-12-01',
    );
    expect(describeApiKeyStatus({ ...base, revoked_at: '2026-09-19 03:00:00' }, now)).toBe(
      'Revoked',
    );
  });

  it('formats sqlite UTC timestamps and tolerates null', () => {
    expect(formatApiKeyTimestamp(null)).toBe('Never');
    expect(formatApiKeyTimestamp('2026-09-19 01:02:03')).toBe('2026-09-19');
  });
});
