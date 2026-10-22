"""Public library API: call the functions directly and assert on the dicts.

The library surface raises where the command line prints, so these tests build a
synthetic profile with ``ProfileFixture`` and assert on the returned records.
"""

from __future__ import annotations

import base64

import keyring
import pytest

import chromedbpy
from chromedbpy import read_cookies, read_local_storage
from profile_fixture import ProfileFixture

PASSWORD = "correct horse battery staple"


def test_read_local_storage_returns_a_text_record():
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="hello",
        )
        records = read_local_storage(str(fixture.path))

    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "storage_key",
        "script_key",
        "charset",
        "mime",
        "conversions",
        "value",
    }
    assert record["storage_key"] == "https://example.com"
    assert record["script_key"] == "token"
    assert record["charset"] == "UTF-16-LE"
    assert record["mime"] == "text/plain"
    assert record["conversions"] == ["strconv.Quote"]
    assert record["value"] == "hello"


def test_read_local_storage_decodes_a_json_value():
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value='{"a": 1}',
        )
        record = read_local_storage(str(fixture.path))[0]

    assert record["value"] == {"a": 1}
    assert record["mime"] == "application/json"


def test_read_cookies_returns_a_decrypted_record(monkeypatch):
    monkeypatch.setenv("BROWSER_PASSWORD", PASSWORD)
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        records = read_cookies(str(fixture.path))

    assert len(records) == 1
    cookie = records[0]
    assert set(cookie) == {"domain", "name", "encrypted_value", "value"}
    assert cookie["domain"] == "example.com"
    assert cookie["name"] == "sid"
    assert cookie["value"] == "secret-session"
    assert base64.b64decode(cookie["encrypted_value"]).startswith(b"v10")


def test_read_cookies_raises_when_no_password_is_available(monkeypatch):
    monkeypatch.delenv("BROWSER_PASSWORD", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda *_args, **_kwargs: None)

    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        with pytest.raises(RuntimeError):
            read_cookies(str(fixture.path))


def test_public_functions_are_reexported_from_the_package():
    assert chromedbpy.read_local_storage is chromedbpy.api.read_local_storage
    assert chromedbpy.read_cookies is chromedbpy.api.read_cookies