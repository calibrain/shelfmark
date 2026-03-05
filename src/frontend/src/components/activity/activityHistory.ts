import type { ActivityItem } from './activityTypes.js';

export const dedupeHistoryItems = (items: ActivityItem[]): ActivityItem[] => {
  const requestIdsWithDownloadRows = new Set<number>();

  items.forEach((item) => {
    if (item.kind === 'download' && typeof item.requestId === 'number') {
      requestIdsWithDownloadRows.add(item.requestId);
    }
  });

  if (!requestIdsWithDownloadRows.size) {
    return items;
  }

  return items.filter((item) => {
    if (item.kind !== 'request' || typeof item.requestId !== 'number') {
      return true;
    }
    if (!requestIdsWithDownloadRows.has(item.requestId)) {
      return true;
    }

    const requestStatus = item.requestRecord?.status;
    return requestStatus !== 'fulfilled' && item.visualStatus !== 'fulfilled';
  });
};
