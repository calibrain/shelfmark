import {
  Book,
  StatusData,
  AppConfig,
  LoginCredentials,
  AuthResponse,
  ReleaseSource,
  ReleasesResponse,
  RequestPolicyResponse,
  CreateRequestPayload,
  RequestRecord,
} from '../types';
import { SettingsResponse, ActionResult, UpdateResult, SettingsTab } from '../types/settings';
import { MetadataBookData, transformMetadataToBook } from '../utils/bookTransformers';
import { getApiBase } from '../utils/basePath';
import {
  buildAdminRequestActionUrl,
  buildFulfilAdminRequestBody,
  buildRejectAdminRequestBody,
  buildRequestListUrl,
  FulfilAdminRequestBody,
  RejectAdminRequestBody,
  RequestListParams,
} from './requestApiHelpers';

const API_BASE = getApiBase();

// API endpoints
const API = {
  search: `${API_BASE}/search`,
  metadataSearch: `${API_BASE}/metadata/search`,
  info: `${API_BASE}/info`,
  download: `${API_BASE}/download`,
  status: `${API_BASE}/status`,
  cancelDownload: `${API_BASE}/download`,
  setPriority: `${API_BASE}/queue`,
  clearCompleted: `${API_BASE}/queue/clear`,
  config: `${API_BASE}/config`,
  login: `${API_BASE}/auth/login`,
  logout: `${API_BASE}/auth/logout`,
  authCheck: `${API_BASE}/auth/check`,
  settings: `${API_BASE}/settings`,
  requestPolicy: `${API_BASE}/request-policy`,
  requests: `${API_BASE}/requests`,
  adminRequests: `${API_BASE}/admin/requests`,
  adminRequestCounts: `${API_BASE}/admin/requests/count`,
  activitySnapshot: `${API_BASE}/activity/snapshot`,
  activityDismiss: `${API_BASE}/activity/dismiss`,
  activityDismissMany: `${API_BASE}/activity/dismiss-many`,
  activityHistory: `${API_BASE}/activity/history`,
};

// Custom error class for authentication failures
export class AuthenticationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthenticationError';
  }
}

// Custom error class for request timeouts
export class TimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TimeoutError';
  }
}

export class ApiResponseError extends Error {
  status: number;
  code?: string;
  requiredMode?: string;
  payload?: Record<string, unknown>;

  constructor(
    message: string,
    params: {
      status: number;
      code?: string;
      requiredMode?: string;
      payload?: Record<string, unknown>;
    }
  ) {
    super(message);
    this.name = 'ApiResponseError';
    this.status = params.status;
    this.code = params.code;
    this.requiredMode = params.requiredMode;
    this.payload = params.payload;
  }
}

export const isApiResponseError = (error: unknown): error is ApiResponseError => {
  return error instanceof ApiResponseError;
};

const mapApiErrorToActionResult = (error: unknown): ActionResult | null => {
  if (!isApiResponseError(error) || !error.payload) {
    return null;
  }

  const payload = error.payload;
  const message =
    typeof payload.message === 'string'
      ? payload.message
      : (typeof payload.error === 'string' ? payload.error : null);
  if (!message) {
    return null;
  }

  const details = Array.isArray(payload.details)
    ? payload.details.filter((detail): detail is string => typeof detail === 'string' && detail.trim().length > 0)
    : undefined;

  return {
    success: false,
    message,
    ...(details && details.length > 0 ? { details } : {}),
  };
};

// Default request timeout in milliseconds (30 seconds)
const DEFAULT_TIMEOUT_MS = 30000;

