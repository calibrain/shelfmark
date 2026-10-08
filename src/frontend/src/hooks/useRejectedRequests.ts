import { useCallback, useRef, useState } from 'react';

import { requestToActivityItem } from '../components/activity/activityMappers';
import type { ActivityItem } from '../components/activity/activityTypes';
import { listRejectedAdminRequests } from '../services/api';

interface UseRejectedRequestsResult {
  rejectedItems: ActivityItem[];
  rejectedLoading: boolean;
  loadRejected: () => Promise<void>;
}

// Rejected requests for the admin Rejected view. Unlike the activity snapshot this
// includes requests that were cleared from the list, which is where most of them are.
// Loaded from the events that change it (opening the view, a reopen), like History.
export const useRejectedRequests = (): UseRejectedRequestsResult => {
  const [rejectedItems, setRejectedItems] = useState<ActivityItem[]>([]);
  const [rejectedLoading, setRejectedLoading] = useState(false);
  const latestLoad = useRef(0);

  const loadRejected = useCallback(async () => {
    latestLoad.current += 1;
    const loadId = latestLoad.current;
    setRejectedLoading(true);
    try {
      const records = await listRejectedAdminRequests();
      if (loadId !== latestLoad.current) {
        return;
      }
      const items = records.map((record) => requestToActivityItem(record, 'admin'));
      items.sort((a, b) => b.timestamp - a.timestamp);
      setRejectedItems(items);
    } catch (error) {
      console.error('Loading rejected requests failed:', error);
      if (loadId === latestLoad.current) {
        setRejectedItems([]);
      }
    } finally {
      if (loadId === latestLoad.current) {
        setRejectedLoading(false);
      }
    }
  }, []);

  return { rejectedItems, rejectedLoading, loadRejected };
};
