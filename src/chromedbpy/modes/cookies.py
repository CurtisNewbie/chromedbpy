"""Cookies mode: dump the profile's cookies as JSON lines.

``chromedbpy -p <dir> -c`` reads the profile's ``Cookies`` SQLite database and
writes one JSON object per line: ``domain``, ``name``, ``encrypted_value`` (the
raw encrypted bytes, standard base64) and the decrypted ``value``.

The storage password comes from ``BROWSER_PASSWORD`` when it is set and
non-empty, and from the macOS keychain otherwise. Decryption is the macOS
Chromium scheme: AES-128-CBC under a PBKDF2 key, an IV of sixteen spaces, a
``v10`` prefix, and PKCS7 padding. A row whose decryption fails is reported on
stdout in the reference tool's wording and is still emitted with an empty
``value``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3

import keyring
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from chromedbpy.keys import derive_key

_AES_BLOCK_SIZE = 16
_AES_IV = b" " * 16
_V10_PREFIX = b"v10"
_DOMAIN_DIGEST_SIZE = 32

# The keychain service each browser profile uses, keyed by a marker in the
# profile's path. The marker is matched case-sensitively, so an unrelated
# directory whose name merely contains "arc" is not mistaken for Arc.
_SERVICE_BY_MARKER = (
    ("Chromium", "Chromium Safe Storage"),
    ("Brave", "Brave Safe Storage"),
    ("Arc", "Arc Safe Storage"),
    ("Edge", "Microsoft Edge Safe Storage"),
    ("Chrome", "Chrome Safe Storage"),
)

# The services tried, in turn, when the path names no known browser.
_COMMON_SERVICES = (
    "Chrome Safe Storage",
    "Chromium Safe Storage",
    "Brave Safe Storage",
    "Microsoft Edge Safe Storage",
    "Arc Safe Storage",
)

_SERVICE_SUFFIX = " Safe Storage"


class _DecryptError(Exception):
    """A cookie value that could not be decrypted."""


def _trimmed_env_password() -> str | None:
    """Return the trimmed ``BROWSER_PASSWORD``, or ``None`` when unset/blank."""
    raw = os.environ.get("BROWSER_PASSWORD")
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed or None


def _infer_service(profile_path: str) -> str | None:
    """Return the keychain service named by ``profile_path``, if any."""
    for marker, service in _SERVICE_BY_MARKER:
        if marker in profile_path:
            return service
    return None


def _account_for(service: str) -> str:
    """The keychain account a service name uses (its name without the suffix)."""
    if service.endswith(_SERVICE_SUFFIX):
        return service[: -len(_SERVICE_SUFFIX)]
    return service


def _keychain_password(profile_path: str) -> str | None:
    """Read the storage password from the keychain, or ``None`` if absent."""
    candidates: list[str] = []
    inferred = _infer_service(profile_path)
    if inferred is not None:
        candidates.append(inferred)
    for service in _COMMON_SERVICES:
        if service not in candidates:
            candidates.append(service)
    for service in candidates:
        try:
            password = keyring.get_password(service, _account_for(service))
        except keyring.errors.KeyringError:
            password = None
        if password:
            return password.strip()
    return None


def _resolve_password(profile_path: str) -> str | None:
    """Resolve the storage password from the environment or the keychain."""
    from_env = _trimmed_env_password()
    if from_env is not None:
        return from_env
    return _keychain_password(profile_path)


def _read_cookies(cookies_path: str) -> tuple[list[tuple], int]:
    """Read the cookie rows and the database version from ``cookies_path``."""
    if not os.path.isfile(cookies_path):
        raise sqlite3.OperationalError("unable to open database file")
    connection = sqlite3.connect(cookies_path)
    try:
        db_version = 0
        try:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'version'"
            ).fetchone()
            if row is not None and row[0] is not None:
                db_version = int(row[0])
        except sqlite3.Error:
            db_version = 0
        rows = connection.execute(
            "SELECT name, value, host_key, encrypted_value FROM cookies"
        ).fetchall()
    finally:
        connection.close()
    return rows, db_version


def _decrypt_value(
    encrypted_value: bytes, key: bytes, db_version: int, host_key: str
) -> str:
    """Decrypt one cookie value, reproducing the reference tool's errors.

    ``db_version`` selects the plaintext layout. Version 24 and above (Chrome
    130+) prepend a SHA256 digest of the host key, which is verified against
    ``host_key`` and then dropped. The digest is only verified when ``host_key``
    is non-empty, matching the reference tool.
    """
    if len(encrypted_value) < 3:
        raise _DecryptError("encrypted length less than 3")
    version = encrypted_value[:3]
    if version != _V10_PREFIX:
        raise _DecryptError(
            "unsported encrypted value version: "
            + version.decode("utf-8", "replace")
        )
    ciphertext = encrypted_value[3:]
    if len(ciphertext) == 0:
        raise _DecryptError("not enough bits")
    if len(ciphertext) % _AES_BLOCK_SIZE != 0:
        raise _DecryptError(
            f"decrypted data block length is not a multiple of {_AES_BLOCK_SIZE}"
        )
    decryptor = Cipher(algorithms.AES(key), modes.CBC(_AES_IV)).decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()
    padding_length = decrypted[-1]
    if padding_length > _AES_BLOCK_SIZE:
        raise _DecryptError(
            f"invalid last block padding length: {padding_length}"
        )
    end = len(decrypted) - padding_length
    if db_version >= 24:
        if len(decrypted) <= _DOMAIN_DIGEST_SIZE:
            raise _DecryptError(
                "decrypted data too short for db version "
                f"{db_version}, expected more than {_DOMAIN_DIGEST_SIZE} bytes "
                f"but got {len(decrypted)}"
            )
        if host_key != "":
            digest = hashlib.sha256(host_key.encode("utf-8")).digest()
            if digest != decrypted[:_DOMAIN_DIGEST_SIZE]:
                raise _DecryptError("domain hash verification failed")
        plaintext = decrypted[_DOMAIN_DIGEST_SIZE:end]
    else:
        plaintext = decrypted[:end]
    return plaintext.decode("utf-8", "replace")


def _json_string(value: str) -> str:
    """Encode ``value`` the way Go's ``encoding/json`` encodes a string.

    Go HTML-escapes ``<``, ``>`` and ``&`` and always escapes the line and
    paragraph separators, while leaving printable non-ASCII characters as UTF-8.
    """
    out = ['"']
    for character in value:
        if character == '"':
            out.append('\\"')
        elif character == "\\":
            out.append("\\\\")
        elif character == "\n":
            out.append("\\n")
        elif character == "\r":
            out.append("\\r")
        elif character == "\t":
            out.append("\\t")
        elif character == "\b":
            out.append("\\b")
        elif character == "\f":
            out.append("\\f")
        elif character < " ":
            out.append("\\u%04x" % ord(character))
        elif character == "<":
            out.append("\\u003c")
        elif character == ">":
            out.append("\\u003e")
        elif character == "&":
            out.append("\\u0026")
        elif ord(character) == 0x2028:
            out.append("\\u2028")
        elif ord(character) == 0x2029:
            out.append("\\u2029")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)


def _format_cookie(
    domain: str, name: str, encrypted_value: bytes, value: str
) -> str:
    """Render a cookie as the one-line JSON object the reference tool writes."""
    encoded = base64.b64encode(encrypted_value).decode("ascii")
    return (
        "{"
        + '"domain":' + _json_string(domain) + ","
        + '"name":' + _json_string(name) + ","
        + '"encrypted_value":' + _json_string(encoded) + ","
        + '"value":' + _json_string(value)
        + "}"
    )


def run(profile_path: str) -> int:
    """Dump cookies for the profile at ``profile_path``; return the exit code."""
    cookies_path = os.path.join(profile_path, "Cookies")
    try:
        rows, db_version = _read_cookies(cookies_path)
    except sqlite3.Error as err:
        print(f"Error opening Cookies database: {err}")
        return 1

    password = _resolve_password(profile_path)
    if password is None:
        print("Error getting key: BROWSER_PASSWORD environment variable not set")
        return 1
    key = derive_key(password)

    for name, value, host_key, encrypted_value in rows:
        decrypted = value
        if encrypted_value:
            try:
                decrypted = _decrypt_value(
                    encrypted_value, key, db_version, host_key
                )
            except _DecryptError as err:
                print(f"Failed to decrypt cookie {name}: {err}")
                decrypted = ""
        print(
            _format_cookie(
                host_key, name, bytes(encrypted_value or b""), decrypted
            )
        )
    return 0