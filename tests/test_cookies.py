"""Cookies mode, driven through the command line seam.

Each test builds a synthetic profile with ``ProfileFixture``, runs ``chromedbpy
-p <dir> -c`` in-process with ``BROWSER_PASSWORD`` set, and asserts on the JSON
lines a user sees on stdout.
"""

from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import sys

import keyring
import pytest

from chromedbpy.cli import main
from profile_fixture import ProfileFixture

PASSWORD = "correct horse battery staple"


def _run_cookies(profile_path, monkeypatch, capsys, password=PASSWORD):
    """Drive ``-c`` against ``profile_path`` and return ``(exit_code, lines)``."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("BROWSER_PASSWORD", password)
    exit_code = main(["-p", str(profile_path), "-c"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    return exit_code, lines


def test_encrypted_cookie_is_dumped_and_decrypted(tmp_path, monkeypatch, capsys):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    assert len(lines) == 1
    cookie = json.loads(lines[0])
    assert cookie["domain"] == "example.com"
    assert cookie["name"] == "sid"
    assert cookie["value"] == "secret-session"
    assert base64.b64decode(cookie["encrypted_value"]).startswith(b"v10")


def test_password_is_trimmed_of_surrounding_whitespace(tmp_path, monkeypatch, capsys):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        exit_code, lines = _run_cookies(
            fixture.path, monkeypatch, capsys, password=f"  {PASSWORD}\n"
        )

    assert exit_code == 0
    assert json.loads(lines[0])["value"] == "secret-session"


def test_browser_password_takes_precedence_over_the_keychain(
    tmp_path, monkeypatch, capsys
):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the keychain must not be consulted")

    monkeypatch.setattr(keyring, "get_password", refuse)

    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    assert json.loads(lines[0])["value"] == "secret-session"


def test_keychain_service_is_inferred_from_the_profile_path(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("BROWSER_PASSWORD", raising=False)

    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        arc_profile = tmp_path / "Arc" / "User Data" / "Default"
        arc_profile.parent.mkdir(parents=True)
        shutil.copytree(fixture.path, arc_profile)

    requested = []

    def fake_get_password(service, account):
        requested.append((service, account))
        return PASSWORD if service == "Arc Safe Storage" else None

    monkeypatch.setattr(keyring, "get_password", fake_get_password)

    exit_code = main(["-p", str(arc_profile), "-c"])
    lines = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    assert json.loads(lines[0])["value"] == "secret-session"
    assert requested[0] == ("Arc Safe Storage", "Arc")


def test_keychain_falls_back_over_the_common_services(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("BROWSER_PASSWORD", raising=False)

    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )

        def fake_get_password(service, account):
            return PASSWORD if service == "Brave Safe Storage" else None

        monkeypatch.setattr(keyring, "get_password", fake_get_password)

        exit_code = main(["-p", str(fixture.path), "-c"])
        lines = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    assert json.loads(lines[0])["value"] == "secret-session"


def test_value_is_encoded_the_way_go_encodes_json(
    tmp_path, monkeypatch, capsys
):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="markup",
            value="caf\u00e9 <&> \u2028",
            host_key="example.com",
        )
        _, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert '"value":"caf\u00e9 \\u003c\\u0026\\u003e \\u2028"' in lines[0]


def test_control_characters_use_go_short_and_long_escapes(
    tmp_path, monkeypatch, capsys
):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        fixture.add_cookie(
            name="raw",
            value="a\tb\nc\x08d\x0ce\x01f",
            host_key="example.com",
        )
        _, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert '"value":"a\\tb\\nc\\bd\\fe\\u0001f"' in lines[0]
    assert "\\u0008" not in lines[0]
    assert "\\u000c" not in lines[0]


@pytest.mark.parametrize(
    ("encrypted", "message"),
    [
        (b"v11" + b"\x00" * 16, "unsported encrypted value version: v11"),
        (b"v1", "encrypted length less than 3"),
        (b"v10", "not enough bits"),
        (
            b"v10" + b"\x00" * 15,
            "decrypted data block length is not a multiple of 16",
        ),
    ],
)
def test_failed_decryption_is_reported_and_emitted_empty(
    tmp_path, monkeypatch, capsys, encrypted, message
):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        with sqlite3.connect(fixture.path / "Cookies") as conn:
            conn.execute(
                "INSERT INTO cookies (name, value, host_key, encrypted_value)"
                " VALUES (?, ?, ?, ?)",
                ("bad", "", "example.com", encrypted),
            )
            conn.commit()
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    assert lines[0] == f"Failed to decrypt cookie bad: {message}"
    cookie = json.loads(lines[1])
    assert cookie["name"] == "bad"
    assert cookie["value"] == ""
    assert base64.b64decode(cookie["encrypted_value"]) == encrypted


def test_plaintext_cookie_keeps_the_database_value(
    tmp_path, monkeypatch, capsys
):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        with sqlite3.connect(fixture.path / "Cookies") as conn:
            conn.execute(
                "INSERT INTO cookies (name, value, host_key, encrypted_value)"
                " VALUES (?, ?, ?, ?)",
                ("plain", "already-readable", "example.com", b""),
            )
            conn.commit()
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    cookie = json.loads(lines[0])
    assert cookie["name"] == "plain"
    assert cookie["value"] == "already-readable"
    assert cookie["encrypted_value"] == ""


def test_version_24_cookie_is_decrypted_with_the_domain_digest_dropped(
    tmp_path, monkeypatch, capsys
):
    with ProfileFixture(password=PASSWORD, version=24) as fixture:
        fixture.add_cookie(
            name="sid", value="secret-session", host_key="example.com"
        )
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    assert len(lines) == 1
    cookie = json.loads(lines[0])
    assert cookie["domain"] == "example.com"
    assert cookie["name"] == "sid"
    assert cookie["value"] == "secret-session"


def test_version_24_value_matches_the_older_version(
    tmp_path, monkeypatch, capsys
):
    cases = [
        ("sid", "abc123", "example.com"),
        ("uni", "caf\u00e9 \u4e2d\u6587", "example.com"),
        ("host<", "v", "ho<st>&.example.com"),
        ("empty", "", "example.com"),
    ]

    def dump(version):
        with ProfileFixture(password=PASSWORD, version=version) as fixture:
            for name, value, host in cases:
                fixture.add_cookie(name=name, value=value, host_key=host)
            _, lines = _run_cookies(fixture.path, monkeypatch, capsys)
        return [json.loads(line)["value"] for line in lines]

    assert dump(24) == dump(18)


def test_version_24_domain_hash_mismatch_is_reported(
    tmp_path, monkeypatch, capsys
):
    with ProfileFixture(password=PASSWORD, version=24) as fixture:
        encrypted = fixture.encrypt(b"secret-session", host_key="evil.com")
        with sqlite3.connect(fixture.path / "Cookies") as conn:
            conn.execute(
                "INSERT INTO cookies (name, value, host_key, encrypted_value)"
                " VALUES (?, ?, ?, ?)",
                ("sid", "", "example.com", encrypted),
            )
            conn.commit()
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    assert lines[0] == (
        "Failed to decrypt cookie sid: domain hash verification failed"
    )
    cookie = json.loads(lines[1])
    assert cookie["name"] == "sid"
    assert cookie["value"] == ""


def test_version_24_short_payload_is_reported(tmp_path, monkeypatch, capsys):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        short = fixture.encrypt(b"")
        fixture.set_version(24)
        with sqlite3.connect(fixture.path / "Cookies") as conn:
            conn.execute(
                "INSERT INTO cookies (name, value, host_key, encrypted_value)"
                " VALUES (?, ?, ?, ?)",
                ("short", "", "example.com", short),
            )
            conn.commit()
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    assert lines[0] == (
        "Failed to decrypt cookie short: decrypted data too short for db"
        " version 24, expected more than 32 bytes but got 16"
    )
    assert json.loads(lines[1])["value"] == ""


def test_version_24_empty_domain_skips_the_hash_check(
    tmp_path, monkeypatch, capsys
):
    with ProfileFixture(password=PASSWORD, version=24) as fixture:
        encrypted = fixture.encrypt(b"secret-session", host_key="evil.com")
        with sqlite3.connect(fixture.path / "Cookies") as conn:
            conn.execute(
                "INSERT INTO cookies (name, value, host_key, encrypted_value)"
                " VALUES (?, ?, ?, ?)",
                ("sid", "", "", encrypted),
            )
            conn.commit()
        exit_code, lines = _run_cookies(fixture.path, monkeypatch, capsys)

    assert exit_code == 0
    cookie = json.loads(lines[0])
    assert cookie["name"] == "sid"
    assert cookie["value"] == "secret-session"


def test_missing_cookies_database_is_reported(tmp_path, monkeypatch, capsys):
    exit_code, lines = _run_cookies(tmp_path, monkeypatch, capsys)

    assert exit_code != 0
    assert lines[0].startswith("Error opening Cookies database:")