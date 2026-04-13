import { describe, it, expect } from 'vitest';

import type { CreateRequestPayload } from '../types/index';
import {
  applyRequestNoteToPayload,
  buildRequestConfirmationPreview,
  MAX_REQUEST_NOTE_LENGTH,
  truncateRequestNote,
} from '../utils/requestConfirmation';

const releasePayload: CreateRequestPayload = {
  book_data: {
    title: 'Example Title',
    author: 'Example Author',
    preview: 'https://example.com/cover.jpg',
  },
  release_data: {
    source: 'prowlarr',
    format: 'epub',
    size: '2 MB',
  },
  context: {
    source: 'prowlarr',
    content_type: 'ebook',
    request_level: 'release',
  },
};

const bookPayload: CreateRequestPayload = {
  book_data: {
    title: 'Book Level',
    author: 'Book Author',
  },
  release_data: null,
  context: {
    source: '*',
    content_type: 'ebook',
    request_level: 'book',
  },
};

describe('requestConfirmation utilities', () => {
  it('builds release preview line for release-level payloads', () => {
    const preview = buildRequestConfirmationPreview(releasePayload);

    expect(preview.title).toBe('Example Title');
    expect(preview.author).toBe('Example Author');
    expect(preview.preview).toBe('https://example.com/cover.jpg');
    expect(preview.releaseLine).toBe('EPUB | 2 MB | Prowlarr');
    expect(preview.year).toBe('');
    expect(preview.seriesLine).toBe('');
  });

  it('omits release line for book-level payloads', () => {
    const preview = buildRequestConfirmationPreview(bookPayload);

    expect(preview.title).toBe('Book Level');
    expect(preview.author).toBe('Book Author');
    expect(preview.releaseLine).toBe('');
  });

  it('includes year and series info when present', () => {
    const payload: CreateRequestPayload = {
      book_data: {
        title: 'Dune',
        author: 'Frank Herbert',
        year: '1965',
        series_name: 'Dune Chronicles',
        series_position: 1,
        series_count: 6,
      },
      release_data: null,
      context: {
        source: '*',
        content_type: 'ebook',
        request_level: 'book',
      },
    };
    const preview = buildRequestConfirmationPreview(payload);

    expect(preview.year).toBe('1965');
    expect(preview.seriesLine).toBe('#1 of 6 in Dune Chronicles');
  });

  it('shows series position without count when count is absent', () => {
    const payload: CreateRequestPayload = {
      book_data: {
        title: 'Dune',
        author: 'Frank Herbert',
        series_name: 'Dune Chronicles',
        series_position: 1,
      },
      release_data: null,
      context: {
        source: '*',
        content_type: 'ebook',
        request_level: 'book',
      },
    };
    const preview = buildRequestConfirmationPreview(payload);
    expect(preview.seriesLine).toBe('#1 in Dune Chronicles');
  });

  it('shows series name without position when position is absent', () => {
    const payload: CreateRequestPayload = {
      book_data: {
        title: 'Test',
        author: 'Author',
        series_name: 'My Series',
      },
      release_data: null,
      context: {
        source: '*',
        content_type: 'ebook',
        request_level: 'book',
      },
    };
    const preview = buildRequestConfirmationPreview(payload);
    expect(preview.seriesLine).toBe('My Series');
  });

  it('applies trimmed note when notes are allowed', () => {
    const result = applyRequestNoteToPayload(releasePayload, '  please add this  ', true);
    expect(result.note).toBe('please add this');
  });

  it('drops note when notes are disabled or blank', () => {
    const withDisabledNotes = applyRequestNoteToPayload(
      { ...releasePayload, note: 'existing note' },
      'new note',
      false,
    );
    const withBlankNote = applyRequestNoteToPayload(
      { ...releasePayload, note: 'existing note' },
      '   ',
      true,
    );

    expect(withDisabledNotes.note).toBe(undefined);
    expect(withBlankNote.note).toBe(undefined);
  });

  it('truncates notes to max length', () => {
    const overlong = 'a'.repeat(MAX_REQUEST_NOTE_LENGTH + 25);
    const truncated = truncateRequestNote(overlong);
    expect(truncated.length).toBe(MAX_REQUEST_NOTE_LENGTH);
  });
});
