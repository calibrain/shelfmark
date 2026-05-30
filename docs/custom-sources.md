# Custom Sources

Custom sources let you add new book search and download sources to Shelfmark without modifying Shelfmark's own code. Each custom source is a single Python script that you drop into your config directory.

## How it works

Shelfmark scans for `.py` files in `$CONFIG_DIR/custom_sources/` every time it starts. Any file it finds is loaded automatically and appears under **Settings → Custom Sources**.

`$CONFIG_DIR` is wherever you mounted the Shelfmark config volume — typically `./data/config/` in a Docker Compose setup.

> **Why isn't `data/` in git?**  
> The `data/` directory is a runtime volume: it holds your database, saved settings, API keys, and downloaded files. It belongs on your machine, not in the source tree. Custom source plugins live there too, which is fine — they're personal to your instance. The example and reference files in `docs/` are what gets shared via git.

## Adding a plugin

1. Copy `docs/example_custom_source.py` to `$CONFIG_DIR/custom_sources/my_source.py`
2. Edit it to point at your actual source
3. Restart Shelfmark — your source appears under Custom Sources in Settings

Each plugin gets its own settings tab automatically, containing at minimum an **Enable / Disable** toggle. You can turn a plugin off without deleting the file.

## Dependencies

If your plugin needs a Python library that isn't already included (e.g. `beautifulsoup4`, `lxml`), create a `requirements.txt` in the same folder:

```
$CONFIG_DIR/custom_sources/requirements.txt
```

List one package per line. Shelfmark runs `pip install -r requirements.txt` automatically the next time it starts. The `requests` library is always available without adding it.

## Security

> ⚠ **Plugin files run as full Python code** with the same access as Shelfmark itself — they can read your config, access the network, and write files. Only install plugins from sources you trust. Treat them the same as any program you run on your machine.

## Reference files

| File | Purpose |
|------|---------|
| `docs/example_custom_source.py` | Template to copy and edit — covers every feature with inline comments |

## Developer reference

For the complete API — all classes, callbacks, field types, column config, and download protocols — see the [Release Sources Plugin Guide](dev/release-sources-plugin-guide.md).
