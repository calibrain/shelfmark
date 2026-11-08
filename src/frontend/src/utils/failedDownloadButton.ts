import type { Book, ButtonStateInfo } from '../types';

/**
 * The button for a release whose last download failed.
 *
 * When the queue cannot resume a failed download of a request, the request is reopened so a
 * release can be picked again, and the one that failed may be fine (its client was down or
 * refused the login). So it is offered again, like a download the queue can retry. Any other
 * failure with nothing to retry from stays marked as failed.
 */
export function failedDownloadButtonState(entry: Book): ButtonStateInfo {
  return entry.retry_available === true || (entry.request_id ?? 0) > 0
    ? { text: 'Retry', state: 'download' }
    : { text: 'Failed', state: 'error' };
}
