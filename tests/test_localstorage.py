"""Local storage mode: drive ``chromedbpy -ls`` in-process and assert on stdout.

One seam, the command line entry point. The expected lines are literals taken
from the Go reference tool's output for the same input, not recomputed the way
the implementation computes them.
"""

from __future__ import annotations

import base64
import json
import sys

import pytest

from chromedbpy import leveldb
from chromedbpy.cli import main
from profile_fixture import ProfileFixture


@pytest.fixture(autouse=True)
def _darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def _run(profile_path, capsys):
    """Drive ``-ls`` over ``profile_path`` and return (exit code, stdout lines)."""
    exit_code = main(["-p", str(profile_path), "-ls"])
    return exit_code, capsys.readouterr().out.splitlines()


def _put(profile_path, key: bytes, value: bytes) -> None:
    """Write one raw LevelDB entry the fixture cannot build."""
    database = leveldb.DB(
        str(profile_path / "Local Storage" / "leveldb"), create_if_missing=True
    )
    try:
        database.put(key, value)
    finally:
        database.close()


def test_json_value_is_emitted_as_raw_json(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value='{"a": 1}',
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"application/json","conversions":[],'
        '"value":{"a":1}}'
    ]


def test_text_value_is_emitted_as_a_quoted_string(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="hello",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"text/plain",'
        '"conversions":["strconv.Quote"],"value":"hello"}'
    ]


def test_empty_value_is_emitted_as_an_empty_string(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com", script_key="token", value=""
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"text/plain",'
        '"conversions":["strconv.Quote"],"value":""}'
    ]


def test_raw_bytes_value_reports_the_iso_8859_1_charset(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="plain",
            charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"ISO-8859-1","mime":"text/plain",'
        '"conversions":["strconv.Quote"],"value":"plain"}'
    ]