// Utility function for JSON fetch with credentials and timeout
async function fetchJSON<T>(
  url: string,
  opts: RequestInit = {},
  timeoutMs: number | null = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = timeoutMs && timeoutMs > 0
    ? setTimeout(() => controller.abort(), timeoutMs)
    : null;

  try {
    const res = await fetch(url, {
      ...opts,
      credentials: 'include',  // Enable cookies for session
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...opts.headers,
      },
    });

    if (!res.ok) {
      // Try to parse error message from response body
      let errorMessage = `${res.status} ${res.statusText}`;
      let hasServerMessage = false;
      let errorData: Record<string, unknown> | null = null;
      try {
        const parsed = await res.json();
        if (parsed && typeof parsed === 'object') {
          errorData = parsed as Record<string, unknown>;
        }
        // Prefer user-friendly 'message' field, fall back to 'error'
        if (typeof errorData?.message === 'string') {
          errorMessage = errorData.message;
          hasServerMessage = true;
        } else if (typeof errorData?.error === 'string') {
          errorMessage = errorData.error;
          hasServerMessage = true;
        }
      } catch (e) {
        // Log parse failure for debugging - server may have returned non-JSON (e.g., HTML error page)
        console.warn(`Failed to parse error response from ${url}:`, e instanceof Error ? e.message : e);
      }

      // Provide helpful message for gateway/proxy errors
      if (res.status === 502 || res.status === 503 || res.status === 504) {
        if (!hasServerMessage) {
          errorMessage = `Server unavailable (${res.status}). If using a reverse proxy, check its configuration.`;
        }
      }

      // Throw appropriate error based on status code
      if (res.status === 401) {
        throw new AuthenticationError(errorMessage);
      }

      throw new ApiResponseError(errorMessage, {
        status: res.status,
        code: typeof errorData?.code === 'string' ? errorData.code : undefined,
        requiredMode:
          typeof errorData?.required_mode === 'string' ? errorData.required_mode : undefined,
        payload: errorData || undefined,
      });
    }

    return res.json();
  } catch (error) {
    // Handle abort/timeout errors
    if (error instanceof Error && error.name === 'AbortError') {
      throw new TimeoutError('Request timed out. Check your network connection or proxy configuration.');
    }
    throw error;
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
}

// API functions
export const searchBooks = async (query: string): Promise<Book[]> => {
  if (!query) return [];
  return fetchJSON<Book[]>(`${API.search}?${query}`);
};

// Metadata search response type (internal)
interface MetadataSearchResponse {
  books: MetadataBookData[];
  provider: string;
  query: string;
  page?: number;
  total_found?: number;
  has_more?: boolean;
}

// Metadata search result with pagination info
export interface MetadataSearchResult {
  books: Book[];
  page: number;
  totalFound: number;
  hasMore: boolean;
}

// Search metadata providers and normalize to Book format
export const searchMetadata = async (
  query: string,
  limit: number = 40,
  sort: string = 'relevance',
  fields: Record<string, string | number | boolean> = {},
  page: number = 1,
  contentType: string = 'ebook'
): Promise<MetadataSearchResult> => {
  const hasFields = Object.values(fields).some(v => v !== '' && v !== false);

  if (!query && !hasFields) {
    return { books: [], page: 1, totalFound: 0, hasMore: false };
  }

  const params = new URLSearchParams();
  if (query) {
    params.set('query', query);
  }
  params.set('limit', String(limit));
  params.set('sort', sort);
  params.set('page', String(page));
  params.set('content_type', contentType);

  // Add custom search field values
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== '' && value !== false) {
      params.set(key, String(value));
    }
  });

  const response = await fetchJSON<MetadataSearchResponse>(`${API.metadataSearch}?${params.toString()}`);

  return {
    books: response.books.map(transformMetadataToBook),
    page: response.page || page,
    totalFound: response.total_found || 0,
    hasMore: response.has_more || false,
  };
};

export const getBookInfo = async (id: string): Promise<Book> => {
  return fetchJSON<Book>(`${API.info}?id=${encodeURIComponent(id)}`);
};

