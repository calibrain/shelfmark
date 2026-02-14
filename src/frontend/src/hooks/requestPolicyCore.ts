import { ContentType, RequestPolicyMode, RequestPolicyResponse } from '../types';

export const DEFAULT_POLICY_TTL_MS = 60_000;

export interface RefreshPolicyOptions {
  enabled: boolean;
  isAdmin: boolean;
  force?: boolean;
}

export const normalizeContentType = (value: ContentType | string): ContentType => {
  return String(value).trim().toLowerCase() === 'audiobook' ? 'audiobook' : 'ebook';
};

export const normalizeSource = (value: string): string => {
  const source = String(value || '').trim().toLowerCase();
  return source || '*';
};

export const resolveDefaultModeFromPolicy = (
  policy: RequestPolicyResponse | null,
  isAdmin: boolean,
  contentType: ContentType | string
): RequestPolicyMode => {
  if (isAdmin || policy?.is_admin) {
    return 'download';
  }
  if (!policy || !policy.requests_enabled) {
    return 'download';
  }
  const normalizedContentType = normalizeContentType(contentType);
  return policy.defaults?.[normalizedContentType] || 'download';
};

export const resolveSourceModeFromPolicy = (
  policy: RequestPolicyResponse | null,
  isAdmin: boolean,
  source: string,
  contentType: ContentType | string
): RequestPolicyMode => {
  const defaultMode = resolveDefaultModeFromPolicy(policy, isAdmin, contentType);
  if (defaultMode === 'download' && (isAdmin || !policy || !policy.requests_enabled)) {
    return 'download';
  }

  const normalizedSource = normalizeSource(source);
  const normalizedContentType = normalizeContentType(contentType);
  const sourceModes = policy?.source_modes?.find(
    (sourceMode) => normalizeSource(sourceMode.source) === normalizedSource
  );
  const fromSource = sourceModes?.modes?.[normalizedContentType];
  return fromSource || defaultMode;
};

export class RequestPolicyCache {
  private ttlMs: number;
  private policy: RequestPolicyResponse | null = null;
  private lastFetchedAt = 0;
  private inFlight: Promise<RequestPolicyResponse | null> | null = null;

  constructor(
    private readonly fetchPolicy: () => Promise<RequestPolicyResponse>,
    ttlMs: number = DEFAULT_POLICY_TTL_MS
  ) {
    this.ttlMs = ttlMs;
  }

  setTtlMs(ttlMs: number): void {
    this.ttlMs = ttlMs;
  }

  reset(): void {
    this.policy = null;
    this.lastFetchedAt = 0;
    this.inFlight = null;
  }

  async refresh({
    enabled,
    isAdmin,
    force = false,
  }: RefreshPolicyOptions): Promise<RequestPolicyResponse | null> {
    if (!enabled || isAdmin) {
      this.reset();
      return null;
    }

    const now = Date.now();
    if (!force && this.policy && now - this.lastFetchedAt < this.ttlMs) {
      return this.policy;
    }

    if (this.inFlight) {
      return this.inFlight;
    }

    const requestPromise = this.fetchPolicy()
      .then((response) => {
        this.policy = response;
        this.lastFetchedAt = Date.now();
        return response;
      })
      .finally(() => {
        this.inFlight = null;
      });

    this.inFlight = requestPromise;
    return requestPromise;
  }
}