def test_script_key_is_decoded_by_the_same_prefix_rule_as_the_value(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="plain",
            charset=0,
            script_key_charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines[0].startswith(
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE",'
    )


def test_json_object_value_is_compacted(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value='{"a": 1, "b": [1, 2]}',
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"application/json","conversions":[],'
        '"value":{"a":1,"b":[1,2]}}'
    ]


def test_html_characters_in_a_json_value_are_escaped(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value='{"a":"<b>&</b>"}',
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"application/json","conversions":[],'
        '"value":{"a":"\\u003cb\\u003e\\u0026\\u003c/b\\u003e"}}'
    ]


def test_text_value_is_content_sniffed_after_quoting(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="<html>hi</html>",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"text/html",'
        '"conversions":["strconv.Quote"],"value":"\\u003chtml\\u003ehi\\u003c/html\\u003e"}'
    ]


def test_printable_non_ascii_text_is_kept_readable(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="café",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"text/plain",'
        '"conversions":["strconv.Quote"],"value":"café"}'
    ]


def test_json_number_too_large_for_a_float_is_emitted_as_a_string(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="1e400",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"application/json","conversions":[],'
        '"value":"1e400"}'
    ]


def test_json_integer_that_fits_a_float_is_emitted_as_a_number(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value="123456789012345678901234567890",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"UTF-16-LE","mime":"application/json","conversions":[],'
        '"value":123456789012345678901234567890}'
    ]


def test_metadata_entries_are_not_emitted_and_records_come_in_key_order(capsys):
    with ProfileFixture() as fixture:
        fixture.add_meta(
            storage_key="https://b.example",
            timestamp_microseconds=13300000000000000,
            size=5,
        )
        fixture.add_meta(
            storage_key="https://a.example",
            timestamp_microseconds=13300000000000000,
            size=5,
        )
        fixture.add_record(
            storage_key="https://b.example", script_key="token", value="second"
        )
        fixture.add_record(
            storage_key="https://a.example", script_key="token", value="first"
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert [json.loads(line)["storage_key"] for line in lines] == [
        "https://a.example",
        "https://b.example",
    ]
    assert [json.loads(line)["value"] for line in lines] == ["first", "second"]


def test_record_key_without_a_separator_is_skipped(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com", script_key="token", value="kept"
        )
        _put(
            fixture.path,
            b"_no-separator",
            b"\x01ignored",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert [json.loads(line)["value"] for line in lines] == ["kept"]


def test_unknown_charset_prefix_is_reported_as_an_error(capsys):
    with ProfileFixture() as fixture:
        _put(
            fixture.path,
            b"_https://example.com\x00"
            + bytes([0])
            + "token".encode("utf-16-le"),
            bytes([2]) + b"hello",
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 1
    assert lines == [
        "Error opening LevelDB: failed to decode value: "
        "unknown string encoding prefix: 2"
    ]


def test_missing_leveldb_directory_is_reported(tmp_path, capsys):
    exit_code, lines = _run(tmp_path, capsys)

    leveldb_path = tmp_path / "Local Storage" / "leveldb"
    assert exit_code == 1
    assert lines == [
        f"Error copying directory: lstat {leveldb_path}: no such file or directory",
        f"Error opening LevelDB: lstat {leveldb_path}: no such file or directory",
    ]


def test_live_profile_leveldb_is_never_opened_for_writing(capsys):
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com", script_key="token", value="hello"
        )
        leveldb_dir = fixture.path / "Local Storage" / "leveldb"
        leveldb_dir.chmod(0o500)
        try:
            # A write-open of the live directory would fail without write
            # permission; the dump only works because it reads a copy.
            exit_code, lines = _run(fixture.path, capsys)
        finally:
            leveldb_dir.chmod(0o700)

    assert exit_code == 0
    assert [json.loads(line)["value"] for line in lines] == ["hello"]


def test_binary_value_is_base64_encoded_then_quoted(capsys):
    # A PNG header cannot survive strconv.Quote as valid JSON, so the Go tool
    # base64-encodes the bytes and quotes the result, naming both steps.
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value=b"\x89PNG\r\n\x1a\npayload",
            charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"ISO-8859-1","mime":"image/png",'
        '"conversions":["base64.StdEncoding.EncodeToString","strconv.Quote"],'
        '"value":"iVBORw0KGgpwYXlsb2Fk"}'
    ]


def test_binary_mime_uses_the_go_string_for_a_shared_type(capsys):
    # ICO: the Python port calls it image/x-icon, the Go tool the
    # reference binary emits image/vnd.microsoft.icon.
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value=b"\x00\x00\x01\x00payload",
            charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"ISO-8859-1","mime":"image/vnd.microsoft.icon",'
        '"conversions":["base64.StdEncoding.EncodeToString","strconv.Quote"],'
        '"value":"AAABAHBheWxvYWQ="}'
    ]


def test_binary_mime_is_octet_stream_when_the_go_tool_has_no_matcher(capsys):
    # Brotli: the Python port recognises it, the Go tool has no matcher, so the
    # Go tool leaves the application/octet-stream default in place.
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value=b"\xce\xb2\xcf\x81\x01payload",
            charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"ISO-8859-1","mime":"application/octet-stream",'
        '"conversions":["base64.StdEncoding.EncodeToString","strconv.Quote"],'
        '"value":"zrLPgQFwYXlsb2Fk"}'
    ]


def test_binary_mime_uses_the_go_name_for_a_type_go_recognises_by_another_name(
    capsys,
):
    # HEIC: the Go tool detects the same bytes as image/heif.
    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value=b"\x00\x00\x00\x14ftypheic\x00\x00\x00\x00heic",
            charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert lines == [
        '{"storage_key":"https://example.com","script_key":"token",'
        '"charset":"ISO-8859-1","mime":"image/heif",'
        '"conversions":["base64.StdEncoding.EncodeToString","strconv.Quote"],'
        '"value":"AAAAFGZ0eXBoZWljAAAAAGhlaWM="}'
    ]


def test_arbitrary_binary_bytes_still_produce_a_valid_json_line(capsys):
    payload = bytes(range(256))

    with ProfileFixture() as fixture:
        fixture.add_record(
            storage_key="https://example.com",
            script_key="token",
            value=payload,
            charset=1,
        )

        exit_code, lines = _run(fixture.path, capsys)

    assert exit_code == 0
    assert len(lines) == 1

    parsed = json.loads(lines[0])
    assert parsed["mime"] == "application/octet-stream"
    assert parsed["conversions"] == [
        "base64.StdEncoding.EncodeToString",
        "strconv.Quote",
    ]
    assert base64.b64decode(parsed["value"]) == payload