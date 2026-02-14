import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  STATUS_ACCENT_CLASSES,
  STATUS_BADGE_STYLES,
  getProgressConfig,
  isActiveDownloadStatus,
} from '../components/activity/activityStyles.js';

describe('activityStyles', () => {
  it('maps accent classes for request and download statuses', () => {
    assert.equal(STATUS_ACCENT_CLASSES.queued, 'border-l-amber-500');
    assert.equal(STATUS_ACCENT_CLASSES.pending, 'border-l-amber-500');
    assert.equal(STATUS_ACCENT_CLASSES.downloading, 'border-l-sky-500');
    assert.equal(STATUS_ACCENT_CLASSES.fulfilled, 'border-l-green-500');
    assert.equal(STATUS_ACCENT_CLASSES.rejected, 'border-l-red-500');
  });

  it('exposes badge style entries for all key statuses', () => {
    assert.equal(STATUS_BADGE_STYLES.queued.bg, 'bg-amber-500/15');
    assert.equal(STATUS_BADGE_STYLES.downloading.text, 'text-sky-700 dark:text-sky-300');
    assert.equal(STATUS_BADGE_STYLES.rejected.bg, 'bg-red-500/15');
  });

  it('returns progress config values matching existing sidebar behavior', () => {
    assert.deepEqual(getProgressConfig('queued'), {
      percent: 5,
      color: 'bg-amber-600',
      animated: true,
    });
    assert.deepEqual(getProgressConfig('resolving'), {
      percent: 15,
      color: 'bg-indigo-600',
      animated: true,
    });
    assert.deepEqual(getProgressConfig('locating'), {
      percent: 90,
      color: 'bg-teal-600',
      animated: true,
    });

    const downloading = getProgressConfig('downloading', 60);
    assert.equal(downloading.percent, 68);
    assert.equal(downloading.color, 'bg-sky-600');
    assert.equal(downloading.animated, true);

    assert.deepEqual(getProgressConfig('complete'), {
      percent: 100,
      color: 'bg-green-600',
      animated: false,
    });
    assert.deepEqual(getProgressConfig('error'), {
      percent: 100,
      color: 'bg-red-600',
      animated: false,
    });
    assert.deepEqual(getProgressConfig('cancelled'), {
      percent: 100,
      color: 'bg-gray-500',
      animated: false,
    });
    assert.deepEqual(getProgressConfig('pending'), {
      percent: 0,
      color: 'bg-amber-600',
      animated: false,
    });
  });

  it('detects active download statuses only', () => {
    assert.equal(isActiveDownloadStatus('queued'), true);
    assert.equal(isActiveDownloadStatus('resolving'), true);
    assert.equal(isActiveDownloadStatus('locating'), true);
    assert.equal(isActiveDownloadStatus('downloading'), true);
    assert.equal(isActiveDownloadStatus('complete'), false);
    assert.equal(isActiveDownloadStatus('pending'), false);
  });
});