// Get full book details from a metadata provider
export const getMetadataBookInfo = async (provider: string, bookId: string): Promise<Book> => {
  const response = await fetchJSON<MetadataBookData>(
    `${API_BASE}/metadata/book/${encodeURIComponent(provider)}/${encodeURIComponent(bookId)}`
  );

  return transformMetadataToBook(response);
};

export const downloadBook = async (id: string): Promise<void> => {
  const params = new URLSearchParams();
  params.set('id', id);
  await fetchJSON(`${API.download}?${params.toString()}`);
};

// Download a specific release (from ReleaseModal)
export const downloadRelease = async (release: {
  source: string;
  source_id: string;
  title: string;
  author?: string;   // Author from metadata provider
  year?: string;     // Year from metadata provider
  format?: string;
  size?: string;
  size_bytes?: number;
  download_url?: string;
  protocol?: string;
  indexer?: string;
  seeders?: number;
  extra?: Record<string, unknown>;
  preview?: string;  // Book cover from metadata provider
  content_type?: string;  // "ebook" or "audiobook" - for directory routing
  series_name?: string;
  series_position?: number;
  subtitle?: string;
  search_author?: string;
}): Promise<void> => {
  await fetchJSON(`${API_BASE}/releases/download`, {
    method: 'POST',
    body: JSON.stringify(release),
  });
};

export const getStatus = async (): Promise<StatusData> => {
  return fetchJSON<StatusData>(API.status);
};

export const getActivitySnapshot = async (): Promise<ActivitySnapshotResponse> => {
  return fetchJSON<ActivitySnapshotResponse>(API.activitySnapshot);
};

