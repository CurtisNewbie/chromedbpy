"""Coverage for the synthetic profile fixture.

The fixture is test infrastructure, not a seam: these tests open the databases
the helper wrote and check the rows are there. The command line seam is covered
in ``test_cli.py``.
"""

from __future__ import annotations

import sqlite3

import plyvel
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from profile_fixture import ProfileFixture

PASSWORD = "correct horse battery staple"
# SHA256("example.com"), an independent literal for the version-24 prefix.
DOMAIN_DIGEST = bytes.fromhex(
    "a379a6f6eeafb9a55e378c118034e2751e682fab9f2d30ab13d2125586ce1947"
)


def _decrypt_cookie(encrypted_value: bytes, password: str) -> bytes:
    """Decrypt a fixture value with an implementation independent of the tool.

    The KDF here is ``cryptography``'s (OpenSSL), not the standard library's
    ``hashlib``; matching values show the fixture used the shared derivation.
    """
    assert encrypted_value[:3] == b"v10"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003
    )
    key = kdf.derive(password.encode("utf-8"))
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    padded = decryptor.update(encrypted_value[3:]) + decryptor.finalize()
    return padded[: -padded[-1]]


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _read_meta(data: bytes) -> tuple[int, int]:
    """Decode the two-varint protobuf a ``META:`` value holds."""
    assert data[0] == 0x08  # field 1, varint
    timestamp, pos = _read_varint(data, 1)
    assert data[pos] == 0x10  # field 2, varint
    size, _ = _read_varint(data, pos + 1)
    return timestamp, size


def test_fixture_writes_cookies_database_with_chosen_version_and_rows():
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(name="sid", value="secret-session", host_key="example.com")

        with sqlite3.connect(fixture.path / "Cookies") as conn:
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'version'"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT name, value, host_key, encrypted_value FROM cookies"
            ).fetchall()

    assert int(version) == 18
    assert len(rows) == 1
    name, value, host_key, encrypted_value = rows[0]
    assert (name, value, host_key) == ("sid", "", "example.com")
    assert bytes(encrypted_value).startswith(b"v10")


def test_fixture_writes_local_storage_records_and_meta():
    with ProfileFixture(password=PASSWORD) as fixture:
        fixture.add_record(
            storage_key="https://example.com", script_key="token", value="hello"
        )
        fixture.add_meta(
            storage_key="https://example.com",
            timestamp_microseconds=13300000000000000,
            size=5,
        )

        db = plyvel.DB(str(fixture.path / "Local Storage" / "leveldb"))
        try:
            items = dict(db)
        finally:
            db.close()

    record_key = next(k for k in items if k.startswith(b"_"))
    meta_key = next(k for k in items if k.startswith(b"META:"))

    storage_key, _, script_part = record_key[1:].partition(b"\x00")
    assert storage_key == b"https://example.com"
    assert script_part == b"\x00" + "token".encode("utf-16-le")
    assert items[record_key] == b"\x00" + "hello".encode("utf-16-le")

    assert meta_key == b"META:https://example.com"
    assert _read_meta(items[meta_key]) == (13300000000000000, 5)


def test_fixture_removes_its_directory_when_discarded():
    with ProfileFixture(password=PASSWORD) as fixture:
        root = fixture.path
        assert root.is_dir()

    assert not root.exists()


def test_fixture_encrypts_cookie_values_with_the_shared_key_derivation():
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(name="sid", value="secret-session", host_key="example.com")

        with sqlite3.connect(fixture.path / "Cookies") as conn:
            encrypted_value = conn.execute(
                "SELECT encrypted_value FROM cookies WHERE name = 'sid'"
            ).fetchone()[0]

    assert _decrypt_cookie(bytes(encrypted_value), PASSWORD) == b"secret-session"


def test_fixture_prepends_sha256_host_digest_at_version_24():
    with ProfileFixture(password=PASSWORD, version=24) as fixture:
        fixture.add_cookie(name="sid", value="secret-session", host_key="example.com")

        with sqlite3.connect(fixture.path / "Cookies") as conn:
            encrypted_value = conn.execute(
                "SELECT encrypted_value FROM cookies WHERE name = 'sid'"
            ).fetchone()[0]

    plaintext = _decrypt_cookie(bytes(encrypted_value), PASSWORD)
    assert plaintext[:32] == DOMAIN_DIGEST
    assert plaintext[32:] == b"secret-session"