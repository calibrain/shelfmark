import { describe, it, expect } from 'vitest';

import { applyRequestUpdateEvent, upsertRequestRecord } from '../hooks/useRequests.helpers';
import { RequestRecord } from '../types/index';

const makeRequest = (overrides: Partial<RequestRecord> = {}): RequestRecord => ({
  id: 1,
  user_id: 100,
  status: 'pending',
  source_hint: 'prowlarr',
  content_type: 'ebook',
  request_level: 'release',
  policy_mode: 'request_release',
  book_data: { title: 'Request Book', author: 'Request Author' },
  release_data: { source: 'prowlarr', source_id: 'rel-1', title: 'Request Book.epub' },
  note: null,
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-02-13T10:00:00Z',
  updated_at: '2026-02-13T10:00:00Z',
  ...overrides,
});

describe('useRequests helpers', () => {
  it('upsertRequestRecord prepends new items and keeps latest-first order', () => {
    const older = makeRequest({ id: 1, created_at: '2026-02-13T09:00:00Z' });
    const newer = makeRequest({ id: 2, created_at: '2026-02-13T11:00:00Z' });

    const result = upsertRequestRecord([older], newer);

    expect(result.map((row) => row.id)).toEqual([2, 1]);
  });

  it('upsertRequestRecord replaces existing items by id', () => {
    const base = makeRequest({ id: 8, status: 'pending' });
    const updated = makeRequest({ id: 8, status: 'fulfilled' });

    const result = upsertRequestRecord([base], updated);

    expect(result.length).toBe(1);
    expect(result[0].id).toBe(8);
    expect(result[0].status).toBe('fulfilled');
  });

  it('applyRequestUpdateEvent updates matching request status', () => {
    const base = makeRequest({ id: 4, status: 'pending' });

    const result = applyRequestUpdateEvent([base], {
      request_id: 4,
      status: 'rejected',
    });

    expect(result.found).toBe(true);
    expect(result.records[0].status).toBe('rejected');
  });

  it('applyRequestUpdateEvent no-ops when request is missing', () => {
    const base = makeRequest({ id: 10, status: 'pending' });

    const result = applyRequestUpdateEvent([base], {
      request_id: 11,
      status: 'cancelled',
    });

    expect(result.found).toBe(false);
    expect(result.records.length).toBe(1);
    expect(result.records[0].status).toBe('pending');
  });
});
