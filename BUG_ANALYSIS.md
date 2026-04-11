# Deep Bug Analysis — Shelfmark (Current Version: calibrain/main @ b3b8f34)

> **Date**: April 11, 2026  
> **Audit scope**: Complete rewrite (Python package + TypeScript frontend, completely restructured from the old flat-file version)  
> **Commits since old version**: ~800+ commits over 7 months  
> **This analysis supersedes the old PR** (`calibrain/shelfmark` old flat-file version is abandoned; this is the current canonical codebase)

---

## Table of Contents

1. [CRITICAL: `downloader/http.py` — Python 2 `except` Syntax + Size Parsing Still Broken](#c1)
2. [CRITICAL: `core/queue.py` — Race Condition in `get_next()` Still Present](#c2)
3. [CRITICAL: `core/queue.py` — Cancelled QUEUED Items Never Removed from PriorityQueue](#c3)
4. [CRITICAL: `bypass/internal_bypasser.py` — `_get_base_domain()` Broken for Compound TLDs](#c4)
5. [CRITICAL: `bypass/internal_bypasser.py` — Unprotected Concurrent Access to Cookie Cache](#c5)
6. [CRITICAL: `core/models.py` — `build_filename` Truncation Loses Year and Extension](#c6)
7. [HIGH: `bypass/internal_bypasser.py` — Cookie Cache Always Misses Due to Domain Mismatch](#c7)
8. [HIGH: `download/archive.py` — Hardcoded Table Cell Indices in Search Results (SAME bug as old version)](#c8)
9. [HIGH: `download/archive.py` — Hardcoded `nth-of-type` CSS Selectors](#c9)
10. [HIGH: `download/archive.py` — Hardcoded `div[-6]` with No Bounds Check](#c10)
11. [HIGH: `download/http.py` — Response Not Closed on `_try_resume` Failure Paths](#c11)
12. [HIGH: `download/http.py` — URL Rotation Only on Zero Bytes Downloaded](#c12)
13. [MEDIUM: `core/queue.py` — Cancel Flag Set Inside Lock, Status Updated Outside](#c13)
14. [MEDIUM: `core/queue.py` — TOCTOU Race in `get_status()` Calling `refresh()` Without Lock](#c14)
15. [MEDIUM: `download/archive.py` — Silent Failure When All Search Rows Fail](#c15)
16. [MEDIUM: `src/frontend/src/App.tsx` — XSS via `status_message` in Toast Notifications](#c16)
17. [MEDIUM: `src/frontend/src/SocketContext.tsx` — No WebSocket Reconnection Logic](#c17)
18. [MEDIUM: `src/frontend/src/api.ts` — Infinite Timeout on Release Searches](#c18)
19. [MEDIUM: `qbittorrent.py` — `metaDL` State Message Never Clears ("Fetching metadata" stuck)](#c19)
20. [LOW: `bypass/internal_bypasser.py` — Abort-on-Consecutive-Challenge Logic Never Triggers](#c20)
21. [LOW: `bypass/internal_bypasser.py` — FFmpeg Race Condition in Recording Functions](#c21)
22. [LOW: `download/archive.py` — Python 2 `except` Syntax](#c22)
23. [Summary](#summary)

---

## <a name="c1"></a>1. CRITICAL: `download/http.py` — Python 2 `except` Syntax + Size Parsing Still Broken

**File**: `shelfmark/download/http.py`

### Bug A: Python 2 `except` Syntax (CRITICAL — Module Won't Load)

**Line**: 165

```python
except ValueError, IndexError:   # ← Python 2 syntax, INVALID in Python 3
```

**Root Cause**: The code uses the deprecated Python 2 comma-separated exception syntax instead of Python 3 tuple syntax.

**How it manifests**: `SyntaxError` at module import time. The entire `http.py` module cannot be loaded in Python 3. This would crash the application on startup.

**Fix**:
```python
except (ValueError, IndexError):
```

---

### Bug B: `parse_size_string` Still Uses `[:-2]` (CRITICAL — Size Parsing Incorrect)

**Lines**: 162–163

```python
if normalized.endswith(suffix):
    return float(normalized[:-2]) * mult   # ← Always slices 2 chars regardless of suffix length
```

**Root Cause**: The code strips the last 2 characters assuming all suffixes are 2 chars (`KB`, `MB`, `GB`). But `normalized` has already been uppercased and spaces removed, so:

- `"1.5MB"` (no space) → uppercased → `"1.5MB"` → `endswith("MB")` → True → `normalized[:-2]` = `"1.5M"` → `float("1.5M")` → **`ValueError`**

Even with spaces present (`"1.5 MB"`), `[:-2]` on `"1.5 MB"` strips the space and B: `"1.5 M"` → `ValueError`. Only `"1.5MB"` (no space) passes the `endswith` check but then fails on `float()`.

**Verified**: `parse_size_string("1.5 MB")` → `None`. `parse_size_string("500 MB")` → `None`. `parse_size_string("1.5MB")` → `ValueError` (caught, returns `None`).

**Impact**: Size parsing returns `None` for nearly all inputs. HTTP fallback to `content-length` header works, but when that's absent (chunked transfer), downloads fail validation silently.

**Fix**:
```python
if normalized.endswith(suffix):
    # Strip the actual suffix length, not always 2
    return float(normalized[:-len(suffix)]) * mult
```

---

## <a name="c2"></a>2. CRITICAL: `core/queue.py` — Race Condition in `get_next()` Still Present

**File**: `shelfmark/core/queue.py`, lines 78–94

### Same Root Cause as Old Version

```python
def get_next(self) -> tuple[str, Event] | None:
    while True:
        try:
            queue_item = self._queue.get_nowait()   # ← OUTSIDE the lock!
        except queue.Empty:
            return None

        with self._lock:                              # ← Lock acquired too late
            task_id = queue_item.book_id
            if task_id in self._status and self._status[task_id] == QueueStatus.CANCELLED:
                continue                             # Item permanently lost from queue
```

**Root Cause**: `queue_item` is removed from the `PriorityQueue` *before* the lock is acquired. Between dequeuing and lock acquisition, another thread can call `cancel_download()`. The cancelled item is then permanently discarded — not returned to the queue.

**Production Impact**: Legitimately queued items silently disappear when `get_next()` is called while another thread is cancelling. This is the **exact same race condition** that existed in the old `models.py`.

**Fix**:
```python
def get_next(self) -> tuple[str, Event] | None:
    while True:
        with self._lock:           # Lock FIRST
            try:
                queue_item = self._queue.get_nowait()
            except queue.Empty:
                return None

            task_id = queue_item.book_id
            if self._status.get(task_id) == QueueStatus.CANCELLED:
                continue           # Skip cancelled, don't permanently remove

            cancel_flag = Event()
            self._cancel_flags[task_id] = cancel_flag
            self._active_downloads[task_id] = True
            return task_id, cancel_flag
```

---

## <a name="c3"></a>3. CRITICAL: `core/queue.py` — Cancelled QUEUED Items Never Removed from PriorityQueue

**File**: `shelfmark/core/queue.py`, lines 222–240

### Same Root Cause as Old Version

When `cancel_download()` is called on a `QUEUED` task, the status is set to `CANCELLED` but the item **stays in the PriorityQueue**. The item only gets pulled and discarded when `get_next()` eventually dequeues it. With many cancellations, the queue grows unbounded with garbage items.

**Fix**:
```python
elif current_status == QueueStatus.QUEUED:
    # Rebuild queue excluding this task_id
    temp_items = []
    while not self._queue.empty():
        item = self._queue.get_nowait()
        if item.book_id != task_id:
            temp_items.append(item)
    for item in temp_items:
        self._queue.put(item)
    self._update_status(task_id, QueueStatus.CANCELLED)
    return True
```

---

## <a name="c4"></a>4. CRITICAL: `bypass/internal_bypasser.py` — `_get_base_domain()` Broken for Compound TLDs

**File**: `shelfmark/bypass/internal_bypasser.py`, lines 149–151

```python
def _get_base_domain(domain: str) -> str:
    return ".".join(domain.split(".")[-2:]) if "." in domain else domain
```

**Root Cause**: Naive split on `.` doesn't handle compound TLDs. This is a **catastrophic bug** for z-lib domains:

| Input | Expected | Actual | Error |
|-------|----------|--------|-------|
| `www.z-lib.fm` | `z-lib.fm` | `fm` | ❌ 100% wrong |
| `books.zlibrary-global.se` | `zlibrary-global.se` | `library-global.se` | ❌ wrong |
| `mirror.z-lib.id` | `z-lib.id` | `id` | ❌ wrong |

**Production Impact**: All cookie lookups for z-lib domains fail. Every Cloudflare bypass request spawns a new Chrome instance even when valid cached cookies exist. This is the **root cause of why z-lib downloads get stuck on "fetching metadata"** — the bypasser can't find cached cookies, so every request reinitializes Chrome.

**Fix**:
```python
COMPOUND_TLDS = {
    "co.uk", "com.au", "com.br", "com.mx", "com.sg", "com.hk",
    "co.jp", "co.nz", "co.za", "co.kr", "co.id", "co.th",
    "org.uk", "org.au", "org.nz", "org.za",
    "net.au", "net.br", "net.nz",
    "z-lib.fm", "z-lib.gs", "z-lib.id", "z-lib.sk",
    "zlibrary-global.se",
}

def _get_base_domain(domain: str) -> str:
    if not domain:
        return domain
    parts = domain.split(".")
    tld = ".".join(parts[-2:])
    if tld in COMPOUND_TLDS:
        return ".".join(parts[-2:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain
```

---

## <a name="c5"></a>5. CRITICAL: `bypass/internal_bypasser.py` — Unprotected Concurrent Access to Cookie Cache

**File**: `shelfmark/bypass/internal_bypasser.py`, lines 237–265

### Root Cause

All **getter** functions (`get_cf_cookies_for_domain`, `has_valid_cf_cookies`, `get_cf_user_agent_for_domain`) access the shared `_cf_cookies` and `_cf_user_agents` dicts **without holding `_cf_cookies_lock`**. Only `_store_extracted_cookies()` and `clear_cf_cookies()` use the lock.

```python
# Line 237: No lock held here!
def get_cf_cookies_for_domain(domain: str) -> dict[str, str]:
    cookies = _cf_cookies.get(base_domain, {})   # ← UNPROTECTED read
    if cf_clearance and expiry > 0 and time.time() > expiry:
        _cf_cookies.pop(base_domain, None)         # ← UNPROTECTED write
    return {name: c["value"] for name, c in cookies.items()}
```

**Production Impact**: Race between any getter and `_store_extracted_cookies()` → `KeyError` or stale data returned. Thread A reads cookies, Thread B evicts expired entry, Thread A returns stale/not-found result → bypass fails → new Chrome spawns.

**Fix**: Wrap all getter access in `with _cf_cookies_lock:`.

---

## <a name="c6"></a>6. CRITICAL: `core/models.py` — `build_filename` Truncation Loses Year and Extension

**File**: `shelfmark/core/models.py`, line 26

```python
filename = re.sub(r'[\\/:*?"<>|]', "_", filename.strip())[:245]  # Truncates HERE
if fmt:
    filename = f"{filename}.{fmt}"                               # Added AFTER truncation
```

**Root Cause**: For a 250+ character title, truncation to 245 chars happens *before* the extension is appended. The year information (part of the filename string) and the file extension can be silently discarded.

**Verified**:
```
Input: title="A"*250, year="2024", fmt="pdf"
Output: "AAAA...AAAA.pdf"  (245 A's + .pdf) — YEAR "2024" LOST
```

**Production Impact**: Downloaded files have no extension → processing pipeline fails. Year stripped from filenames → incorrect library organization.

**Fix**:
```python
if fmt:
    max_base_len = 245 - (len(fmt) + 1)
    filename = filename[:max_base_len] if len(filename) > max_base_len else filename
    filename = f"{filename}.{fmt}"
else:
    filename = filename[:245]
```

---

## <a name="c7"></a>7. HIGH: `bypass/internal_bypasser.py` — Cookie Cache Always Misses Due to Domain Mismatch

**File**: `shelfmark/bypass/internal_bypasser.py`, lines 995–998

```python
def _try_with_cached_cookies(url: str, hostname: str) -> str | None:
    cookies = get_cf_cookies_for_domain(hostname)   # Passes full hostname
```

This calls `get_cf_cookies_for_domain("www.z-lib.fm")` → internally calls `_get_base_domain("www.z-lib.fm")` → returns `"fm"` → looks up `_cf_cookies["fm"]` → **never found** even if cookies exist under `"z-lib.fm"`.

This is a direct consequence of Bug #4. Fix `_get_base_domain()` and this resolves automatically.

---

## <a name="c8"></a>8. HIGH: `download/archive.py` — Hardcoded Table Cell Indices (SAME Bug as Old Version)

**File**: `shelfmark/release_sources/direct_download.py`, lines 291–306

```python
cells = row.find_all("td")
return BrowseRecord(
    id=row.find_all("a")[0]["href"].split("/")[-1],   # ← [0] — first link could be ad
    title=cells[1].find("span").next,                  # ← [1], [2], [3], [4], [7], [8], [9], [10]
    author=cells[2].find("span").next,                  # All hardcoded indices
    publisher=cells[3].find("span").next,
    year=cells[4].find("span").next,
    language=cells[7].find("span").next,
    content=cells[8].find("span").next,
    format=cells[9].find("span").next.lower(),
    size=cells[10].find("span").next,
)
```

**Root Cause**: **Exactly the same fragility** as the old `book_manager.py`. Anna's Archive changes their HTML table structure frequently. Any column added/reordered causes field misalignment.

**Production Impact**: When Anna's Archive adds a single column (e.g., for ratings, series, file count), every field after the change point is read from the wrong column. Books show wrong author/publisher/year. `IndexError` if fewer than 11 columns.

**Fix**: Use header-based column mapping:
```python
header_cells = row.find_parent("table").find("thead").find_all("th")
header_map = {th.text.strip().lower(): idx for idx, th in enumerate(header_cells)}

def get_cell(name: str, default=None):
    idx = header_map.get(name)
    return cells[idx].find("span").next if idx is not None and idx < len(cells) else default
```

---

## <a name="c9"></a>9. HIGH: `download/archive.py` — Hardcoded `nth-of-type` CSS Selectors

**File**: `shelfmark/release_sources/direct_download.py`, lines 320, 327

```python
data = soup.select_one("body > main > div:nth-of-type(1)")
node = data.select_one("div:nth-of-type(1) > img")
```

**Root Cause**: These selectors assume exact DOM nesting. Any wrapper div added by Anna's Archive for ads, analytics, or layout changes breaks this.

**Production Impact**: Book info page parsing returns `RuntimeError("Failed to parse book info for ID: {book_id}")`. Downloads fail with no book metadata extracted.

**Fix**: Use semantic selectors with fallback strategies.

---

## <a name="c10"></a>10. HIGH: `download/archive.py` — Hardcoded `div[-6]` with No Bounds Check

**File**: `shelfmark/release_sources/direct_download.py`, lines 332, 428

```python
data = soup.find_all("div", {"class": "main-inner"})[0].find_next("div")
# ...
info = _extract_book_metadata(original_divs[-6])   # ← No bounds check!
```

`[-6]` with no validation that `original_divs` has at least 6 elements. If a book has minimal metadata (just title and format, no ISBN, series, language), this raises `IndexError`.

**Fix**:
```python
if len(original_divs) >= 6:
    info = _extract_book_metadata(original_divs[-6])
else:
    logger.warning("Book page has fewer divs than expected (%d)", len(original_divs))
    info = {}
```

---

## <a name="c11"></a>11. HIGH: `download/http.py` — Response Not Closed on `_try_resume` Failure Paths

**File**: `shelfmark/download/http.py`, lines 617–622

```python
if response.status_code == _HTTP_STATUS_RANGE_NOT_SATISFIABLE:  # 416
    logger.warning("Range not satisfiable")
    return None   # ← BUG: response not closed!

if response.status_code == _HTTP_STATUS_OK:  # Server doesn't support resume
    logger.info("Server doesn't support resume")
    return None   # ← BUG: response not closed!
```

**Root Cause**: Both early-return paths in `_try_resume()` leak the HTTP connection. Each failed resume attempt holds an open socket.

**Production Impact**: Under high retry load with resume failures, socket exhaustion → "Too many open files" errors → downloads fail entirely.

**Fix**: `response.close()` before `return None` on both paths.

---

## <a name="c12"></a>12. HIGH: `download/http.py` — URL Rotation Only on Zero Bytes Downloaded

**File**: `shelfmark/download/http.py`, lines 548–567

```python
if bytes_downloaded > 0 and retryable:
    resumed = _try_resume(...)
    if resumed:
        return resumed

if bytes_downloaded == 0 and retryable:   # ← Only rotates when NOTHING downloaded!
    new_url = _try_rotation(link, current_url, selector)
```

**Root Cause**: URL rotation (`_try_rotation`) is only attempted when `bytes_downloaded == 0`. If a large file partially downloads (e.g., 500MB of a 1GB file) and then fails, the code retries the **same URL** instead of trying a different mirror. The partial data is wasted.

**Production Impact**: Large downloads that partially complete before a connection failure waste time retrying the same failing server.

**Fix**:
```python
if retryable:
    new_url = _try_rotation(link, current_url, selector)
    if new_url:
        current_url = new_url
        attempt += 1
        continue
```

---

## <a name="c13"></a>13. MEDIUM: `core/queue.py` — Cancel Flag Set Inside Lock, Status Updated Outside

**File**: `shelfmark/core/queue.py`, lines 234, 239

```python
if task_id in self._cancel_flags:
    self._cancel_flags[task_id].set()   # Inside lock
# ...
self.update_status(task_id, QueueStatus.CANCELLED)  # Outside lock (different lock scope)
```

**Root Cause**: The cancel flag is set inside `self._lock`, but `update_status()` acquires `self._lock` again separately. Between these two operations, `get_task_status()` could see the task as DOWNLOADING while the cancel flag is already set — inconsistent state.

**Fix**: Move `_update_status()` inside the lock scope.

---

## <a name="c14"></a>14. MEDIUM: `core/queue.py` — TOCTOU Race in `get_status()` Calling `refresh()` Without Lock

**File**: `shelfmark/core/queue.py`, lines 171–190

```python
def get_status(self, user_id: int | None = None) -> dict[QueueStatus, dict[str, DownloadTask]]:
    refresh()          # ← Called WITHOUT holding the lock!
    with self._lock:
        for task_id, status in self._status.items():
            # ...
```

**Root Cause**: `refresh()` is called outside the lock, then `self._lock` is acquired separately. Between these, another thread can modify `_status`. Also, `refresh()` releases its lock between dict iteration and deletion, creating a window for `RuntimeError: dictionary changed size during iteration` if `clear_completed()` runs concurrently.

**Fix**: Wrap entire `get_status()` body (including `refresh()` call) inside `with self._lock:`.

---

## <a name="c15"></a>15. MEDIUM: `download/archive.py` — Silent Failure When All Search Rows Fail

**File**: `shelfmark/release_sources/direct_download.py`, lines 286–310

```python
def _parse_search_result_row(row: Tag) -> BrowseRecord | None:
    try:
        # ... parsing ...
    except (AttributeError, IndexError, KeyError, TypeError) as e:
        logger.error_trace(...)   # ← Only error_trace — may not be visible
        return None              # ← Silent drop
```

**Root Cause**: When parsing fails, it returns `None` and only logs at `error_trace` level. If ALL rows fail (Anna's Archive changed their HTML structure), the user sees "No books found" with no indication that parsing itself broke. Developers have no visibility until users report the issue.

**Fix**:
```python
except (AttributeError, IndexError, KeyError, TypeError) as e:
    _parse_failures += 1
    logger.warning("Parse failure #%d: %s. Row: %s", _parse_failures, e, row.text[:200])
    return None
```

And in `search_books()` after the loop:
```python
if not books and _parse_failures > 0:
    logger.error("0 books returned but %d rows failed to parse. HTML structure may have changed.", _parse_failures)
```

---

## <a name="c16"></a>16. MEDIUM: `src/frontend/src/App.tsx` — XSS via `status_message` in Toast Notifications

**File**: `src/frontend/src/App.tsx`, line 698

```tsx
showToast(`${book.title || 'Book'}: ${errorMsg}`, 'error');
```

**Root Cause**: `book.status_message` from the API is interpolated directly into a toast without sanitization. If a compromised/malicious book record contains `status_message: "<script>alert('xss')</script>"`, it executes in the browser.

**Production Impact**: Stored XSS via book metadata. Also, `book.title` in toast interpolations at lines 645, 659, 670 is unescaped.

**Fix**: Escape at toast call sites:
```tsx
const escapeHtml = (s: string) => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
showToast(`${escapeHtml(book.title) || 'Book'}: ${escapeHtml(errorMsg)}`, 'error');
```

---

## <a name="c17"></a>17. MEDIUM: `src/frontend/src/SocketContext.tsx` — No WebSocket Reconnection Logic

**File**: `src/frontend/src/SocketContext.tsx`, lines 22–56

```tsx
socket.on('connect_error', (err) => {
  console.error('Socket connection error:', err.message);
  setConnected(false);
  // ← No reconnection attempt!
});
```

**Root Cause**: The Socket.IO client has no `reconnection: true` configured. When the WebSocket disconnects (network hiccup, server restart), it never reconnects automatically.

**Production Impact**: Users see a stale UI after any transient connection drop. Real-time status updates stop until manual page refresh.

**Fix**:
```tsx
const socket = io(wsUrl, {
  reconnection: true,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 5000,
});
```

---

## <a name="c18"></a>18. MEDIUM: `src/frontend/src/api.ts` — Infinite Timeout on Release Searches

**File**: `src/frontend/src/api.ts`, line 789

```tsx
return fetchJSON<ReleasesResponse>(`${API_BASE}/releases?${params.toString()}`, {}, null);
//                                                                            ↑ null = no timeout
```

**Root Cause**: Release searches pass `null` for timeout, disabling it entirely. If the backend hangs, the request never resolves and the UI spinner shows forever.

**Production Impact**: User clicks "Get Releases" → modal spinner freezes indefinitely if backend doesn't respond.

**Fix**: Use a reasonable timeout:
```tsx
return fetchJSON<ReleasesResponse>(`${API_BASE}/releases?${params.toString()}`, {}, 120000);
```

---

## <a name="c19"></a>19. MEDIUM: `qbittorrent.py` — `metaDL` State Message Never Clears ("Fetching metadata" stuck)

**File**: `shelfmark/download/clients/qbittorrent.py`, lines 439–462

### Root Cause of "Fetching Metadata" Stuck

```python
state_info = {
    "metaDL": ("downloading", "Fetching metadata"),
    # ...
}
```

When qBittorrent transitions from `metaDL` → `downloading`, the state key changes to `"downloading"` but the progress message does not update to reflect actual download progress. The message remains `"Fetching metadata"` even after metadata is fetched and real downloading begins.

Additionally, `update_download_status` returns early without broadcasting if the `(status_key, message)` tuple is unchanged from the last call — so even if the message *should* update, it may not.

**Fix**: Add explicit transitional state handler:
```python
if torrent_state == "metaDL":
    state, message = ("downloading", "Fetching metadata")
elif torrent_state == "downloading":
    state, message = ("downloading", None)  # Progress bar shows real progress
```

---

## <a name="c20"></a>20. LOW: `bypass/internal_bypasser.py` — Abort-on-Consecutive-Challenge Logic Never Triggers

**File**: `shelfmark/bypass/internal_bypasser.py`, lines 562, 581

```python
min_same_challenge_before_abort = max(MAX_CONSECUTIVE_SAME_CHALLENGE, len(BYPASS_METHODS) + 1)  # = 5
# ...
if consecutive_same_challenge >= min_same_challenge_before_abort:  # needs 5!
    return False
```

With `max_retries` defaulting to 3, the abort logic (which needs 5 consecutive same-challenge detections) **never triggers**. Stuck challenge loops run until `max_retries` exhausted without early termination.

---

## <a name="c21"></a>21. LOW: `bypass/internal_bypasser.py` — FFmpeg Race Condition in Recording Functions

**File**: `shelfmark/bypass/internal_bypasser.py`, lines 845–846, 967–992

The global `DISPLAY` dict (containing FFmpeg process handle and output path) is accessed without any lock. Between `DISPLAY.get("ffmpeg")` returning `None` and another thread setting it, the recording state can corrupt. Under concurrent bypass requests in Docker mode, FFmpeg subprocesses can leak.

---

## <a name="c22"></a>22. LOW: `download/archive.py` — Python 2 `except` Syntax

**File**: `shelfmark/release_sources/direct_download.py`, line 354

```python
except AttributeError, TypeError:   # ← Python 2 syntax
```

Same Python 2 compatibility issue as Bug #1A. Would cause `SyntaxWarning` in Python 3.

---

## <a name="summary"></a>Summary

### Bugs by Severity

| Priority | Count | Items |
|----------|-------|-------|
| **CRITICAL** | 6 | Python 2 syntax (http.py), size parsing broken (http.py), get_next race (queue.py), cancelled queue items (queue.py), compound TLD domain parsing (bypasser), unprotected cookie cache (bypasser), build_filename truncation (models.py) |
| **HIGH** | 5 | Cookie cache domain mismatch (bypasser), hardcoded table cell indices (archive.py), nth-of-type selectors (archive.py), div[-6] no bounds check (archive.py), response leaks in _try_resume (http.py), URL rotation only at zero bytes (http.py) |
| **MEDIUM** | 6 | Cancel flag/status not atomic (queue.py), TOCTOU race in get_status (queue.py), silent parse failures (archive.py), XSS in toasts (frontend), no WebSocket reconnection (frontend), infinite API timeout (frontend), metaDL state stuck (qbittorrent) |
| **LOW** | 3 | Abort logic never triggers (bypasser), FFmpeg race (bypasser), Python 2 except syntax (archive.py) |

### Why qBittorrent Gets Stuck on "Fetching Metadata" (Current Version Root Causes)

| Factor | Severity | Mechanism |
|--------|----------|-----------|
| **Cookie cache domain mismatch** (`_get_base_domain`) | CRITICAL | z-lib cookies never found → every request spawns new Chrome → slow → metadata timeout |
| **Compound TLD parsing broken** | CRITICAL | `z-lib.fm` → `"fm"` → all z-lib cookie lookups fail |
| **`metaDL` state message never clears** | MEDIUM | qBittorrent state transitions but UI message doesn't update |
| **`_is_torrent_loaded` 404 with no retry** | MEDIUM | Torrent takes >5s to register → verification loop exits early → torrent not found → 30s retry cycle |
| **URL rotation only at zero bytes** | MEDIUM | Partial download fails → retries same URL → metadata fetch never completes |
| **Silent parse failures** | MEDIUM | HTML structure change → no downloads queued → qBittorrent has nothing to fetch |

### Key Files Audited

| File | LOC | Bugs Found |
|------|-----|-----------|
| `shelfmark/download/http.py` | ~700 | 6 (1 critical syntax, 1 critical size parsing, 2 high response leaks, 1 high rotation, 1 medium) |
| `shelfmark/core/queue.py` | ~450 | 4 (2 critical race/cancelled items, 2 medium atomicity/TOCTOU) |
| `shelfmark/bypass/internal_bypasser.py` | ~1053 | 6 (2 critical domain parsing/cookie cache, 1 high cookie mismatch, 2 low abort/ffmpeg) |
| `shelfmark/core/models.py` | ~169 | 1 (critical truncation), plus 3 medium/low |
| `shelfmark/release_sources/direct_download.py` | ~850 | 6 (3 high HTML parsing fragility, 2 medium silent failures, 1 low python2 syntax) |
| `shelfmark/download/clients/qbittorrent.py` | ~678 | 3 (1 medium metaDL stuck, 2 low 404/no-retry) |
| `src/frontend/src/` (TypeScript) | ~3000+ | 4 (1 medium XSS in toasts, 1 medium no reconnect, 1 medium infinite timeout, 1 info no error boundary) |
