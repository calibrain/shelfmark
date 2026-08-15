"""IRC settings registration.

Registers IRC settings for the settings UI.
"""

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    SelectField,
    SettingsField,
    TextField,
    register_settings,
)


def _clear_irc_cache() -> dict[str, str | int | bool]:
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
def irc_settings() -> list[SettingsField]:
    """Define IRC source settings."""
    return [
        HeadingField(
            key="heading",
            title="IRC",
            description=(
                "Search and download ebook and audiobook releases from IRC channels. "
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
        ),
        TextField(
            key="IRC_CHANNEL",
            label="Channel",
            placeholder="e.g. ebooks",
            description=(
                "Channel name without the # prefix. Used for all searches unless a "
                "separate audiobook channel is configured below."
            ),
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
            description=(
                "The search bot to address queries to (required). Searches are sent as "
                '"@<bot> <query>".'
            ),
            required=True,
            env_supported=True,
        ),
        HeadingField(
            key="audiobook_heading",
            title="Audiobooks",
            description=(
                "Most networks index audiobooks in the same channel as ebooks, so leaving "
                "these blank is the right setting for almost everyone. On irc.irchighway.net "
                "the audiobooks are in #ebooks and #bookz is effectively inactive — pointing "
                "this at an empty channel just returns no results. Only fill these in when "
                "your network really does index audiobooks elsewhere (Undernet's #bookz, for "
                "example). Audiobooks are usually posted as archives, so keep ZIP and RAR "
                "enabled under Supported Audiobook Formats or the releases are filtered out."
            ),
        ),
        TextField(
            key="IRC_AUDIOBOOK_CHANNEL",
            label="Audiobook channel",
            placeholder="e.g. bookz",
            description=(
                "Optional. Channel name (without the # prefix) for networks that index "
                "audiobooks separately, such as Undernet's bookz. Leave blank (the usual "
                "setting) to search the main channel above for audiobooks too."
            ),
            required=False,
            env_supported=True,
        ),
        TextField(
            key="IRC_AUDIOBOOK_SEARCH_BOT",
            label="Audiobook search bot",
            placeholder="e.g. search",
            description=(
                "Optional. Search bot for the audiobook channel. Leave blank to reuse "
                "the main search bot above. Only used when an audiobook channel is set."
            ),
            required=False,
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
