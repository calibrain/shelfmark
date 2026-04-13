import { describe, it, expect } from 'vitest';

import {
  STATUS_ACCENT_CLASSES,
  STATUS_BADGE_STYLES,
  getProgressConfig,
  isActiveDownloadStatus,
} from '../components/activity/activityStyles';

describe('activityStyles', () => {
  it('maps accent classes for request and download statuses', () => {
    expect(STATUS_ACCENT_CLASSES.queued).toBe('border-l-amber-500');
    expect(STATUS_ACCENT_CLASSES.pending).toBe('border-l-amber-500');
    expect(STATUS_ACCENT_CLASSES.downloading).toBe('border-l-sky-500');
    expect(STATUS_ACCENT_CLASSES.fulfilled).toBe('border-l-green-500');
    expect(STATUS_ACCENT_CLASSES.rejected).toBe('border-l-red-500');
  });

  it('exposes badge style entries for all key statuses', () => {
    expect(STATUS_BADGE_STYLES.queued.bg).toBe('bg-amber-500/15');
    expect(STATUS_BADGE_STYLES.downloading.text).toBe('text-sky-700 dark:text-sky-300');
    expect(STATUS_BADGE_STYLES.rejected.bg).toBe('bg-red-500/15');
  });

  it('returns progress config values matching existing sidebar behavior', () => {
    expect(getProgressConfig('queued')).toEqual({
      percent: 5,
      color: 'bg-amber-600',
      animated: true,
    });
    expect(getProgressConfig('resolving')).toEqual({
      percent: 15,
      color: 'bg-indigo-600',
      animated: true,
    });
    expect(getProgressConfig('locating')).toEqual({
      percent: 90,
      color: 'bg-teal-600',
      animated: true,
    });

    const downloading = getProgressConfig('downloading', 60);
    expect(downloading.percent).toBe(68);
    expect(downloading.color).toBe('bg-sky-600');
    expect(downloading.animated).toBe(true);

    expect(getProgressConfig('complete')).toEqual({
      percent: 100,
      color: 'bg-green-600',
      animated: false,
    });
    expect(getProgressConfig('error')).toEqual({
      percent: 100,
      color: 'bg-red-600',
      animated: false,
    });
    expect(getProgressConfig('cancelled')).toEqual({
      percent: 100,
      color: 'bg-gray-500',
      animated: false,
    });
    expect(getProgressConfig('pending')).toEqual({
      percent: 0,
      color: 'bg-amber-600',
      animated: false,
    });
  });

  it('detects active download statuses only', () => {
    expect(isActiveDownloadStatus('queued')).toBe(true);
    expect(isActiveDownloadStatus('resolving')).toBe(true);
    expect(isActiveDownloadStatus('locating')).toBe(true);
    expect(isActiveDownloadStatus('downloading')).toBe(true);
    expect(isActiveDownloadStatus('complete')).toBe(false);
    expect(isActiveDownloadStatus('pending')).toBe(false);
  });
});
