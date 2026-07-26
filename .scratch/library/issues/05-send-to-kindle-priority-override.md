Type: grilling
Status: resolved
Blocked by: 01

# Decide the Send-to-Kindle format priority and override UX

## Question

Two decisions for one feature.

**Background (settled in grilling session):** Send-to-Kindle uses the existing SMTP email-output machinery (`shelfmark/download/outputs/email.py`), not a new mailer. The recipient is configured per-user in My Settings and stored as `kindle_address`. The Send button lives on book detail, and the **format priority + override is chosen inline on the book detail page** (your latest answer).

**Decision 1 — Kindle-supported format set & priority.** Your initial request said default priority is "azw3 then epub". Send-to-Kindle's accepted formats (per Amazon) are PDF, EPUB, DOC, DOCX, TXT, RTF, HTM, HTML, PNG, GIF, JPG, JPEG, BMP — not azw3, not mobi (mobi was deprecated for send-to-kindle in 2022). Out of shelfmark's supported formats (`shelfmark/download/postprocess/policy.py:37-41`: epub, mobi, azw3, fb2, djvu, cbz, cbr), the intersection with Kindle-supported is just **epub** (and pdf if extended). So your stated priority "azw3 then epub" can't actually fire — azw3 is not Kindle-supported. Decide:

- Correct the priority list to Kindle-supported-only. Default = `["epub"]` (with `pdf` once pdf format support is in scope).
- OR: explicitly support "send azw3 then epub" by declining to use Send-to-Kindle for azw3 and falling back to "no Kindle-compatible format available" — but then azw3 is *never* sent, so it shouldn't be in the priority list at all.
- OR: support "azw3 first via a different delivery method" (e.g. side-loading via USB) — but that's outside this effort (no automation, the user does it themselves). Out of scope.

(Correct answer is likely "default for Kindle = epub, since that's the only Kindle-compatible format shelfmark normally ships. azw3 / mobi keep their value as Best For Local Reading, not Kindle.")

**Decision 2 — Override UX on book detail page.** When the user is on book detail and clicks "Send to Kindle":
- The button shows the currently-selected format ("Send EPUB"). One click = send.
- An adjacent control (chevron / dropdown / explicit picker) lets the user override with another available format.
- If no Kindle-compatible format is on disk for this book: the Send button is greyed out with an explanatory tooltip ("No Kindle-compatible format on disk — download EPUB first") — your confirmed position.

Open questions this ticket must resolve:

