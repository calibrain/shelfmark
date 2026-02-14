import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { CreateRequestPayload } from '../types/index.js';
import {
  applyRequestNoteToPayload,
  buildRequestConfirmationPreview,
  MAX_REQUEST_NOTE_LENGTH,
  truncateRequestNote,
} from '../utils/requestConfirmation.js';

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

    assert.equal(preview.title, 'Example Title');
    assert.equal(preview.author, 'Example Author');
    assert.equal(preview.preview, 'https://example.com/cover.jpg');
    assert.equal(preview.releaseLine, 'EPUB | 2 MB | Prowlarr');
    assert.equal(preview.year, '');
    assert.equal(preview.seriesLine, '');
  });

  it('omits release line for book-level payloads', () => {
    const preview = buildRequestConfirmationPreview(bookPayload);

    assert.equal(preview.title, 'Book Level');
    assert.equal(preview.author, 'Book Author');
    assert.equal(preview.releaseLine, '');
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

    assert.equal(preview.year, '1965');
    assert.equal(preview.seriesLine, '#1 of 6 in Dune Chronicles');
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
    assert.equal(preview.seriesLine, '#1 in Dune Chronicles');
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
    assert.equal(preview.seriesLine, 'My Series');
  });

  it('applies trimmed note when notes are allowed', () => {
    const result = applyRequestNoteToPayload(releasePayload, '  please add this  ', true);
    assert.equal(result.note, 'please add this');
  });

  it('drops note when notes are disabled or blank', () => {
    const withDisabledNotes = applyRequestNoteToPayload(
      { ...releasePayload, note: 'existing note' },
      'new note',
      false
    );
    const withBlankNote = applyRequestNoteToPayload(
      { ...releasePayload, note: 'existing note' },
      '   ',
      true
    );

    assert.equal(withDisabledNotes.note, undefined);
    assert.equal(withBlankNote.note, undefined);
  });

  it('truncates notes to max length', () => {
    const overlong = 'a'.repeat(MAX_REQUEST_NOTE_LENGTH + 25);
    const truncated = truncateRequestNote(overlong);
    assert.equal(truncated.length, MAX_REQUEST_NOTE_LENGTH);
  });
});
