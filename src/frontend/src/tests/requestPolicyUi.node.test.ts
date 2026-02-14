import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { ButtonStateInfo } from '../types/index.js';
import {
  applyDirectPolicyModeToButtonState,
  applyUniversalPolicyModeToButtonState,
} from '../utils/requestPolicyUi.js';

describe('requestPolicyUi', () => {
  const baseDownload: ButtonStateInfo = { text: 'Download', state: 'download' };

  it('maps direct mode to request for request_release/request_book', () => {
    assert.deepEqual(applyDirectPolicyModeToButtonState(baseDownload, 'request_release'), {
      text: 'Request',
      state: 'download',
    });
    assert.deepEqual(applyDirectPolicyModeToButtonState(baseDownload, 'request_book'), {
      text: 'Request',
      state: 'download',
    });
  });

  it('maps direct mode to unavailable for blocked', () => {
    assert.deepEqual(applyDirectPolicyModeToButtonState(baseDownload, 'blocked'), {
      text: 'Unavailable',
      state: 'blocked',
    });
  });

  it('preserves non-download direct states', () => {
    const queued: ButtonStateInfo = { text: 'Queued', state: 'queued' };
    assert.equal(applyDirectPolicyModeToButtonState(queued, 'request_release'), queued);
  });

  it('maps universal mode to request only for request_book', () => {
    assert.deepEqual(applyUniversalPolicyModeToButtonState(baseDownload, 'request_book'), {
      text: 'Request',
      state: 'download',
    });
  });

  it('maps universal mode to get for download and request_release', () => {
    assert.deepEqual(applyUniversalPolicyModeToButtonState(baseDownload, 'download'), {
      text: 'Get',
      state: 'download',
    });
    assert.deepEqual(applyUniversalPolicyModeToButtonState(baseDownload, 'request_release'), {
      text: 'Get',
      state: 'download',
    });
  });

  it('maps universal mode to unavailable for blocked and preserves non-download states', () => {
    assert.deepEqual(applyUniversalPolicyModeToButtonState(baseDownload, 'blocked'), {
      text: 'Unavailable',
      state: 'blocked',
    });

    const complete: ButtonStateInfo = { text: 'Downloaded', state: 'complete' };
    assert.equal(applyUniversalPolicyModeToButtonState(complete, 'request_book'), complete);
  });
});
