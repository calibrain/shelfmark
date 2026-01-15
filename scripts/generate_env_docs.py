#!/usr/bin/env python3
"""Generate markdown documentation for environment variables from the settings registry.

This script extracts all settings that support environment variable configuration
and generates a comprehensive markdown file documenting each option.

Usage:
    python scripts/generate_env_docs.py [--output path/to/output.md]

The generated documentation includes:
- Environment variable name
- Description
- Type (string, number, boolean, etc.)
- Default value
- Organizational grouping by settings tab/group
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def get_field_type_name(field) -> str:
    """Get a human-readable type name for a field."""
    from shelfmark.core.settings_registry import (
        CheckboxField,
        MultiSelectField,
        NumberField,
        OrderableListField,
        PasswordField,
        SelectField,
        TextField,
    )

    if isinstance(field, CheckboxField):
        return "boolean"
    elif isinstance(field, NumberField):
        return "number"
    elif isinstance(field, SelectField):
        return "string (choice)"
    elif isinstance(field, MultiSelectField):
        return "string (comma-separated)"
    elif isinstance(field, OrderableListField):
        return "JSON array"
    elif isinstance(field, PasswordField):
        return "string (secret)"
    elif isinstance(field, TextField):
        return "string"
    else:
        return "string"


def format_default_value(field) -> str:
    """Format the default value for display."""
    default = field.default

    if default is None:
        return "_none_"
    elif isinstance(default, bool):
        return f"`{str(default).lower()}`"
    elif isinstance(default, (int, float)):
        return f"`{default}`"
    elif isinstance(default, str):
        if default == "":
            return "_empty string_"
        return f"`{default}`"
    elif isinstance(default, list):
        if not default:
            return "_empty list_"
        # For simple lists, show comma-separated values
        if all(isinstance(item, str) for item in default):
            return f"`{','.join(default)}`"
        # For complex lists (e.g., OrderableListField defaults), summarize
        return f"_see UI for defaults_"
    else:
        return f"`{default}`"


def get_select_options(field) -> Optional[List[str]]:
    """Get the available options for a SelectField."""
    from shelfmark.core.settings_registry import SelectField

    if not isinstance(field, SelectField):
        return None

    options = field.options
    if callable(options):
        try:
            options = options()
        except Exception:
            return None

    if not options:
        return None

    return [opt.get("label", opt.get("value", "")) for opt in options]


def _generate_bootstrap_env_docs() -> List[str]:
    """Generate documentation for bootstrap environment variables from env.py."""
    # These are environment variables defined in env.py that are used before
    # the settings registry is available
    bootstrap_vars = [
        {
            "name": "CONFIG_DIR",
            "description": "Directory for storing configuration files and plugin settings.",
            "type": "string (path)",
            "default": "/config",
        },
        {
            "name": "LOG_ROOT",
            "description": "Root directory for log files.",
            "type": "string (path)",
            "default": "/var/log/",
        },
        {
            "name": "TMP_DIR",
            "description": "Staging directory for downloads before moving to destination.",
            "type": "string (path)",
            "default": "/tmp/shelfmark",
        },
        {
            "name": "ENABLE_LOGGING",
            "description": "Enable file logging to LOG_ROOT/shelfmark/shelfmark.log.",
            "type": "boolean",
            "default": "true",
        },
        {
            "name": "FLASK_HOST",
            "description": "Host address for the Flask web server.",
            "type": "string",
            "default": "0.0.0.0",
        },
        {
            "name": "FLASK_PORT",
            "description": "Port number for the Flask web server.",
            "type": "number",
            "default": "8084",
        },
        {
            "name": "SESSION_COOKIE_SECURE",
            "description": "Enable secure cookies (requires HTTPS).",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "CWA_DB_PATH",
            "description": "Path to the Calibre-Web database for authentication integration.",
            "type": "string (path)",
            "default": "/auth/app.db",
        },
        {
            "name": "DOCKERMODE",
            "description": "Indicates the application is running inside a Docker container.",
            "type": "boolean",
            "default": "false",
        },
    ]

    lines = [
        "## Bootstrap Configuration",
        "",
        "These environment variables are used at startup before the settings system loads. They typically configure paths and server settings.",
        "",
        "| Variable | Description | Type | Default |",
        "|----------|-------------|------|---------|",
    ]

    for var in bootstrap_vars:
        lines.append(f"| `{var['name']}` | {var['description']} | {var['type']} | `{var['default']}` |")

    lines.append("")
    lines.append("<details>")
    lines.append("<summary>Detailed descriptions</summary>")
    lines.append("")

    for var in bootstrap_vars:
        lines.append(f"#### `{var['name']}`")
        lines.append("")
        lines.append(var["description"])
        lines.append("")
        lines.append(f"- **Type:** {var['type']}")
        lines.append(f"- **Default:** `{var['default']}`")
        lines.append("")

    lines.append("</details>")
    lines.append("")

    return lines


def generate_env_docs() -> str:
    """Generate markdown documentation for all environment variables."""
    # Import settings modules to ensure all settings are registered
    import shelfmark.config.settings  # noqa: F401
    import shelfmark.release_sources.irc.settings  # noqa: F401
    import shelfmark.release_sources.prowlarr.settings  # noqa: F401
    import shelfmark.metadata_providers.hardcover  # noqa: F401
    import shelfmark.metadata_providers.openlibrary  # noqa: F401
    import shelfmark.metadata_providers.googlebooks  # noqa: F401

    from shelfmark.core.settings_registry import (
        ActionButton,
        HeadingField,
        get_all_groups,
        get_all_settings_tabs,
    )

    tabs = get_all_settings_tabs()
    groups = {g.name: g for g in get_all_groups()}

    # Organize tabs by group
    grouped_tabs: Dict[Optional[str], List] = {None: []}
    for group_name in groups:
        grouped_tabs[group_name] = []

    for tab in tabs:
        group_name = tab.group
        if group_name not in grouped_tabs:
            grouped_tabs[group_name] = []
        grouped_tabs[group_name].append(tab)

    # Build markdown output
    lines = [
        "# Environment Variables",
        "",
        "This document lists all configuration options that can be set via environment variables.",
        "",
        "> **Auto-generated** - Do not edit manually. Run `python scripts/generate_env_docs.py` to regenerate.",
        "",
        "## Table of Contents",
        "",
    ]

    # Generate TOC
    toc_entries = [
        "- [Bootstrap Configuration](#bootstrap-configuration)",
    ]

    # Ungrouped tabs first
    for tab in grouped_tabs.get(None, []):
        anchor = tab.display_name.lower().replace(" ", "-")
        toc_entries.append(f"- [{tab.display_name}](#{anchor})")

    # Then grouped tabs
    for group_name, group in groups.items():
        group_tabs = grouped_tabs.get(group_name, [])
        if group_tabs:
            anchor = group.display_name.lower().replace(" ", "-")
            toc_entries.append(f"- [{group.display_name}](#{anchor})")
            for tab in group_tabs:
                sub_anchor = f"{group.display_name}-{tab.display_name}".lower().replace(" ", "-")
                toc_entries.append(f"  - [{tab.display_name}](#{sub_anchor})")

    lines.extend(toc_entries)
    lines.append("")
    lines.append("---")
    lines.append("")

    # Add bootstrap environment variables documentation
    lines.extend(_generate_bootstrap_env_docs())

    # Generate documentation for ungrouped tabs
    for tab in grouped_tabs.get(None, []):
        lines.extend(_generate_tab_docs(tab))

    # Generate documentation for grouped tabs
    for group_name, group in groups.items():
        group_tabs = grouped_tabs.get(group_name, [])
        if not group_tabs:
            continue

        lines.append(f"## {group.display_name}")
        lines.append("")

        for tab in group_tabs:
            lines.extend(_generate_tab_docs(tab, group_prefix=group.display_name))

    return "\n".join(lines)


def _generate_tab_docs(tab, group_prefix: Optional[str] = None) -> List[str]:
    """Generate documentation for a single settings tab."""
    from shelfmark.core.settings_registry import ActionButton, HeadingField

    lines = []

    # Section header
    if group_prefix:
        lines.append(f"### {group_prefix}: {tab.display_name}")
        anchor_id = f"{group_prefix}-{tab.display_name}".lower().replace(" ", "-")
    else:
        lines.append(f"## {tab.display_name}")

    lines.append("")

    # Collect env-supported fields
    env_fields = []
    for field in tab.fields:
        # Skip non-value fields
        if isinstance(field, (ActionButton, HeadingField)):
            continue

        # Skip fields that don't support ENV vars
        if not getattr(field, "env_supported", True):
            continue

        env_fields.append(field)

    if not env_fields:
        lines.append("_No environment variables for this section._")
        lines.append("")
        return lines

    # Generate table
    lines.append("| Variable | Description | Type | Default |")
    lines.append("|----------|-------------|------|---------|")

    for field in env_fields:
        env_var = field.get_env_var_name()
        description = field.description or field.label
        # Clean up description for table (remove newlines, escape pipes)
        description = description.replace("\n", " ").replace("|", "\\|").strip()

        field_type = get_field_type_name(field)
        default = format_default_value(field)

        lines.append(f"| `{env_var}` | {description} | {field_type} | {default} |")

    lines.append("")

    # Add detailed documentation for each field
    lines.append("<details>")
    lines.append("<summary>Detailed descriptions</summary>")
    lines.append("")

    for field in env_fields:
        env_var = field.get_env_var_name()
        lines.append(f"#### `{env_var}`")
        lines.append("")
        lines.append(f"**{field.label}**")
        lines.append("")

        if field.description:
            lines.append(field.description)
            lines.append("")

        lines.append(f"- **Type:** {get_field_type_name(field)}")
        lines.append(f"- **Default:** {format_default_value(field)}")

        if getattr(field, "required", False):
            lines.append("- **Required:** Yes")

        if getattr(field, "requires_restart", False):
            lines.append("- **Requires restart:** Yes")

        # Show options for SelectField
        options = get_select_options(field)
        if options:
            lines.append(f"- **Options:** {', '.join(options)}")

        # Show constraints for NumberField
        from shelfmark.core.settings_registry import NumberField
        if isinstance(field, NumberField):
            constraints = []
            if field.min_value is not None:
                constraints.append(f"min: {field.min_value}")
            if field.max_value is not None:
                constraints.append(f"max: {field.max_value}")
            if constraints:
                lines.append(f"- **Constraints:** {', '.join(constraints)}")

        lines.append("")

    lines.append("</details>")
    lines.append("")

    return lines


def main():
    parser = argparse.ArgumentParser(
        description="Generate markdown documentation for environment variables"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=project_root / "docs" / "environment-variables.md",
        help="Output file path (default: docs/environment-variables.md)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of file",
    )
    args = parser.parse_args()

    docs = generate_env_docs()

    if args.stdout:
        print(docs)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(docs)
        print(f"Generated: {args.output}")


if __name__ == "__main__":
    main()
