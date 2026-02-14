import { ActivityVisualStatus } from './activityTypes';

export const STATUS_ACCENT_CLASSES: Record<ActivityVisualStatus, string> = {
  queued: 'border-l-amber-500',
  pending: 'border-l-amber-500',
  resolving: 'border-l-indigo-500',
  locating: 'border-l-teal-500',
  downloading: 'border-l-sky-500',
  complete: 'border-l-green-500',
  fulfilled: 'border-l-green-500',
  error: 'border-l-red-500',
  rejected: 'border-l-red-500',
  cancelled: 'border-l-gray-400',
};

export const STATUS_LABELS: Record<ActivityVisualStatus, string> = {
  queued: 'Queued',
  pending: 'Pending',
  resolving: 'Resolving',
  locating: 'Locating files',
  downloading: 'Downloading',
  complete: 'Complete',
  fulfilled: 'Fulfilled',
  error: 'Error',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
};

export interface StatusBadgeStyle {
  bg: string;
  text: string;
  waveColor?: string;
  fillColor?: string;
}

export const STATUS_BADGE_STYLES: Record<ActivityVisualStatus, StatusBadgeStyle> = {
  queued: {
    bg: 'bg-amber-500/15',
    text: 'text-amber-700 dark:text-amber-300',
    waveColor: 'rgba(217, 119, 6, 0.3)',
    fillColor: 'rgba(217, 119, 6, 0.4)',
  },
  pending: {
    bg: 'bg-amber-500/15',
    text: 'text-amber-700 dark:text-amber-300',
  },
  resolving: {
    bg: 'bg-indigo-500/15',
    text: 'text-indigo-700 dark:text-indigo-300',
    waveColor: 'rgba(79, 70, 229, 0.3)',
    fillColor: 'rgba(79, 70, 229, 0.4)',
  },
  locating: {
    bg: 'bg-teal-500/15',
    text: 'text-teal-700 dark:text-teal-300',
    waveColor: 'rgba(13, 148, 136, 0.3)',
    fillColor: 'rgba(13, 148, 136, 0.4)',
  },
  downloading: {
    bg: 'bg-sky-500/15',
    text: 'text-sky-700 dark:text-sky-300',
    waveColor: 'rgba(2, 132, 199, 0.3)',
    fillColor: 'rgba(2, 132, 199, 0.4)',
  },
  complete: {
    bg: 'bg-green-500/15',
    text: 'text-green-700 dark:text-green-300',
  },
  fulfilled: {
    bg: 'bg-green-500/15',
    text: 'text-green-700 dark:text-green-300',
  },
  error: {
    bg: 'bg-red-500/15',
    text: 'text-red-700 dark:text-red-300',
  },
  rejected: {
    bg: 'bg-red-500/15',
    text: 'text-red-700 dark:text-red-300',
  },
  cancelled: {
    bg: 'bg-gray-500/15',
    text: 'text-gray-600 dark:text-gray-400',
  },
};

export const STATUS_TOOLTIP_CLASSES: Record<ActivityVisualStatus, string> = {
  queued: 'bg-amber-50 text-amber-800 border border-amber-300/50 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-700/50',
  pending: 'bg-amber-50 text-amber-800 border border-amber-300/50 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-700/50',
  resolving: 'bg-indigo-50 text-indigo-800 border border-indigo-300/50 dark:bg-indigo-950 dark:text-indigo-200 dark:border-indigo-700/50',
  locating: 'bg-teal-50 text-teal-800 border border-teal-300/50 dark:bg-teal-950 dark:text-teal-200 dark:border-teal-700/50',
  downloading: 'bg-sky-50 text-sky-800 border border-sky-300/50 dark:bg-sky-950 dark:text-sky-200 dark:border-sky-700/50',
  complete: 'bg-green-50 text-green-800 border border-green-300/50 dark:bg-green-950 dark:text-green-200 dark:border-green-700/50',
  fulfilled: 'bg-green-50 text-green-800 border border-green-300/50 dark:bg-green-950 dark:text-green-200 dark:border-green-700/50',
  error: 'bg-red-50 text-red-800 border border-red-300/50 dark:bg-red-950 dark:text-red-200 dark:border-red-700/50',
  rejected: 'bg-red-50 text-red-800 border border-red-300/50 dark:bg-red-950 dark:text-red-200 dark:border-red-700/50',
  cancelled: 'bg-gray-50 text-gray-700 border border-gray-300/50 dark:bg-gray-900 dark:text-gray-300 dark:border-gray-600/50',
};

export const isActiveDownloadStatus = (status: ActivityVisualStatus): boolean =>
  status === 'queued' || status === 'resolving' || status === 'locating' || status === 'downloading';

const clampPercent = (value: number): number => Math.max(0, Math.min(100, value));

export const getProgressConfig = (
  status: ActivityVisualStatus,
  progress?: number
): { percent: number; color: string; animated: boolean } => {
  switch (status) {
    case 'queued':
      return { percent: 5, color: 'bg-amber-600', animated: true };
    case 'resolving':
      return { percent: 15, color: 'bg-indigo-600', animated: true };
    case 'locating':
      return { percent: 90, color: 'bg-teal-600', animated: true };
    case 'downloading': {
      const numericProgress = typeof progress === 'number' ? clampPercent(progress) : 0;
      return {
        percent: clampPercent(20 + numericProgress * 0.8),
        color: 'bg-sky-600',
        animated: true,
      };
    }
    case 'complete':
    case 'fulfilled':
      return { percent: 100, color: 'bg-green-600', animated: false };
    case 'error':
    case 'rejected':
      return { percent: 100, color: 'bg-red-600', animated: false };
    case 'cancelled':
      return { percent: 100, color: 'bg-gray-500', animated: false };
    case 'pending':
    default:
      return { percent: 0, color: 'bg-amber-600', animated: false };
  }
};
