import { RequestRecord } from '../types';

type RequestUpdateStatus = RequestRecord['status'];

export interface RequestUpdateEventPayload {
  request_id: number;
  status: RequestUpdateStatus;
}

const isValidRequestStatus = (value: unknown): value is RequestUpdateStatus => {
  return value === 'pending' || value === 'fulfilled' || value === 'rejected' || value === 'cancelled';
};

export const normalizeRequestUpdatePayload = (payload: unknown): RequestUpdateEventPayload | null => {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const row = payload as Record<string, unknown>;
  const requestId = row.request_id;
  const status = row.status;

  if (typeof requestId !== 'number' || !Number.isFinite(requestId) || !isValidRequestStatus(status)) {
    return null;
  }

  return {
    request_id: requestId,
    status,
  };
};

export const upsertRequestRecord = (
  records: RequestRecord[],
  updated: RequestRecord
): RequestRecord[] => {
  const index = records.findIndex((record) => record.id === updated.id);
  if (index === -1) {
    return [updated, ...records].sort(
      (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at)
    );
  }

  const next = [...records];
  next[index] = updated;
  return next;
};

export const applyRequestUpdateEvent = (
  records: RequestRecord[],
  payload: RequestUpdateEventPayload
): { records: RequestRecord[]; found: boolean } => {
  let found = false;
  const next = records.map((record) => {
    if (record.id !== payload.request_id) {
      return record;
    }
    found = true;
    return {
      ...record,
      status: payload.status,
      updated_at: new Date().toISOString(),
    };
  });

  return { records: next, found };
};
