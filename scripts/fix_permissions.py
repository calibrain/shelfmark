#!/usr/bin/env python3
"""Fix permissions on all configured directories.

This script is called by the entrypoint to ensure all user-configured
directories have correct ownership. It reads directory paths from:
- CONFIG_DIR environment variable
- Config files in CONFIG_DIR/plugins/

Outputs directory paths that need permission fixing (one per line).
The entrypoint handles the actual chown operations.
"""

import json
import os
import sys
from pathlib import Path


def get_directories_from_config() -> set[str]:
    """Extract all directory paths from config files."""
    directories = set()

    config_dir = Path(os.getenv("CONFIG_DIR", "/config"))
    plugins_dir = config_dir / "plugins"

    if not plugins_dir.exists():
        return directories

    # Keys that contain directory paths
    directory_keys = {
        # Main destinations
        "DESTINATION",
        "DESTINATION_AUDIOBOOK",
        # Content type routing directories
        "AA_CONTENT_TYPE_DIR_FICTION",
        "AA_CONTENT_TYPE_DIR_NON_FICTION",
        "AA_CONTENT_TYPE_DIR_UNKNOWN",
        "AA_CONTENT_TYPE_DIR_MAGAZINE",
        "AA_CONTENT_TYPE_DIR_COMIC",
        "AA_CONTENT_TYPE_DIR_STANDARDS",
        "AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
        "AA_CONTENT_TYPE_DIR_OTHER",
        # Legacy keys (in case of old configs)
        "INGEST_DIR",
        "INGEST_DIR_AUDIOBOOK",
        "INGEST_DIR_BOOK_FICTION",
        "INGEST_DIR_BOOK_NON_FICTION",
        "INGEST_DIR_BOOK_UNKNOWN",
        "INGEST_DIR_MAGAZINE",
        "INGEST_DIR_COMIC_BOOK",
        "INGEST_DIR_STANDARDS_DOCUMENT",
        "INGEST_DIR_MUSICAL_SCORE",
        "INGEST_DIR_OTHER",
        "LIBRARY_PATH",
        "LIBRARY_PATH_AUDIOBOOK",
    }

    # Read all JSON config files
    for config_file in plugins_dir.glob("*.json"):
        try:
            with open(config_file, "r") as f:
                config = json.load(f)

            for key in directory_keys:
                if key in config:
                    value = config[key]
                    if value and isinstance(value, str) and value.startswith("/"):
                        directories.add(value)
        except (json.JSONDecodeError, OSError):
            continue

    return directories


def main():
    """Output all configured directories that exist."""
    directories = get_directories_from_config()

    # Filter to directories that actually exist
    existing = []
    for dir_path in directories:
        path = Path(dir_path)
        if path.exists() and path.is_dir():
            existing.append(dir_path)

    # Output one directory per line
    for dir_path in sorted(existing):
        print(dir_path)


if __name__ == "__main__":
    main()
