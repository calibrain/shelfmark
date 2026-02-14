import { CreateRequestPayload } from '../types';

export const MAX_REQUEST_NOTE_LENGTH = 1000;

const toText = (value: unknown, fallback: string): string => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return fallback;
};

export const formatSourceLabel = (value: unknown): string => {
  const source = String(value || '').trim();
  if (!source) {
    return 'Unknown source';
  }
  return source
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

export interface RequestConfirmationPreview {
  title: string;
  author: string;
  preview: string;
  releaseLine: string;
}

export const buildRequestConfirmationPreview = (
  payload: CreateRequestPayload
): RequestConfirmationPreview => {
  const bookData = payload.book_data || {};
  const releaseData = payload.release_data || {};
  const requestLevel = payload.context?.request_level;

  return {
    title: toText(bookData.title ?? releaseData.title, 'Untitled'),
    author: toText(bookData.author ?? releaseData.author, 'Unknown author'),
    preview:
      typeof bookData.preview === 'string'
        ? bookData.preview
        : typeof releaseData.preview === 'string'
          ? releaseData.preview
          : '',
    releaseLine:
      requestLevel === 'release'
        ? [
            typeof releaseData.format === 'string' && releaseData.format
              ? releaseData.format.toUpperCase()
              : null,
            typeof releaseData.size === 'string' && releaseData.size ? releaseData.size : null,
            formatSourceLabel(releaseData.source || payload.context?.source),
          ]
            .filter(Boolean)
            .join(' | ')
        : '',
  };
};

export const truncateRequestNote = (
  value: string,
  maxLength: number = MAX_REQUEST_NOTE_LENGTH
): string => value.slice(0, maxLength);

export const applyRequestNoteToPayload = (
  payload: CreateRequestPayload,
  note: string,
  allowNotes: boolean
): CreateRequestPayload => {
  const trimmedNote = note.trim();
  const nextPayload: CreateRequestPayload = {
    ...payload,
  };

  if (allowNotes && trimmedNote) {
    nextPayload.note = trimmedNote;
  } else {
    delete nextPayload.note;
  }

  return nextPayload;
};
