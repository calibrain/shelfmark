"""Chomikuj mobile API request token.

Chomikuj signs every mobile API request with a static app constant:

    md5(PATH_WITHOUT_LEADING_SLASH + ('?' + query if query else '') + body + SECRET)

The digest is lowercase hexadecimal MD5 over the UTF-8 bytes of that string.
The caller must sign the exact query string it puts on the wire (same order,
same encoding); this module never re-encodes or reorders the query.
"""

import hashlib

# The 20-character secret shipped in Chomikuj's mobile app.
CHOMIKUJ_SECRET = "wzrwYua$.DSe8suk!`'2"  # noqa: S105 - API-dictated app constant


def sign_token(path: str, query: str = "", body: str = "") -> str:
    """Return the lowercase MD5 request token for one mobile API call.

    ``path`` may carry any number of leading slashes; they are stripped and
    never signed. ``query`` is the query string without the leading ``?`` and
    is signed exactly as given. ``body`` is the raw request body exactly as
    sent (for example the compact JSON login payload).
    """
    signed = path.lstrip("/")
    if query:
        signed = f"{signed}?{query}"
    signed += body + CHOMIKUJ_SECRET
    return hashlib.md5(signed.encode("utf-8"), usedforsecurity=False).hexdigest()
