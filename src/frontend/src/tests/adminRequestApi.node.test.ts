import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildAdminRequestActionUrl,
  buildFulfilAdminRequestBody,
  buildRejectAdminRequestBody,
  buildRequestListUrl,
} from '../services/requestApiHelpers.js';

describe('admin request API client functions', () => {
  it('builds list URL query params correctly', () => {
    const url = buildRequestListUrl('/api/admin/requests', {
      status: 'pending',
      limit: 10,
      offset: 5,
    });
    assert.equal(url, '/api/admin/requests?status=pending&limit=10&offset=5');
  });

  it('returns bare list URL when no params are provided', () => {
    const url = buildRequestListUrl('/api/admin/requests');
    assert.equal(url, '/api/admin/requests');
  });

  it('builds fulfil endpoint URL and payload shape', () => {
    const url = buildAdminRequestActionUrl('/api/admin/requests', 42, 'fulfil');
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      admin_note: 'Approved',
    });

    assert.equal(url, '/api/admin/requests/42/fulfil');
    assert.deepEqual(body, {
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      admin_note: 'Approved',
    });
  });

  it('builds manual-approval fulfil payload without release data', () => {
    const body = buildFulfilAdminRequestBody({
      manual_approval: true,
      admin_note: 'Handled manually',
    });

    assert.deepEqual(body, {
      manual_approval: true,
      admin_note: 'Handled manually',
    });
  });

  it('builds reject endpoint URL and payload shape', () => {
    const url = buildAdminRequestActionUrl('/api/admin/requests', 51, 'reject');
    const body = buildRejectAdminRequestBody({
      admin_note: 'No suitable release found',
    });

    assert.equal(url, '/api/admin/requests/51/reject');
    assert.deepEqual(body, {
      admin_note: 'No suitable release found',
    });
  });
});
