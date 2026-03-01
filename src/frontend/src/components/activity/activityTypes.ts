import { RequestRecord } from '../../types';

export type ActivityKind = 'download' | 'request';

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

  timestamp: number;
  username?: string;

  downloadBookId?: string;
  downloadPath?: string;
  requestId?: number;
  requestLevel?: 'book' | 'release';
  requestNote?: string;
  requestRecord?: RequestRecord;
}
