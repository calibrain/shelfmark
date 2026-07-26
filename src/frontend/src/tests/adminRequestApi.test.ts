import { describe, it, expect } from 'vitest';

import {
  buildFulfilAdminRequestBody,
  buildRejectAdminRequestBody,
} from '../services/requestApiHelpers';

describe('admin request API client functions', () => {
  it('builds Book fulfil payload shape', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      admin_note: 'Approved',
    });

    expect(body).toEqual({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      admin_note: 'Approved',
    });
  });

  it('builds manual-approval fulfil payload without release data', () => {
    const body = buildFulfilAdminRequestBody({
      manual_approval: true,
      admin_note: 'Handled manually',
    });

    expect(body).toEqual({
      manual_approval: true,
      admin_note: 'Handled manually',
    });
  });

  it('builds reject payload shape', () => {
    const body = buildRejectAdminRequestBody({
      admin_note: 'No suitable release found',
    });

    expect(body).toEqual({
      admin_note: 'No suitable release found',
    });
  });
});
