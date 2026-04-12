import { useEffect, useRef } from 'react';

import type { AppConfig, StatusData } from '../../types';
import { withBasePath } from '../../utils/basePath';

interface UseStatusChangeNotificationsOptions {
  currentStatus: StatusData;
  config: AppConfig | null;
  showToast: (message: string, type: 'info' | 'success' | 'error') => void;
  openDownloadsSidebar: () => void;
  bookToReleaseMap: Record<string, string[]>;
  markBookCompleted: (bookId: string) => void;
}

export const useStatusChangeNotifications = ({
  currentStatus,
  config,
  showToast,
  openDownloadsSidebar,
  bookToReleaseMap,
  markBookCompleted,
}: UseStatusChangeNotificationsOptions): void => {
  const prevStatusRef = useRef<StatusData>({});

  useEffect(() => {
    const prevStatus = prevStatusRef.current;
    if (!prevStatus || Object.keys(prevStatus).length === 0) {
      prevStatusRef.current = currentStatus;
      return;
    }

    const autoDownloadContentTypes = Array.isArray(config?.download_to_browser_content_types)
      ? config.download_to_browser_content_types
      : [];
    const canAutoDownloadContentType = (downloadContentType?: string): boolean => {
      const contentTypeKey =
        (downloadContentType || '').trim().toLowerCase() === 'audiobook' ? 'audiobook' : 'book';
      return autoDownloadContentTypes.includes(contentTypeKey);
    };

    const prevQueued = prevStatus.queued || {};
    const currQueued = currentStatus.queued || {};
    let shouldOpenDownloadsSidebar = false;
    Object.keys(currQueued).forEach((bookId) => {
      if (!prevQueued[bookId]) {
        const book = currQueued[bookId];
        showToast(`${book.title || 'Book'} added to queue`, 'info');
        if (config?.auto_open_downloads_sidebar !== false) {
          shouldOpenDownloadsSidebar = true;
        }
      }
    });
    if (shouldOpenDownloadsSidebar) {
      openDownloadsSidebar();
    }

    const prevDownloading = prevStatus.downloading || {};
    const currDownloading = currentStatus.downloading || {};
    Object.keys(currDownloading).forEach((bookId) => {
      if (!prevDownloading[bookId]) {
        const book = currDownloading[bookId];
        showToast(`${book.title || 'Book'} started downloading`, 'info');
      }
    });

    const prevComplete = prevStatus.complete || {};
    const currComplete = currentStatus.complete || {};
    Object.keys(currComplete).forEach((bookId) => {
      if (!prevComplete[bookId]) {
        const book = currComplete[bookId];
        showToast(`${book.title || 'Book'} completed`, 'success');

        if (book.download_path && canAutoDownloadContentType(book.content_type)) {
          const link = document.createElement('a');
          link.href = withBasePath(`/api/localdownload?id=${encodeURIComponent(bookId)}`);
          link.download = '';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        }

        Object.entries(bookToReleaseMap).forEach(([metadataBookId, releaseIds]) => {
          if (releaseIds.includes(bookId)) {
            markBookCompleted(metadataBookId);
          }
        });
      }
    });

    const prevError = prevStatus.error || {};
    const currError = currentStatus.error || {};
    Object.keys(currError).forEach((bookId) => {
      if (!prevError[bookId]) {
        const book = currError[bookId];
        const errorMsg = book.status_message || 'Download failed';
        showToast(`${book.title || 'Book'}: ${errorMsg}`, 'error');
      }
    });

    prevStatusRef.current = currentStatus;
  }, [bookToReleaseMap, config, currentStatus, markBookCompleted, openDownloadsSidebar, showToast]);
};
