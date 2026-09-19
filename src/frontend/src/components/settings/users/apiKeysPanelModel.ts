import type { ApiKeySummary } from '../../../services/api';

export interface ExpiryOption {
  value: number | null;
  label: string;
}

export const EXPIRY_OPTIONS: ExpiryOption[] = [
  { value: null, label: 'Never expires' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: 365, label: '1 year' },
];

// Shelfmark stores timestamps as "YYYY-MM-DD HH:MM:SS" in UTC.
const parseSqliteTimestamp = (value: string): Date | null => {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(value);
  if (!match) {
    return null;
  }
  const [, y, mo, d, h, mi, s] = match;
  return new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)));
};

export const formatApiKeyTimestamp = (value: string | null): string => {
  if (!value) {
    return 'Never';
  }
  const parsed = parseSqliteTimestamp(value);
  return parsed ? parsed.toISOString().slice(0, 10) : value;
};

export const splitApiKeys = (
  keys: ApiKeySummary[],
): { active: ApiKeySummary[]; revoked: ApiKeySummary[] } => ({
  active: keys.filter((key) => key.revoked_at === null),
  revoked: keys.filter((key) => key.revoked_at !== null),
});

export const describeApiKeyStatus = (key: ApiKeySummary, now: Date): string => {
  if (key.revoked_at !== null) {
    return 'Revoked';
  }
  if (key.expires_at !== null) {
    const expiry = parseSqliteTimestamp(key.expires_at);
    if (expiry && expiry.getTime() <= now.getTime()) {
      return 'Expired';
    }
    return `Expires ${formatApiKeyTimestamp(key.expires_at)}`;
  }
  return 'Active';
};