export const dismissActivityItem = async (payload: ActivityDismissPayload): Promise<void> => {
  await fetchJSON(API.activityDismiss, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const dismissManyActivityItems = async (items: ActivityDismissPayload[]): Promise<void> => {
  await fetchJSON(API.activityDismissMany, {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
};

export const listActivityHistory = async (
  limit: number = 50,
  offset: number = 0
): Promise<ActivityHistoryItem[]> => {
  const params = new URLSearchParams();
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return fetchJSON<ActivityHistoryItem[]>(`${API.activityHistory}?${params.toString()}`);
};

export const clearActivityHistory = async (): Promise<void> => {
  await fetchJSON(API.activityHistory, { method: 'DELETE' });
};

export const cancelDownload = async (id: string): Promise<void> => {
  await fetchJSON(`${API.cancelDownload}/${encodeURIComponent(id)}/cancel`, { method: 'DELETE' });
};

export const clearCompleted = async (): Promise<void> => {
  await fetchJSON(`${API_BASE}/queue/clear`, { method: 'DELETE' });
};

export const getConfig = async (): Promise<AppConfig> => {
  return fetchJSON<AppConfig>(API.config);
};

export type ListRequestsParams = RequestListParams;

export interface AdminRequestCounts {
  pending: number;
  total: number;
  by_status: Record<string, number>;
}

export interface ActivityDismissedItem {
  item_type: 'download' | 'request';
  item_key: string;
}

export interface ActivitySnapshotResponse {
  status: StatusData;
  requests: RequestRecord[];
  dismissed: ActivityDismissedItem[];
}

export interface ActivityDismissPayload {
  item_type: 'download' | 'request';
  item_key: string;
  activity_log_id?: number;
}

export interface ActivityHistoryItem {
  id: number;
  user_id: number;
  item_type: 'download' | 'request';
  item_key: string;
  activity_log_id: number | null;
  dismissed_at: string;
  snapshot: Record<string, unknown> | null;
  origin: 'direct' | 'request' | 'requested' | null;
  final_status: string | null;
  terminal_at: string | null;
  request_id: number | null;
  source_id: string | null;
}

export const fetchRequestPolicy = async (): Promise<RequestPolicyResponse> => {
  return fetchJSON<RequestPolicyResponse>(API.requestPolicy);
};

export const createRequest = async (payload: CreateRequestPayload): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(API.requests, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const listRequests = async (params: ListRequestsParams = {}): Promise<RequestRecord[]> => {
  const url = buildRequestListUrl(API.requests, params);
  return fetchJSON<RequestRecord[]>(url);
};

export const cancelRequest = async (id: number): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(`${API.requests}/${encodeURIComponent(String(id))}`, {
    method: 'DELETE',
  });
};

export const listAdminRequests = async (params: ListRequestsParams = {}): Promise<RequestRecord[]> => {
  const url = buildRequestListUrl(API.adminRequests, params);
  return fetchJSON<RequestRecord[]>(url);
};

export const getAdminRequestCounts = async (): Promise<AdminRequestCounts> => {
  return fetchJSON<AdminRequestCounts>(API.adminRequestCounts);
};

export const fulfilAdminRequest = async (
  id: number,
  body: FulfilAdminRequestBody = {}
): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(buildAdminRequestActionUrl(API.adminRequests, id, 'fulfil'), {
    method: 'POST',
    body: JSON.stringify(buildFulfilAdminRequestBody(body)),
  });
};

export const rejectAdminRequest = async (
  id: number,
  body: RejectAdminRequestBody = {}
): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(buildAdminRequestActionUrl(API.adminRequests, id, 'reject'), {
    method: 'POST',
    body: JSON.stringify(buildRejectAdminRequestBody(body)),
  });
};

// Authentication functions
export const login = async (credentials: LoginCredentials): Promise<AuthResponse> => {
  return fetchJSON<AuthResponse>(API.login, {
    method: 'POST',
    body: JSON.stringify(credentials),
  });
};

export const logout = async (): Promise<AuthResponse> => {
  return fetchJSON<AuthResponse>(API.logout, {
    method: 'POST',
  });
};

export const checkAuth = async (): Promise<AuthResponse> => {
  return fetchJSON<AuthResponse>(API.authCheck);
};

// Settings API functions
export const getSettings = async (): Promise<SettingsResponse> => {
  return fetchJSON<SettingsResponse>(API.settings);
};

export const getSettingsTab = async (tabName: string): Promise<SettingsTab> => {
  return fetchJSON<SettingsTab>(`${API.settings}/${tabName}`);
};

export const updateSettings = async (
  tabName: string,
  values: Record<string, unknown>
): Promise<UpdateResult> => {
  return fetchJSON<UpdateResult>(`${API.settings}/${tabName}`, {
    method: 'PUT',
    body: JSON.stringify(values),
  });
};

export const executeSettingsAction = async (
  tabName: string,
  actionKey: string,
  currentValues?: Record<string, unknown>
): Promise<ActionResult> => {
  try {
    return await fetchJSON<ActionResult>(`${API.settings}/${tabName}/action/${actionKey}`, {
      method: 'POST',
      body: currentValues ? JSON.stringify(currentValues) : undefined,
    });
  } catch (error) {
    const mapped = mapApiErrorToActionResult(error);
    if (mapped) {
      return mapped;
    }
    throw error;
  }
};

// Onboarding API functions

export interface OnboardingStepCondition {
  field: string;
  value: unknown;
}

export interface OnboardingStep {
  id: string;
  title: string;
  tab: string;
  fields: import('../types/settings').SettingsField[];
  showWhen?: OnboardingStepCondition[];  // Array of conditions (all must be true)
  optional?: boolean;
}

export interface OnboardingConfig {
  steps: OnboardingStep[];
  values: Record<string, unknown>;
  complete: boolean;
}

export const getOnboarding = async (): Promise<OnboardingConfig> => {
  return fetchJSON<OnboardingConfig>(`${API_BASE}/onboarding`);
};

export const saveOnboarding = async (
  values: Record<string, unknown>
): Promise<{ success: boolean; message: string }> => {
  return fetchJSON<{ success: boolean; message: string }>(`${API_BASE}/onboarding`, {
    method: 'POST',
    body: JSON.stringify(values),
  });
};

export const skipOnboarding = async (): Promise<{ success: boolean; message: string }> => {
  return fetchJSON<{ success: boolean; message: string }>(`${API_BASE}/onboarding/skip`, {
    method: 'POST',
  });
};

// Release source API functions

// Get available release sources from plugin registry
export const getReleaseSources = async (): Promise<ReleaseSource[]> => {
  return fetchJSON<ReleaseSource[]>(`${API_BASE}/release-sources`);
};

// Search for releases of a book
export const getReleases = async (
  provider: string,
  bookId: string,
  source?: string,
  title?: string,
  author?: string,
  expandSearch?: boolean,
  languages?: string[],
  contentType?: string,
  manualQuery?: string,
  indexers?: string[]
): Promise<ReleasesResponse> => {
  const params = new URLSearchParams({
    provider,
    book_id: bookId,
  });
  if (source) {
    params.set('source', source);
  }
  if (title) {
    params.set('title', title);
  }
  if (author) {
    params.set('author', author);
  }
  if (expandSearch) {
    params.set('expand_search', 'true');
  }
  if (languages && languages.length > 0) {
    params.set('languages', languages.join(','));
  }
  if (contentType) {
    params.set('content_type', contentType);
  }
  if (manualQuery) {
    params.set('manual_query', manualQuery);
  }
  if (indexers && indexers.length > 0) {
    params.set('indexers', indexers.join(','));
  }
  // Let the backend control timeouts for release searches (can be long-running).
  return fetchJSON<ReleasesResponse>(`${API_BASE}/releases?${params.toString()}`, {}, null);
};

// Admin user management API

export type AdminAuthSource = 'builtin' | 'oidc' | 'proxy' | 'cwa';

export interface AdminUserEditCapabilities {
  authSource: AdminAuthSource;
  canSetPassword: boolean;
  canEditRole: boolean;
  canEditEmail: boolean;
  canEditDisplayName: boolean;
}

export interface AdminUser {
  id: number;
  username: string;
  email: string | null;
  display_name: string | null;
  role: string;
  auth_source: AdminAuthSource;
  is_active: boolean;
  oidc_subject: string | null;
  created_at: string;
  edit_capabilities: AdminUserEditCapabilities;
  settings?: Record<string, unknown>;
}

export interface SelfUserEditContext {
  user: AdminUser;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  notificationPreferences: DeliveryPreferencesResponse | null;
  userOverridableKeys: string[];
  visibleUserSettingsSections?: string[];
}

export const getAdminUsers = async (): Promise<AdminUser[]> => {
  return fetchJSON<AdminUser[]>(`${API_BASE}/admin/users`);
};

export const getAdminUser = async (userId: number): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/admin/users/${userId}`);
};

export const createAdminUser = async (
  data: { username: string; password: string; email?: string; display_name?: string; role?: string }
): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/admin/users`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
};

export const updateAdminUser = async (
  userId: number,
  data: Partial<Pick<AdminUser, 'role' | 'email' | 'display_name'>> & {
    password?: string;
    settings?: Record<string, unknown>;
  }
): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/admin/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
};

