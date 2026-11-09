import type { ButtonStateInfo, RequestPolicyMode } from '../types';

/**
 * Whether the release modal offers a book-level request ("Add to requests"). Admins always get
 * it. Anyone else gets it only when requests are on, the setting lets users who can download
 * also ask for a book without choosing a release, and their policy for this content type is
 * Download. The server turns a book-level request into a pending one only in Download mode, so
 * the action would be refused (Blocked) or mean something else (Request Release) otherwise.
 */
export const canRequestBookFromReleaseModal = ({
  isBrowseFulfilMode,
  isAdmin,
  requestsEnabled,
  allowBookRequestWithDownload,
  bookRequestMode,
}: {
  isBrowseFulfilMode: boolean;
  isAdmin: boolean;
  requestsEnabled: boolean;
  allowBookRequestWithDownload: boolean;
  bookRequestMode: RequestPolicyMode;
}): boolean => {
  if (isBrowseFulfilMode) return false;
  if (isAdmin) return true;
  return requestsEnabled && allowBookRequestWithDownload && bookRequestMode === 'download';
};

/**
 * Whether the activity sidebar shows a Requests tab. Admins always see it. A user sees it when
 * requests are on and either default asks for requests, or when they may request a book
 * alongside downloads, since those requests then need a place to be followed and cancelled.
 */
export const shouldShowRequestsTab = ({
  isAdmin,
  isAuthenticated,
  requestsEnabled,
  defaults,
  allowBookRequestWithDownload,
}: {
  isAdmin: boolean;
  isAuthenticated: boolean;
  requestsEnabled: boolean;
  defaults: { ebook: RequestPolicyMode; audiobook: RequestPolicyMode } | null;
  allowBookRequestWithDownload: boolean;
}): boolean => {
  if (isAdmin) return true;
  if (!isAuthenticated || !requestsEnabled || !defaults) return false;
  if (allowBookRequestWithDownload) return true;
  return !(defaults.ebook === 'download' && defaults.audiobook === 'download');
};

export const applyDirectPolicyModeToButtonState = (
  baseState: ButtonStateInfo,
  mode: RequestPolicyMode,
): ButtonStateInfo => {
  if (baseState.state !== 'download') {
    return baseState;
  }

  if (mode === 'blocked') {
    return { text: 'Unavailable', state: 'blocked' };
  }

  if (mode === 'request_release') {
    return { text: 'Request', state: 'download' };
  }

  return baseState;
};

export const applyUniversalPolicyModeToButtonState = (
  baseState: ButtonStateInfo,
  mode: RequestPolicyMode,
): ButtonStateInfo => {
  if (baseState.state !== 'download') {
    return baseState;
  }

  if (mode === 'request_book') {
    return { text: 'Request', state: 'download' };
  }

  if (mode === 'blocked') {
    return { text: 'Unavailable', state: 'blocked' };
  }

  return { ...baseState, text: 'Get' };
};
