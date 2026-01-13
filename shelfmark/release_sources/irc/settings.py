"""IRC settings registration.

Registers IRC settings for the settings UI.
"""

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    SelectField,
    TextField,
    register_settings,
)


def _clear_irc_cache():
    """Clear all cached IRC search results."""
    from shelfmark.release_sources.irc.cache import clear_cache, get_cache_stats

    stats = get_cache_stats()
    count = clear_cache()
    return {
        "success": True,
        "message": f"Cleared {count} cached searches ({stats['total_releases']} releases)",
    }


@register_settings(
    name="irc",
    display_name="IRC",
    icon="download",
    order=56,
)
def irc_settings():
    """Define IRC source settings."""
    return [
        HeadingField(
            key="heading",
            title="IRC",
            description=(
                "Search and download books from IRC ebook channels. "
                "This source connects via IRC and uses DCC for file transfers. "
                "Configure the connection details below to enable IRC search. "
                "Note: DCC requires direct TCP connections to arbitrary ports, "
                "which may not work behind strict firewalls or NAT."
            ),
        ),

        TextField(
            key="IRC_SERVER",
            label="Server",
            placeholder="e.g. irc.example.net",
            description="IRC server hostname",
            required=True,
            env_supported=True,
        ),

        NumberField(
            key="IRC_PORT",
            label="Port",
            default=6697,
            description="IRC server port (usually 6697 for TLS, 6667 for plain)",
            env_supported=True,
        ),

        CheckboxField(
            key="IRC_USE_TLS",
            label="Use TLS",
            default=True,
            description="Enable TLS/SSL encryption for the IRC connection. Disable for servers that don't support TLS.",
            env_supported=True,
        ),

        TextField(
            key="IRC_CHANNEL",
            label="Channel",
            placeholder="e.g. ebooks",
            description="Channel name without the # prefix",
            required=True,
            env_supported=True,
        ),

        TextField(
            key="IRC_NICK",
            label="Nickname",
            placeholder="e.g. myusername",
            description="Your IRC nickname (required). Must be unique on the IRC network.",
            required=True,
            env_supported=True,
        ),

        TextField(
            key="IRC_SEARCH_BOT",
            label="Search bot",
            placeholder="e.g. search",
            description="The search bot to query for results",
            env_supported=True,
        ),

        HeadingField(
            key="cache_heading",
            title="Search Cache",
            description=(
                "IRC search results are cached to reduce load on IRC servers. "
                "Use the Refresh button in the release modal to force a new search."
            ),
        ),

        SelectField(
            key="IRC_CACHE_TTL",
            label="Cache Duration",
            description="How long to keep cached search results before they expire.",
            options=[
                {"value": "2592000", "label": "30 days"},
                {"value": "0", "label": "Forever (until manually cleared)"},
            ],
            default="2592000",  # 30 days
        ),

        ActionButton(
            key="clear_irc_cache",
            label="Clear Cache",
            description="Remove all cached IRC search results.",
            style="danger",
            callback=_clear_irc_cache,
        ),
    ]
