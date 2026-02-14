import { ContentType, RequestPolicyMode, RequestPolicyResponse } from '../types';

export const DEFAULT_POLICY_TTL_MS = 60_000;

const MODE_RANK: Record<RequestPolicyMode, number> = {
  download: 0,
  request_release: 1,
  request_book: 2,
  blocked: 3,
};

const MATRIX_MODES = new Set<RequestPolicyMode>(['download', 'request_release', 'blocked']);

const capModeToCeiling = (mode: RequestPolicyMode, ceiling: RequestPolicyMode): RequestPolicyMode => {
  return MODE_RANK[mode] < MODE_RANK[ceiling] ? ceiling : mode;
};

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

const normalizeRuleSource = (value: unknown): string | null => {
  if (typeof value !== 'string') {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  if (!normalized || normalized === 'any') {
    return '*';
  }
  return normalized;
};

const normalizeRuleContentType = (value: unknown): ContentType | '*' | null => {
  if (typeof value !== 'string') {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  if (!normalized || normalized === 'any') {
    return '*';
  }
  if (normalized === '*') {
    return '*';
  }
  return normalizeContentType(normalized);
};

const parseMatrixMode = (value: unknown): RequestPolicyMode | null => {
  if (typeof value !== 'string') {
    return null;
  }
  const normalized = value.trim().toLowerCase() as RequestPolicyMode;
  if (!MATRIX_MODES.has(normalized)) {
    return null;
  }
  return normalized;
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
  if (fromSource) {
    return capModeToCeiling(fromSource, defaultMode);
  }

  const rules = Array.isArray(policy?.rules) ? policy.rules : [];
  const precedence: Array<[string, ContentType | '*']> = [
    [normalizedSource, normalizedContentType],
    [normalizedSource, '*'],
    ['*', normalizedContentType],
    ['*', '*'],
  ];

  for (const [sourceMatch, contentTypeMatch] of precedence) {
    const matchedRule = rules.find((rule) => {
      if (!rule || typeof rule !== 'object') {
        return false;
      }
      const ruleSource = normalizeRuleSource((rule as Record<string, unknown>).source);
      const ruleContentType = normalizeRuleContentType((rule as Record<string, unknown>).content_type);
      return ruleSource === sourceMatch && ruleContentType === contentTypeMatch;
    });

    if (!matchedRule || typeof matchedRule !== 'object') {
      continue;
    }

    const parsedMode = parseMatrixMode((matchedRule as Record<string, unknown>).mode);
    if (!parsedMode) {
      continue;
    }

    return capModeToCeiling(parsedMode, defaultMode);
  }

  return defaultMode;
};

export class RequestPolicyCache {
  private ttlMs: number;
  private policy: RequestPolicyResponse | null = null;
  private lastFetchedAt = 0;
  private inFlight: Promise<RequestPolicyResponse | null> | null = null;
  private inFlightWasForced = false;

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
    this.inFlightWasForced = false;
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
      // If a forced refresh arrives while a best-effort refresh is in-flight,
      // wait for the current request and then fetch a fresh snapshot.
      if (force && !this.inFlightWasForced) {
        try {
          await this.inFlight;
        } catch {
          // Ignore failures from the superseded in-flight request.
        }
      } else {
        return this.inFlight;
      }
    }

    if (!force && this.policy && Date.now() - this.lastFetchedAt < this.ttlMs) {
      return this.policy;
    }

    const requestPromise = this.fetchPolicy()
      .then((response) => {
        this.policy = response;
        this.lastFetchedAt = Date.now();
        return response;
      })
      .finally(() => {
        this.inFlight = null;
        this.inFlightWasForced = false;
      });

    this.inFlightWasForced = force;
    this.inFlight = requestPromise;
    return requestPromise;
  }
}
