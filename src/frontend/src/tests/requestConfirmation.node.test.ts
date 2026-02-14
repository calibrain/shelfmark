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
  });

  it('omits release line for book-level payloads', () => {
    const preview = buildRequestConfirmationPreview(bookPayload);

    assert.equal(preview.title, 'Book Level');
    assert.equal(preview.author, 'Book Author');
    assert.equal(preview.releaseLine, '');
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
