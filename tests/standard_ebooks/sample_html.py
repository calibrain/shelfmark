"""Sample HTML fixtures for Standard Ebooks tests, representing real website markup."""

SLUG_2_SEG = "mary-shelley/frankenstein"
SLUG_3_SEG = "maurice-leblanc/the-hollow-needle/alexander-teixeira-de-mattos"

SEARCH_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head><title>Ebooks — Standard Ebooks</title></head>
<body>
<main>
  <ol class="grid ebooks-list">
    <li>
      <article typeof="schema:Book">
        <a href="/ebooks/{SLUG_2_SEG}" property="schema:url">
          <picture>
            <img src="/ebooks/{SLUG_2_SEG}/cover-thumbnail.jpg" alt="Frankenstein cover" property="schema:image" />
          </picture>
          <p class="title" property="schema:name">Frankenstein</p>
        </a>
        <p class="author" property="schema:author">Mary Shelley</p>
      </article>
    </li>
    <li>
      <article typeof="schema:Book">
        <a href="/ebooks/{SLUG_3_SEG}" property="schema:url">
          <img src="/ebooks/{SLUG_3_SEG}/cover-thumbnail.jpg" property="schema:image" />
          <p class="title" property="schema:name">The Hollow Needle</p>
        </a>
        <p class="author" property="schema:author">Maurice Leblanc</p>
      </article>
    </li>
    <li>
      <!-- Entry with missing author and fallback markup -->
      <a href="/ebooks/anonymous/beowulf">
        <p class="title">Beowulf</p>
      </a>
    </li>
    <li>
      <!-- Malformed entry with invalid link - should be safely skipped -->
      <p>Malformed item without link</p>
    </li>
  </ol>
</main>
</body>
</html>
"""

EMPTY_SEARCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>Ebooks — Standard Ebooks</title></head>
<body>
<main>
  <ol class="grid ebooks-list">
  </ol>
</main>
</body>
</html>
"""

NO_CONTAINER_HTML = """<!DOCTYPE html>
<html>
<body>
  <p>No results found matching your query.</p>
</body>
</html>
"""

BOOK_PAGE_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head><title>Frankenstein — Standard Ebooks</title></head>
<body>
<main>
  <h1>Frankenstein</h1>
  <section id="downloads">
    <h2>Download for e-readers</h2>
    <ul>
      <li><a href="/ebooks/{SLUG_2_SEG}/downloads/mary-shelley_frankenstein.epub">Compatible epub</a></li>
      <li><a href="/ebooks/{SLUG_2_SEG}/downloads/mary-shelley_frankenstein_advanced.epub">Advanced epub</a></li>
      <li><a href="/ebooks/{SLUG_2_SEG}/downloads/mary-shelley_frankenstein.kepub.epub">Kepub</a></li>
      <li><a href="/ebooks/{SLUG_2_SEG}/downloads/mary-shelley_frankenstein.azw3">Kindle azw3</a></li>
    </ul>
  </section>
</main>
</body>
</html>
"""

BOOK_PAGE_ADVANCED_ONLY_HTML = f"""<!DOCTYPE html>
<html lang="en">
<body>
  <section id="downloads">
    <a href="/ebooks/{SLUG_2_SEG}/downloads/mary-shelley_frankenstein_advanced.epub">Advanced epub only</a>
  </section>
</body>
</html>
"""

BOOK_PAGE_NO_EPUB_HTML = """<!DOCTYPE html>
<html lang="en">
<body>
  <section id="downloads">
    <p>Downloads currently unavailable.</p>
  </section>
</body>
</html>
"""
