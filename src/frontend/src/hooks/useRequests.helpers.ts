import type { RequestRecord } from '../types';

type RequestUpdateStatus = RequestRecord['status'];

interface RequestUpdateEventPayload {
  request_id: number;
  status: RequestUpdateStatus;
}

export const upsertRequestRecord = (
  records: RequestRecord[],
  updated: RequestRecord,
): RequestRecord[] => {
  const index = records.findIndex((record) => record.id === updated.id);
  if (index === -1) {
    return [updated, ...records].toSorted(
      (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
    );
  }

  const next = [...records];
  next[index] = updated;
  return next;
};

export const applyRequestUpdateEvent = (
  records: RequestRecord[],
  payload: RequestUpdateEventPayload,
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