export const deleteAdminUser = async (userId: number): Promise<{ success: boolean }> => {
  return fetchJSON<{ success: boolean }>(`${API_BASE}/admin/users/${userId}`, {
    method: 'DELETE',
  });
};

export interface CwaUserSyncResult {
  success: boolean;
  message: string;
  created: number;
  updated: number;
  total: number;
}

export const syncAdminCwaUsers = async (): Promise<CwaUserSyncResult> => {
  return fetchJSON<CwaUserSyncResult>(`${API_BASE}/admin/users/sync-cwa`, {
    method: 'POST',
  });
};

export interface DownloadDefaults {
  BOOKS_OUTPUT_MODE: string;
  DESTINATION: string;
  DESTINATION_AUDIOBOOK: string;
  BOOKLORE_LIBRARY_ID: string;
  BOOKLORE_PATH_ID: string;
  EMAIL_RECIPIENT: string;
  OIDC_ADMIN_GROUP: string;
  OIDC_USE_ADMIN_GROUP: boolean;
  OIDC_AUTO_PROVISION: boolean;
}

export const getDownloadDefaults = async (): Promise<DownloadDefaults> => {
  return fetchJSON<DownloadDefaults>(`${API_BASE}/admin/download-defaults`);
};

export interface BookloreOption {
  value: string;
  label: string;
  childOf?: string;
}

