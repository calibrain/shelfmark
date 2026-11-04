import type { ActivityDismissTarget } from './ActivitySidebar';
import type { ActivityItem } from './activityTypes';

// A fulfilled request whose download is still under way is not finished.
const ACTIVE_DELIVERY_STATES = new Set(['queued', 'resolving', 'locating', 'downloading']);

/**
 * Items the "Clear Completed" button dismisses: every finished download, plus the request
 * linked to it once that request is itself finished. The server only dismisses finished
 * requests and rejects the whole batch if one is still pending, which a restart can cause
 * by reopening the request of a download that already ended.
 *
 * Also every finished request card that has no download of its own: an approved request
 * nothing is still downloading for (auto-download closes a request whose book is already
 * in the library without downloading anything), or a cancelled one. Rejected requests
 * stay; they have their own view.
 */
export const getClearCompletedTargets = (
  visibleItems: ActivityItem[],
  requestItems: ActivityItem[],
): ActivityDismissTarget[] => {
  const pendingRequestIds = new Set<number>();
  requestItems.forEach((item) => {
    if (typeof item.requestId === 'number' && item.requestRecord?.status === 'pending') {
      pendingRequestIds.add(item.requestId);
    }
  });
  const targets: ActivityDismissTarget[] = [];
  const seen = new Set<string>();

  visibleItems.forEach((item) => {
    if (item.kind === 'request' && typeof item.requestId === 'number') {
      const record = item.requestRecord;
      const finished =
        record?.status === 'cancelled' ||
        (record?.status === 'fulfilled' &&
          !ACTIVE_DELIVERY_STATES.has(record.delivery_state ?? ''));
      const requestKey = `request:${item.requestId}`;
      if (finished && !pendingRequestIds.has(item.requestId) && !seen.has(requestKey)) {
        seen.add(requestKey);
        targets.push({ itemType: 'request', itemKey: requestKey });
      }
      return;
    }

    const isTerminalDownload =
      item.kind === 'download' &&
      (item.visualStatus === 'complete' ||
        item.visualStatus === 'error' ||
        item.visualStatus === 'cancelled');

    if (!isTerminalDownload || !item.downloadBookId) {
      return;
    }

    const downloadKey = `download:${item.downloadBookId}`;
    if (!seen.has(downloadKey)) {
      seen.add(downloadKey);
      targets.push({ itemType: 'download', itemKey: downloadKey });
    }

    if (item.requestId && !pendingRequestIds.has(item.requestId)) {
      const requestKey = `request:${item.requestId}`;
      if (!seen.has(requestKey)) {
        seen.add(requestKey);
        targets.push({ itemType: 'request', itemKey: requestKey });
      }
    }
  });

  return targets;
};
