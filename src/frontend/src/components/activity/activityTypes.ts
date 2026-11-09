import type { RequestRecord } from '../../types';

type ActivityKind = 'download' | 'request';

export type ActivityVisualStatus =
  | 'queued'
  | 'resolving'
  | 'locating'
  | 'downloading'
  | 'complete'
  | 'error'
  | 'cancelled'
  | 'pending'
  | 'fulfilled'
  | 'rejected';

export interface FormatPill {
  label: string;
  kind: 'audiobook' | 'ebook';
}

export interface ActivityItem {
  id: string;
  kind: ActivityKind;
  visualStatus: ActivityVisualStatus;

  title: string;
  author: string;
  preview?: string;

  metaLine: string;

  statusLabel: string;
  statusDetail?: string;
  adminNote?: string;

  progress?: number;
  progressAnimated?: boolean;
  sizeRaw?: string;
  downloads?: number;

  timestamp: number;
  username?: string;

  downloadBookId?: string;
  downloadRetryAvailable?: boolean;
  downloadPath?: string;
  requestId?: number;
  requestLevel?: 'book' | 'release';
  requestNote?: string;
  requestRecord?: RequestRecord;
  // Fork: format shown as a pill coloured by kind, e.g. EPUB in the ebook colour.
  formatPill?: FormatPill;
  // Fork: the download picked up work that was already under way (shown as a pill).
  reattached?: boolean;
  // Fork Rejected view only: whether the admin hid this rejected request there.
  hiddenInRejected?: boolean;
}
