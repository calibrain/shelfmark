import { isRetryingAutomatically } from './activityAutomation';
import type { ActivityItem } from './activityTypes';

export type ActivityCategoryKey =
  | 'needs_review'
  | 'auto_retry'
  | 'in_progress'
  | 'complete'
  | 'failed';

export type ActivityTabKey = 'all' | 'downloads' | 'requests' | 'history' | 'rejected';

export const getCategoryLabel = (key: ActivityCategoryKey, isAdmin: boolean): string => {
  if (key === 'needs_review') {
    // Fork: nothing here is waiting for a person; the automatic search found no match it trusts.
    return isAdmin ? 'No Match Yet' : 'Waiting';
  }
  if (key === 'auto_retry') {
    return 'Retrying Automatically';
  }
  if (key === 'in_progress') {
    return 'In Progress';
  }
  if (key === 'complete') {
    return 'Complete';
  }
  return 'Failed';
};

/** One line under a section header that says what, if anything, is expected of the reader. */
export const getCategoryHint = (key: ActivityCategoryKey, isAdmin: boolean): string | undefined => {
  if (!isAdmin) {
    return undefined;
  }
  if (key === 'needs_review') {
    return 'Searched automatically on every sync. Choose a release yourself, decline, or leave it.';
  }
  if (key === 'auto_retry') {
    return 'A download failed and another copy is being tried. Nothing to do.';
  }
  return undefined;
};

export const getVisibleCategoryOrder = (tab: ActivityTabKey): ActivityCategoryKey[] => {
  if (tab === 'downloads') {
    return ['in_progress', 'complete', 'failed'];
  }
  if (tab === 'history' || tab === 'rejected') {
    return [];
  }
  return ['needs_review', 'auto_retry', 'in_progress', 'complete', 'failed'];
};

export const getActivityCategory = (item: ActivityItem): ActivityCategoryKey => {
  if (item.kind === 'download') {
    if (
      item.visualStatus === 'queued' ||
      item.visualStatus === 'resolving' ||
      item.visualStatus === 'locating' ||
      item.visualStatus === 'downloading'
    ) {
      return 'in_progress';
    }
    if (item.visualStatus === 'complete') {
      return 'complete';
    }
    return 'failed';
  }

  const requestStatus = item.requestRecord?.status;
  if (requestStatus === 'pending' || item.visualStatus === 'pending') {
    return isRetryingAutomatically(item.requestRecord) ? 'auto_retry' : 'needs_review';
  }

  if (requestStatus === 'rejected' || requestStatus === 'cancelled') {
    return 'failed';
  }

  const deliveryState = item.requestRecord?.delivery_state;
  if (requestStatus === 'fulfilled' || item.visualStatus === 'fulfilled') {
    if (
      deliveryState === 'queued' ||
      deliveryState === 'resolving' ||
      deliveryState === 'locating' ||
      deliveryState === 'downloading'
    ) {
      return 'in_progress';
    }
    if (deliveryState === 'error' || deliveryState === 'cancelled') {
      return 'failed';
    }
    // Legacy fulfilled requests often have unknown/none delivery state because the
    // pre-refactor queue state was ephemeral. Treat as completed approval, not in-progress.
    return 'complete';
  }

  if (deliveryState === 'complete') {
    return 'complete';
  }
  if (deliveryState === 'error' || deliveryState === 'cancelled') {
    return 'failed';
  }
  return 'in_progress';
};
