"""Differential parity check against the Go reference tool.

Not a seam: this is verification. The Go tool ``chromedb`` is built from the
pinned revision and run over the same synthetic fixture as ``chromedbpy``; the
two raw stdout streams are compared line by line, byte for byte, so the parity
claim behind the drop-in replacement is checked rather than asserted.

The check is skipped, never failed, when the Go toolchain or the network is
unavailable, so the rest of the suite stays hermetic.

The fixture deliberately excludes the divergences documented by earlier
tickets, because they are out of this check's scope:

- a value that is valid JSON but contains invalid UTF-8 inside a string
  (Python keeps valid JSON, Go writes the raw byte);
- ``deb`` magic bytes (the Go detector is non-deterministic across runs);
- an ``avif`` sample whose ``ftyp`` box length is ``0x14``;
- types only the Go library can detect (``macho``, ``dex``, ISO-9660, real JP2
  signatures).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from profile_fixture import ProfileFixture

# The pinned behavioural reference: noperator/chromedb at 2025-05-07. Its
# stdout is the single source of truth for every field name, mime value,
# conversion name, and error string.
GO_REVISION = "ea058a1058188e78b5e67262df556cdf8c8787c5"
GO_PACKAGE = "github.com/noperator/chromedb/cmd/chromedb"

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="both tools are macOS-only, so parity is only meaningful there",
)

PASSWORD = "testpass"

# Cookie cases proven identical to the Go output by the throwaway harnesses
# used while implementing cookies (version 18) and the version-24 digest.
_COOKIE_CASES = [
    ("sid", "abc123", ".example.com"),
    ("html", "<a>&</a>", "example.com"),
    ("uni", "\u4e2d\u6587-caf\u00e9", "example.com"),
    ("bsff", "a\tb\x08c\x0cd", "example.com"),
    ("del", "x\x7fy", "example.com"),
    ("para", "p\u2028q\u2029r", "example.com"),
    ("emoji", "s\U0001f600t", "example.com"),
    ("ctl2", "\x01\x07\x0b\x1b", "example.com"),
    ("quote", 'he said "hi" \\ back', "example.com"),
    ("name<", "<n&>", "example.com"),
    ("host<", "v", "ho<st>&.example.com"),
    ("empty", "", "example.com"),
]

_COOKIE_CASES_V24 = [
    ("sid", "abc123", ".example.com"),
    ("html", "<a>&</a>", "example.com"),
    ("uni", "\u4e2d\u6587-caf\u00e9", "example.com"),
    ("bsff", "a\tb\x08c\x0cd", "example.com"),
    ("para", "p\u2028q\u2029r", "example.com"),
    ("emoji", "s\U0001f600t", "example.com"),
    ("empty", "", "example.com"),
]

# The Go tool reads only ``BROWSER_PASSWORD``; the same variable drives the
# Python tool. ``v11`` is not a decryptable version, so both tools print the
# Go wording and still emit the row with an empty value.
_UNDECRYPTABLE_COOKIE = ("bad", "example.com", b"v11" + b"\x00" * 16)

# (storage_key, script_key, value, charset) cases proven identical by the
# local storage harness. ``charset`` is the prefix byte on disk: 0 is
# little-endian UTF-16, 1 is raw bytes.
_LOCAL_STORAGE_RECORDS = [
    ("https://a.test", "plain text", "hello world", 0),
    ("https://a.test", "json obj", '{"b":1,"a":[true,null]}', 0),
    ("https://a.test", "json arr", "[1,2,3]", 0),
    ("https://a.test", "json num", "42", 0),
    ("https://a.test", "big num", "123456789012345678901234567890", 0),
    ("https://a.test", "json str", '"already a string"', 0),
    ("https://a.test", "html", "<b>&amp;</b>", 0),
    ("https://a.test", "empty", "", 0),
    ("https://a.test", "iso", b"caf\xe9", 1),
    ("https://a.test", "png", b"\x89PNG\r\n\x1a\npayload", 1),
    ("https://a.test", "jpg", b"\xff\xd8\xff\xe0payload", 1),
    ("https://a.test", "gif", b"GIF89apayload", 1),
    ("https://a.test", "pdf", b"%PDF-1.4\npayload", 1),
    ("https://a.test", "zip", b"PK\x03\x04payload", 1),
    ("https://a.test", "ico", b"\x00\x00\x01\x00payload", 1),
    ("https://a.test", "random", b"\x00\x01\x02\x03\xff\xfe\x80", 1),
    ("https://a.test", "exe", b"MZ\x01payload", 1),
    ("https://a.test", "m4a", b"\x00\x00\x00\x20ftypM4A payload", 1),
    ("https://a.test", "rar", b"Rar!\x1a\x07\x00payload", 1),
    ("https://a.test", "sqlite", b"SQLite format 3\x00", 1),
    ("https://a.test", "heic", b"\x00\x00\x00\x14ftypheic\x00\x00\x00\x00heic", 1),
    (
        "https://a.test",
        "avif",
        b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf",
        1,
    ),
    (
        "https://a.test",
        "apng",
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00acTL\x00\x00\x00\x00",
        1,
    ),
    (
        "https://a.test",
        "odt",
        b"PK\x03\x04"
        + b"\x00" * 26
        + b"mimetype"
        + b"application/vnd.oasis.opendocument.text",
        1,
    ),
    ("https://a.test", "br", b"\xce\xb2\xcf\x81\x01payload", 1),
    ("https://a.test", "lz4", b"\x04\x22\x4d\x18payload", 1),
    ("https://a.test", "lzo", b"\x89LZO\x00\r\n\x1apayload", 1),
    ("https://a.test", "xcf", b"gimp xcf v\x01payload", 1),
    (
        "https://a.test",
        "jpx",
        b"\x00\x00\x00\x0c" + b"\x00" * 12 + b"ftypjp2 " + b"\x00" * 30,
        1,
    ),
]


@pytest.fixture(scope="module")
def go_chromedb() -> Path:
    """Build the pinned Go reference once, caching the binary by revision."""
    binary = _binary_cache_dir() / "chromedb"
    if binary.is_file():
        return binary

    go = shutil.which("go")
    if go is None:
        pytest.skip("Go toolchain is not on PATH; cannot build the reference tool")

    cache_dir = _binary_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    install = subprocess.run(
        [go, "install", f"{GO_PACKAGE}@{GO_REVISION}"],
        env=dict(os.environ, GOBIN=str(cache_dir)),
        capture_output=True,
        text=True,
    )
    if install.returncode != 0 or not binary.is_file():
        pytest.skip(
            "could not build the Go reference at "
            f"{GO_REVISION}: {install.stderr.strip()}"
        )
    return binary


def _binary_cache_dir() -> Path:
    """A stable, out-of-repo cache keyed by the pinned revision."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return root / "chromedbpy-go" / GO_REVISION


