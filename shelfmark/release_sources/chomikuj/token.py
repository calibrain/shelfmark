"""Chomikuj mobile API request token.

Chomikuj signs every mobile API request with a static signing constant:

    md5(PATH_WITHOUT_LEADING_SLASH + ('?' + query if query else '') + body + SIGNING_CONSTANT)

``CHOMIKUJ_SIGNING_CONSTANT`` is the 20-character value shipped in Chomikuj's
mobile app: a public app value, not a user credential. The MD5 digest is
mandated by Chomikuj's mobile API for request signing, not used for password
storage or security, and is computed with ``usedforsecurity=False``. The digest
is lowercase hexadecimal over the UTF-8 bytes of that string. The caller must
sign the exact query string it puts on the wire (same order, same encoding);
this module never re-encodes or reorders the query.
"""

import hashlib

# The 20-character static signing constant shipped in Chomikuj's mobile app.
# It is a public app value, not a user credential.
CHOMIKUJ_SIGNING_CONSTANT = "wzrwYua$.DSe8suk!`'2"


def sign_token(path: str, query: str = "", body: str = "") -> str:
    """Return the lowercase MD5 request token for one mobile API call.

    ``path`` may carry any number of leading slashes; they are stripped and
    never signed. ``query`` is the query string without the leading ``?`` and
    is signed exactly as given. ``body`` is the raw request body exactly as
    sent (for example the compact JSON login payload). The MD5 digest is
    protocol-mandated request signing (``usedforsecurity=False``), never
    password storage.
    """
    signed = path.lstrip("/")
    if query:
        signed = f"{signed}?{query}"
    signed += body + CHOMIKUJ_SIGNING_CONSTANT
    return hashlib.md5(signed.encode("utf-8"), usedforsecurity=False).hexdigest()
