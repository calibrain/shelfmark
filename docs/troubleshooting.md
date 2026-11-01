# Troubleshooting

Start with the logs, then attach a debug bundle if you open an issue.

## Logs

Shelfmark writes its log to the container output, so `docker logs shelfmark` shows it. With `ENABLE_LOGGING` on (the default) it also writes log files under `LOG_ROOT/shelfmark/` (`/var/log/shelfmark/` by default), including `shelfmark.log` and the startup logs.

`LOG_LEVEL` sets how much is written (`INFO` by default). Set it to `DEBUG` to see what a search or download is doing. See the [Environment Variables](environment-variables.md) reference for both settings.

## Debug bundle

Set `DEBUG=true` on the container and restart it. A **DEBUG** button then appears in the interface. It generates a zip with logs, configuration and environment details, with sensitive values redacted. Have a look through it before you post it publicly, then attach it when you open an issue.

The full image also records the browser's screen during a bypass while Debug mode is on. ffmpeg isn't in the image, so the container installs it in the background at startup, which needs network access. Bypasses that run before it finishes aren't recorded. The log says when the install succeeds, fails or gives up after 120 seconds. In non-root mode it can't install anything, so the log warns that bypasses won't be recorded and everything else works as normal.

## Reporting an issue

Open an issue on [GitHub](https://github.com/calibrain/shelfmark/issues) and fill in the bug report template. A description of what you expected, the steps to reproduce it and the debug bundle are what make a problem findable.
