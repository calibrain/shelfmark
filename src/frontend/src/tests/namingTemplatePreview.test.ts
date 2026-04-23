import { describe, expect, it } from 'vitest';

import {
  buildNamingTemplatePreview,
  renderNamingTemplate,
  SAMPLE_NAMING_METADATA,
} from '../utils/namingTemplatePreview';

describe('namingTemplatePreview', () => {
  it('renders primary title in path previews', () => {
    const preview = buildNamingTemplatePreview(
      '{Author}/{Series/}{SeriesPosition - }{PrimaryTitle} ({Year})',
      'path',
      'book',
    );

    expect(preview.value).toBe(
      'Arthur Conan Doyle/Sherlock Holmes/5 - The Hound of the Baskervilles (1902).epub',
    );
  });

  it('omits conditional text when a variable is empty', () => {
    const preview = renderNamingTemplate(
      '{Author}/{Series/}{PrimaryTitle}{ - Subtitle}',
      {
        ...SAMPLE_NAMING_METADATA,
        Series: '',
        Subtitle: '',
      },
      { allowPathSeparators: true },
    );

    expect(preview.value).toBe('Arthur Conan Doyle/The Hound of the Baskervilles');
  });

  it('reports unknown bare variables', () => {
    const preview = renderNamingTemplate('{Author}/{NotAThing}', SAMPLE_NAMING_METADATA, {
      allowPathSeparators: true,
    });

    expect(preview.unknownTokens).toEqual(['NotAThing']);
    expect(preview.value).toBe('Arthur Conan Doyle');
  });
});
