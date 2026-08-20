"""Challenge-page detection shared by the bypassers and the HTTP retry path.

Kept out of `internal_bypasser` so the HTTP layer can recognise an interstitial
without importing SeleniumBase: that module is imported lazily precisely because its
browser dependencies are optional, and external-bypasser setups run without them.
"""

# Matched against lowercased text, so every entry must be lowercase.
CLOUDFLARE_INDICATORS = [
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "cloudflare.com/products/turnstile",
]

DDOS_GUARD_INDICATORS = [
    "ddos-guard",
    "ddos guard",
    "checking your browser before accessing",
    "complete the manual check to continue",
    "could not verify your browser automatically",
]

# Markers that exist only in raw markup: the bypassers scan rendered innerText, where
# a script src or a <title> never appears. The title match is scoped to the tag on
# purpose - hosts word the rest of that sentence differently, and matching "checking
# your browser" as free text would trip on any page that merely discusses a challenge.
_RAW_HTML_MARKERS = (
    "<title>checking your browser",
    "/cdn-cgi/challenge-platform",
    "/.well-known/ddos-guard/",
)

# An interstitial is a few KB of markup. Past that it is a real page that happens to
# mention a marker - a protected site links its own DDoS-Guard endpoints on every page.
MAX_CHALLENGE_HTML_CHARS = 64 * 1024


def challenge_marker(html: str) -> str | None:
    """Return the marker proving `html` is an unsolved challenge page, or None.

    Only meaningful for a response that already carries a challenge status: the
    markers appear on protected sites' real pages too, so the status is what
    separates "blocked" from "served".
    """
    if not html or len(html) > MAX_CHALLENGE_HTML_CHARS:
        return None
    lowered = html.lower()
    for marker in (*_RAW_HTML_MARKERS, *DDOS_GUARD_INDICATORS, *CLOUDFLARE_INDICATORS):
        if marker in lowered:
            return marker
    return None
