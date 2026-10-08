import { useCallback, useRef, useState } from 'react';

import { requestToActivityItem } from '../components/activity/activityMappers';
import type { ActivityItem } from '../components/activity/activityTypes';
import {
  listHiddenRejectedRequestIds,
  listRejectedAdminRequests,
  setRejectedRequestHidden,
} from '../services/api';

interface UseRejectedRequestsResult {
  rejectedItems: ActivityItem[];
  rejectedLoading: boolean;
  loadRejected: () => Promise<void>;
  setRejectedHidden: (requestId: number, hidden: boolean) => Promise<void>;
}

// Rejected requests for the admin Rejected view. Unlike the activity snapshot this
// includes requests that were cleared from the list, which is where most of them are.
// Each item says whether the admin hid it from this view (its own state, not the
// activity list's clear). Loaded from the events that change it, like History.
export const useRejectedRequests = (): UseRejectedRequestsResult => {
  const [rejectedItems, setRejectedItems] = useState<ActivityItem[]>([]);
  const [rejectedLoading, setRejectedLoading] = useState(false);
  const latestLoad = useRef(0);

  const loadRejected = useCallback(async () => {
    latestLoad.current += 1;
    const loadId = latestLoad.current;
    setRejectedLoading(true);
    try {
      const [records, hiddenIds] = await Promise.all([
        listRejectedAdminRequests(),
        listHiddenRejectedRequestIds(),
      ]);
      if (loadId !== latestLoad.current) {
        return;
      }
      const hidden = new Set(hiddenIds);
      const items = records.map((record) => {
        const item = requestToActivityItem(record, 'admin');
        item.hiddenInRejected = hidden.has(record.id);
        return item;
      });
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

  const setRejectedHidden = useCallback(async (requestId: number, hidden: boolean) => {
    try {
      await setRejectedRequestHidden(requestId, hidden);
    } catch (error) {
      console.error('Changing hidden state failed:', error);
      return;
    }
    setRejectedItems((current) =>
      current.map((item) =>
        item.requestId === requestId ? Object.assign({}, item, { hiddenInRejected: hidden }) : item,
      ),
    );
  }, []);

  return { rejectedItems, rejectedLoading, loadRejected, setRejectedHidden };
};
