"""The one piece of key material the tool and the tests must agree on.

A Chromium storage password is turned into the 16-byte AES key with
PBKDF2-HMAC-SHA1 over the salt ``saltysalt``. Cookies mode decrypts with this
key and the test fixture encrypts with it, so the two sides cannot drift apart.
Password resolution (``BROWSER_PASSWORD`` or the keychain) is built on top of
this function elsewhere.
"""

from __future__ import annotations

import hashlib

_SALT = b"saltysalt"
_ITERATIONS = 1003
_KEY_LENGTH = 16


def derive_key(password: str) -> bytes:
    """Derive the 16-byte Chromium storage key from ``password``."""
    return hashlib.pbkdf2_hmac(
        "sha1", password.encode("utf-8"), _SALT, _ITERATIONS, dklen=_KEY_LENGTH
    )