def _run_go(go_chromedb: Path, mode: str, profile: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, BROWSER_PASSWORD=PASSWORD)
    return subprocess.run(
        [str(go_chromedb), mode, "-p", str(profile)],
        capture_output=True,
        env=env,
    )


def _run_python(mode: str, profile: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, BROWSER_PASSWORD=PASSWORD)
    return subprocess.run(
        [sys.executable, "-m", "chromedbpy", mode, "-p", str(profile)],
        capture_output=True,
        env=env,
    )


def _assert_same_output(
    go: subprocess.CompletedProcess, py: subprocess.CompletedProcess
) -> None:
    """Compare the two raw stdout streams line by line, byte for byte."""
    if go.returncode != 0 or py.returncode != 0:
        pytest.fail(
            f"both tools must succeed on a valid fixture: "
            f"go rc={go.returncode} py rc={py.returncode}\n"
            f"  go stderr: {go.stderr.decode('utf-8', 'replace')}\n"
            f"  py stderr: {py.stderr.decode('utf-8', 'replace')}"
        )

    go_lines = go.stdout.split(b"\n")
    py_lines = py.stdout.split(b"\n")
    if go_lines == py_lines:
        return

    for index, (go_line, py_line) in enumerate(zip(go_lines, py_lines), start=1):
        if go_line != py_line:
            pytest.fail(
                f"line {index} differs byte for byte:\n"
                f"  go: {go_line!r}\n"
                f"  py: {py_line!r}"
            )
    pytest.fail(f"line count differs: go={len(go_lines)} py={len(py_lines)}")


def test_cookie_output_matches_go(go_chromedb):
    with ProfileFixture(password=PASSWORD, version=18) as fixture:
        for name, value, host_key in _COOKIE_CASES:
            fixture.add_cookie(name=name, value=value, host_key=host_key)
        with sqlite3.connect(fixture.path / "Cookies") as conn:
            name, host_key, encrypted_value = _UNDECRYPTABLE_COOKIE
            conn.execute(
                "INSERT INTO cookies (name, value, host_key, encrypted_value)"
                " VALUES (?, ?, ?, ?)",
                (name, "", host_key, encrypted_value),
            )
            conn.commit()

        go = _run_go(go_chromedb, "-c", fixture.path)
        py = _run_python("-c", fixture.path)

    _assert_same_output(go, py)


def test_version_24_cookie_output_matches_go(go_chromedb):
    with ProfileFixture(password=PASSWORD, version=24) as fixture:
        for name, value, host_key in _COOKIE_CASES_V24:
            fixture.add_cookie(name=name, value=value, host_key=host_key)

        go = _run_go(go_chromedb, "-c", fixture.path)
        py = _run_python("-c", fixture.path)

    _assert_same_output(go, py)


def test_local_storage_output_matches_go(go_chromedb):
    with ProfileFixture() as fixture:
        for storage_key, script_key, value, charset in _LOCAL_STORAGE_RECORDS:
            fixture.add_record(
                storage_key=storage_key,
                script_key=script_key,
                value=value,
                charset=charset,
            )
        fixture.add_meta(
            storage_key="https://a.test",
            timestamp_microseconds=13300000000000000,
            size=7,
        )

        go = _run_go(go_chromedb, "-ls", fixture.path)
        py = _run_python("-ls", fixture.path)

    _assert_same_output(go, py)