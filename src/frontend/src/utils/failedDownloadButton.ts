import type { Book, ButtonStateInfo } from '../types';

const RETRY: ButtonStateInfo = { text: 'Retry', state: 'download' };
const FAILED: ButtonStateInfo = { text: 'Failed', state: 'error' };

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
    ? { ...RETRY }
    : { ...FAILED };
}

/**
 * The button for a book with several tracked releases once another of them is found failed.
 *
 * A failure that can be retried wins over one that can't, in whichever order they are found, so
 * the book keeps Retry while any of its failed releases can be tried again.
 */
export function addFailedRelease(best: ButtonStateInfo, entry: Book): ButtonStateInfo {
  if (best.state === RETRY.state && best.text === RETRY.text) {
    return best;
  }
  const failed = failedDownloadButtonState(entry);
  return failed.text === RETRY.text || best.state === 'download' ? failed : best;
}

/**
 * The reopened request a failed release belongs to, if any.
 *
 * Retry on such a release is queued through that request, so the download fulfils it (#1681);
 * a failed release with no request behind it is retried as a plain download.
 */
export function retryRequestId(entry: Book | undefined): number | undefined {
  const requestId = entry?.request_id ?? 0;
  return requestId > 0 ? requestId : undefined;
}
