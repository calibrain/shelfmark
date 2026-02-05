# Custom Scripts

Shelfmark can run an executable you provide after it finishes importing a download into your destination. This is useful for notifications, triggering a library scan, or running your own post-processing tools.

**TL;DR:** Leave Custom Script Path Mode set to `absolute` (default) and use `$1` in your script. It will be the imported file or folder path.

## Quick Start (Recommended)

1. Put your script on the machine that runs Shelfmark.
1. Make it executable.
1. Set it in Shelfmark (Settings -> Advanced -> Custom Script Path).

Example:

```bash
chmod +x /path/to/your/scripts/post_process.sh
```

### Docker Users

If you run Shelfmark in Docker, the script must exist inside the container. The easiest way is to mount a folder of scripts, then point Shelfmark at the container path in the UI.

```yaml
services:
  shelfmark:
    image: ghcr.io/calibrain/shelfmark:latest
    volumes:
      - /path/to/your/scripts:/scripts:ro
```

Then set:

- Settings -> Advanced -> Custom Script Path: `/scripts/post_process.sh`

<details>
<summary>Docker Compose: Configure Via Environment Variables (Optional)</summary>

```yaml
services:
  shelfmark:
    environment:
      - CUSTOM_SCRIPT=/scripts/post_process.sh
      - CUSTOM_SCRIPT_PATH_MODE=absolute
```

</details>

## What Shelfmark Runs

Shelfmark runs your script once per successful download task, after post-processing and transfer to the final destination.

It always passes **one argument** to your script called the **target path**:

```bash
/scripts/post_process.sh "<target_path>"
```

- Command shape: `<script_path> <target_path>`
- Timeout: 300 seconds (5 minutes)
- Failure behavior: if the script is missing, not executable, times out, or exits non-zero, the task is marked as **Error**

## The Target Path (`$1`)

`$1` is the path Shelfmark wants your script to operate on:

- If exactly one file was imported: the final file path.
- If multiple files were imported: a directory path (the common parent directory of the imported files).

This is always the **final** imported location (after any renaming/organizing and after transfer into your destination folder).

By default, `$1` is an absolute path inside the Shelfmark container (or on your host, if you are not using Docker).

## Example Script

Minimal bash example that prints what Shelfmark imported:

```bash
#!/usr/bin/env bash
set -euo pipefail

target="${1:-}"

echo "Shelfmark custom script target=${target}" >&2

if [[ -d "${target}" ]]; then
  echo "Imported multiple files into: ${target}" >&2
else
  echo "Imported single file: ${target}" >&2
fi
```

<details>
<summary>Advanced Options</summary>

### `CUSTOM_SCRIPT_PATH_MODE` (Absolute vs Relative)

This setting controls what gets passed as `$1`:

- `absolute` (default): pass an absolute path.
- `relative`: pass a path relative to the destination folder.

`relative` is mainly useful if your script needs a destination-relative path (for example, for a scanner/API that already knows the library root). If you're not sure, keep `absolute`.

When set to `relative`, Shelfmark runs the script with its working directory set to the destination folder, so you can treat `$1` as relative to `$PWD`.

Note: if the target is the destination folder itself, `relative` mode may pass `.`.

### Environment Variables For Scripts

These are convenience variables. They can look redundant because they often mirror `$1` in `absolute` mode.

They become useful if you set `CUSTOM_SCRIPT_PATH_MODE=relative` but still want the canonical absolute path:

- `SHELFMARK_CUSTOM_SCRIPT_TARGET`: always the absolute target path (file or folder)
- `SHELFMARK_CUSTOM_SCRIPT_DESTINATION`: destination folder path (Settings -> Downloads -> Destination)

Full list:

| Variable | Meaning |
| --- | --- |
| `SHELFMARK_CUSTOM_SCRIPT_TARGET` | Absolute target path (file or folder) |
| `SHELFMARK_CUSTOM_SCRIPT_RELATIVE` | Target path relative to the destination folder |
| `SHELFMARK_CUSTOM_SCRIPT_DESTINATION` | Destination folder path |
| `SHELFMARK_CUSTOM_SCRIPT_MODE` | `absolute` or `relative` |
| `SHELFMARK_CUSTOM_SCRIPT_PHASE` | Currently always `post_transfer` |

</details>

## Notes And Caveats

- **Concurrency:** downloads can run concurrently (up to your configured worker count), so your script may be invoked in parallel for different tasks.
- **Hardlinks and torrents:** if you use hardlinking to keep seeding, avoid scripts that modify file contents, since hardlinked files share data with the seeding copy.
- **Booklore output mode:** custom scripts run only for the file-based "folder" destination output. If you're using Booklore output mode for books, the script will not run for book imports (but will still run for audiobooks, which always use a destination folder).
