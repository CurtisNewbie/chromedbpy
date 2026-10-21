"""Build a synthetic Chromium profile on disk for the test suite.

Not a seam and not a public API: the tests drive ``chromedbpy`` the way a user
does. This helper writes the on-disk databases those runs read, encrypting
cookie values with the same key derivation the tool decrypts with
(``chromedbpy.keys``).
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from pathlib import Path

import plyvel
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from chromedbpy.keys import derive_key

# Cookie value encryption: the macOS Chrome scheme the tool decrypts. Fixture
# side only.
_V10_PREFIX = b"v10"
_AES_IV = b" " * 16

# Local storage: the leading byte of a value or script key selects its charset.
_UTF16LE = 0
_ISO88591 = 1

_COOKIES_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER);
CREATE TABLE cookies (
    name TEXT,
    value TEXT,
    host_key TEXT,
    encrypted_value BLOB
);
"""


def _encode(text_or_bytes: str | bytes, charset: int) -> bytes:
    """Prefix ``text_or_bytes`` with the byte that names its charset."""
    if isinstance(text_or_bytes, bytes):
        body = text_or_bytes
    elif charset == _UTF16LE:
        body = text_or_bytes.encode("utf-16-le")
    elif charset == _ISO88591:
        body = text_or_bytes.encode("latin-1")
    else:
        raise ValueError(f"unknown local storage charset prefix: {charset}")
    return bytes([charset]) + body


def _encode_varint(value: int) -> bytes:
    """Base-128 varint, the protobuf wire encoding for a non-negative int."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _encode_meta(timestamp_microseconds: int, size: int) -> bytes:
    """Encode a ``META:`` value.

    Chromium stores local storage metadata as a protobuf message with two
    varint fields: field 1 is a microsecond timestamp (measured from the 1601
    epoch) and field 2 is the size in bytes. A tag is ``(field << 3) | type``
    with wire type 0 for varints, so the two tags are 0x08 and 0x10.
    """
    return (
        bytes([(1 << 3) | 0])
        + _encode_varint(timestamp_microseconds)
        + bytes([(2 << 3) | 0])
        + _encode_varint(size)
    )


class ProfileFixture:
    """A temporary profile directory built from synthetic data."""

    def __init__(self, *, password: str = "", version: int = 0) -> None:
        self._root: Path | None = None
        self._cookies: sqlite3.Connection | None = None

        self._root = Path(tempfile.mkdtemp(prefix="chromedbpy-profile-"))
        self.path = self._root
        self.version = version
        self._key = derive_key(password)

        self._cookies = sqlite3.connect(self._root / "Cookies")
        self._cookies.executescript(_COOKIES_SCHEMA)
        self.set_version(version)

        self._leveldb = self._root / "Local Storage" / "leveldb"
        self._leveldb.mkdir(parents=True)
        plyvel.DB(str(self._leveldb), create_if_missing=True).close()

    def set_version(self, version: int) -> None:
        """Set ``meta.version`` and use it for later cookie encryption."""
        self.version = version
        self._cookies.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)",
            (version,),
        )
        self._cookies.commit()

    def encrypt(self, plaintext: bytes, host_key: str = "") -> bytes:
        """Encrypt ``plaintext`` the way Chrome stores a cookie value."""
        body = plaintext
        if self.version >= 24:
            # Chrome 130+ (db version >= 24) prepends SHA256(host_key).
            body = hashlib.sha256(host_key.encode("utf-8")).digest() + plaintext
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(body) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self._key), modes.CBC(_AES_IV)).encryptor()
        return _V10_PREFIX + encryptor.update(padded) + encryptor.finalize()

    def add_cookie(self, *, name: str, value: str, host_key: str) -> None:
        """Insert a cookie row whose ``encrypted_value`` carries ``value``.

        ``value`` is the plaintext; the plaintext column is left empty, the way
        a real profile stores an encrypted cookie.
        """
        encrypted_value = self.encrypt(value.encode("utf-8"), host_key)
        self._cookies.execute(
            "INSERT INTO cookies (name, value, host_key, encrypted_value)"
            " VALUES (?, ?, ?, ?)",
            (name, "", host_key, encrypted_value),
        )
        self._cookies.commit()

    def _put(self, key: bytes, value: bytes) -> None:
        """Write one LevelDB entry, opening and closing the database around it.

        The database is closed between writes so a test can reopen it (a live
        ``plyvel`` handle holds a lock).
        """
        db = plyvel.DB(str(self._leveldb), create_if_missing=True)
        try:
            db.put(key, value)
        finally:
            db.close()

    def add_record(
        self,
        *,
        storage_key: str,
        script_key: str,
        value: str | bytes,
        charset: int = _UTF16LE,
        script_key_charset: int | None = None,
    ) -> None:
        """Write a ``_`` record: storage key, script key, and value.

        The key is ``b"_" + storage_key + b"\\x00" + prefixed_script_key``; the
        value and script key each carry their charset-prefix byte.
        """
        if script_key_charset is None:
            script_key_charset = charset
        key = (
            b"_"
            + storage_key.encode("utf-8")
            + b"\x00"
            + _encode(script_key, script_key_charset)
        )
        self._put(key, _encode(value, charset))

    def add_meta(
        self,
        *,
        storage_key: str,
        timestamp_microseconds: int = 0,
        size: int = 0,
    ) -> None:
        """Write a ``META:`` entry for ``storage_key``."""
        self._put(
            b"META:" + storage_key.encode("utf-8"),
            _encode_meta(timestamp_microseconds, size),
        )

    def close(self) -> None:
        """Close the databases and delete the temporary directory."""
        if self._cookies is not None:
            self._cookies.close()
            self._cookies = None
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)
            self._root = None

    def __enter__(self) -> "ProfileFixture":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter shutdown edge case
            pass