export interface BookloreOptions {
  libraries: BookloreOption[];
  paths: BookloreOption[];
}

export const getBookloreOptions = async (): Promise<BookloreOptions> => {
  return fetchJSON<BookloreOptions>(`${API_BASE}/admin/booklore-options`);
};

export interface DeliveryPreferencesResponse {
  tab: string;
  keys: string[];
  fields: import('../types/settings').SettingsField[];
  globalValues: Record<string, unknown>;
  userOverrides: Record<string, unknown>;
  effective: Record<string, { value: unknown; source: string }>;
}

export const getAdminDeliveryPreferences = async (
  userId: number
): Promise<DeliveryPreferencesResponse> => {
  return fetchJSON<DeliveryPreferencesResponse>(`${API_BASE}/admin/users/${userId}/delivery-preferences`);
};

export const getAdminNotificationPreferences = async (
  userId: number
): Promise<DeliveryPreferencesResponse> => {
  return fetchJSON<DeliveryPreferencesResponse>(`${API_BASE}/admin/users/${userId}/notification-preferences`);
};

export const testAdminUserNotificationPreferences = async (
  userId: number,
  routes: Array<Record<string, unknown>>
): Promise<import('../types/settings').ActionResult> => {
  try {
    return await fetchJSON<import('../types/settings').ActionResult>(
      `${API_BASE}/admin/users/${userId}/notification-preferences/test`,
      {
        method: 'POST',
        body: JSON.stringify({ USER_NOTIFICATION_ROUTES: routes }),
      }
    );
  } catch (error) {
    const mapped = mapApiErrorToActionResult(error);
    if (mapped) {
      return mapped;
    }
    throw error;
  }
};

export const testSelfNotificationPreferences = async (
  routes: Array<Record<string, unknown>>
): Promise<import('../types/settings').ActionResult> => {
  try {
    return await fetchJSON<import('../types/settings').ActionResult>(
      `${API_BASE}/users/me/notification-preferences/test`,
      {
        method: 'POST',
        body: JSON.stringify({ USER_NOTIFICATION_ROUTES: routes }),
      }
    );
  } catch (error) {
    const mapped = mapApiErrorToActionResult(error);
    if (mapped) {
      return mapped;
    }
    throw error;
  }
};

export interface SettingsOverrideUserDetail {
  userId: number;
  username: string;
  value: unknown;
}

export interface SettingsOverrideKeySummary {
  count: number;
  users: SettingsOverrideUserDetail[];
}

export interface SettingsOverridesSummaryResponse {
  tab: string;
  keys: Record<string, SettingsOverrideKeySummary>;
}

export const getAdminSettingsOverridesSummary = async (
  tabName: string
): Promise<SettingsOverridesSummaryResponse> => {
  return fetchJSON<SettingsOverridesSummaryResponse>(`${API_BASE}/admin/settings/overrides-summary?tab=${encodeURIComponent(tabName)}`);
};

export const getSelfUserEditContext = async (): Promise<SelfUserEditContext> => {
  return fetchJSON<SelfUserEditContext>(`${API_BASE}/users/me/edit-context`);
};

export const updateSelfUser = async (
  data: Partial<Pick<AdminUser, 'email' | 'display_name'>> & {
    password?: string;
    settings?: Record<string, unknown>;
  }
): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/users/me`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
};
