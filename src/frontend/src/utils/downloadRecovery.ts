import { StatusData } from '../types';

const ACTIVE_STATUS_BUCKETS = ['queued', 'resolving', 'locating', 'downloading'] as const;
const TERMINAL_STATUS_BUCKETS = ['complete', 'error', 'cancelled'] as const;
const TERMINAL_CONFIRMATION_WINDOW_SECONDS = 2;

export function wasDownloadQueuedAfterResponseError(
  status: StatusData,
  taskId: string,
  requestedAtSeconds: number
): boolean {
  for (const bucket of ACTIVE_STATUS_BUCKETS) {
    if (status[bucket]?.[taskId]) {
      return true;
    }
  }

  for (const bucket of TERMINAL_STATUS_BUCKETS) {
    const task = status[bucket]?.[taskId];
    if (!task) {
      continue;
    }

    if (
      typeof task.added_time === 'number'
      && task.added_time >= requestedAtSeconds - TERMINAL_CONFIRMATION_WINDOW_SECONDS
    ) {
      return true;
    }
  }

  return false;
}