- Does the format override **persist** per-user (preferred format for future Send-to-Kindle actions), or is it per-action ephemeral? If persist — where: a new `kindle_preferred_format` per-user setting, or reuse the long-desired `PREFERRED_FORMAT` setting the codebase doesn't have today? (Piggybacking on a new general `PREFERRED_FORMAT` setting would also address the format-picker gap that surfaces in #08/#09 — but that's a separate decision.)
- When multiple Kindle-compatible formats are on disk (rare today, more common if PDF support lands), what's the order? Alphabetical? User-set priority?
- Does Send-to-Kindle go through `BOOKS_OUTPUT_MODE = email` (the existing flow, which is per-user) or via a dedicated endpoint (#04) that bypasses the output mode setting? Decide — the architectural survey confirms the two paths exist; pick the cleaner one for an explicit 1-click action.

The output of this ticket is a documented behaviour: format priority list, override persistence policy, button states, and which backend path the Send button uses.

## Outcome of this ticket

A spec for Send-to-Kindle behaviour. Implementation is part of #08 (book detail) and #11 (API impl) — this ticket feeds them.

## Update from #04 (applied when #04 was resolved 2026-07-20)

#04 settled most of the questions this ticket originally posed. Scope is now **narrowed to the format-priority algorithm only**. The settled-and-removed-from-#05 list:

- **Recipient storage**: the per-user `kindle_address` preference. Reject the "reuse `EMAIL_RECIPIENT`" alternative — they're distinct concerns.
- **SMTP machinery**: new `send_file_to_email(file_path, recipient, *, label=None, subject=None) -> None` in `shelfmark/download/outputs/email.py`, reuses `compose_email_message` + `send_email_message` + `_get_email_settings()`. No synthetic `DownloadTask`.
- **Per-user requirement**: a recipient must be set per-user; no fallback to `EMAIL_RECIPIENT` (or anything else). If unset → 400 `{"error": "No email recipient configured"}`.
- **Admin `EMAIL_FROM`** required at instance level (env-overrides-UI per existing convention). If unset, Send-to-Kindle fails at send time via `_OPERATIONAL_ERRORS` → 500. No new "not configured" error code.
- **Endpoint shape**: `POST /api/library/books/:book_id/send-to-kindle` with optional body `{format?}`. Success response: `{"status": "sent", "recipient": "<masked>", "format": "<chosen>"}`. Masking reuses the `label` mechanism; no custom masker for MVP.
- **Backend path**: dedicated library endpoint (`shelfmark/core/library_routes.py`), NOT `BOOKS_OUTPUT_MODE = email` reuse. The existing `BOOKS_OUTPUT_MODE` is the download-time output choice; Send-to-Kindle is a separate click action.

**What #05 still owns**: the format-priority algorithm when `format` is omitted from the request body. Decision 1 above (Kindle-supported format set, default `["epub"]` since Amazon deprecated azw3/mobi for Send-to-Kindle in 2022) is the expected resolution — confirm before locking. Decision 2 (override UX on book detail) moves to #08 — the inline format picker is a UI concern, not #05's. The "Does Send-to-Kindle go through `BOOKS_OUTPUT_MODE = email`" question is answered above (no — dedicated endpoint). The recipient lives in the per-user `kindle_address` preference, not `EMAIL_RECIPIENT`.

Recommended resolution path for #05 when worked: single grilling question — "default format list `["epub"]`, with PDF added if/when PDF support lands, and azw3/mobi never sent via Send-to-Kindle because Amazon doesn't accept them — confirm?". Confirm → close.

## Answer

Resolved 2026-07-20 via single confirm-grill. **Default Send-to-Kindle format priority is `["epub"]`** — the only Kindle-compatible format shelfmark ships today (Amazon deprecated azw3/mobi for Send-to-Kindle in 2022; Kindle accepts PDF/EPUB/DOC/DOCX/TXT/RTF/HTML/images; shelfmark ships epub/mobi/azw3/fb2/djvu/cbz/cbr; intersection = epub only).

Full algorithm, for the `POST /api/library/books/:book_id/send-to-kindle` endpoint when `format` is omitted from the request body:

1. Iterate the static priority list `["epub"]` (known Kindle-compatible only). Azw3 and mobi are **excluded** from this list unconditionally — they have value for *local reading only*, never for Kindle delivery.
2. Return the first format from the priority list that is on disk for the book (per #04's `formats_on_disk` UNION across all `download_history` rows for the Book).
3. If no format from the priority list is on disk → backend returns a 400/error response indicating "no Kindle-compatible format on disk"; the frontend button is greyed out with tooltip ("No Kindle-compatible format on disk — download EPUB first" per #08's scope).
4. If the request body contains `format`, it overrides the algorithm entirely — the caller is responsible for passing a Kindle-compatible value; the backend sends whatever's requested if on disk, else 404 for that (book, format).

**Forward slot**: when (if ever) shelfmark adds **PDF** format support, PDF appends to the priority list **after** epub — i.e. `["epub", "pdf"]`. That append is a fresh decision at the time PDF support lands, not auto-applied today.

### What this resolution does *not* decide

- The override picker UI on book detail (#08 owns) — #05 dictates priority only; #08 implements the inline picker.
- Field name, endpoint shape, SMTP plumbing, admin `EMAIL_FROM` requirement — all settled by #04 (see ticket body's "#04 update" section).
- Per-format file existence checks for non-default formats — #08's UI concern for the picker.
- Whether to expose a `kindle_preferred_format` per-user setting persisting override choices — out of scope for this effort; if a user wants a non-default format, they pass `format` in the body per-action.

## Comments

- Worked 2026-07-20 as the sixth decision on the Library map.
- Resolution is exactly the path the ticket's own "#04 update" recommended — the confirm-grill was the right shape; no new grilling threads surfaced.
- This decision is referenced by #06 (API impl — implements the algorithm in `LibraryService`/`library_routes.py`), #08 (book detail page — implements the grey-out + override picker UI), and #11 (search integration — no direct dependency but the Send-to-Kindle feature ships end-to-end once #06 + #08 both land).
- **Fog cleared:** none parked on #05 itself; this was a sharp question with a single bounded answer. No fog graduates from this resolution.
- **Map unchanged** beyond appending #05 to Decisions-so-far. No tickets newly blocked/unblocked by this (it was only blocked by #01, already resolved). Frontier after this resolution: #06, #09, #10 newly or still takeable.
