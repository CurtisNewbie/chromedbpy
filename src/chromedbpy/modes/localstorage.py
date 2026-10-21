"""Local storage mode: dump a profile's local storage as JSON lines.

The profile's LevelDB directory is copied to a temporary directory and opened
there, because the Python binding cannot open a database read-only and the live
profile must never be opened for writing.

The output contract is the Go reference tool's, quirks included (see
``docs/adr/0001-faithful-parity-with-go-chromedb.md``). Four of those quirks
shape this module:

- Values are converted the way Go's ``strconv.Quote`` and ``encoding/json``
  behave, not the way Python's ``json`` module behaves. Go keeps printable
  non-ASCII as-is, writes ``\\xNN`` for some control bytes, and HTML-escapes
  ``<``, ``>``, ``&`` and U+2028/U+2029.
- A value that survives quoting gets its mime from content sniffing, not from
  the ``text/plain`` the Go tool sets first and immediately overwrites, so only
  the sniffed type is observable.
- A JSON number the Go decoder cannot turn into a float64 is emitted as a
  string instead of a number.
- A leveldb directory that cannot be walked is reported twice: once where the
  Go tool copies the directory and again as the LevelDB open error that ends
  the mode.
"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import filetype
import plyvel
from mimesniff import what as detect_content_type

from chromedbpy import goencoding

_META_PREFIX = b"META:"
_RECORD_PREFIX = b"_"
_SEPARATOR = b"\x00"

_DEFAULT_MIME = "application/octet-stream"
_JSON_MIME = "application/json"

# The Python `filetype` package and the Go `h2non/filetype` library the
# reference tool vendors are ports of one another but not the same type table.
# Six matchers are shared yet spell their MIME differently, a handful of types
# exist in only one of the two libraries, and Go recognises a few images under
# a broader neighbouring type. The Go tool's output is the contract, so its
# answer is restored here, keyed by the Python port's file extension, instead
# of scattering special cases or patching the library.
_GO_MIME_OVERRIDES = {
    # Same magic-byte matcher, different MIME string in the two ports.
    "exe": "application/vnd.microsoft.portable-executable",
    "ico": "image/vnd.microsoft.icon",
    "m4a": "audio/m4a",
    "rar": "application/vnd.rar",
    "sqlite": "application/vnd.sqlite3",
    # Go matches the same bytes as a neighbouring, more broadly named type.
    "heic": "image/heif",
    "apng": "image/png",
    "odt": "application/zip",
    "ods": "application/zip",
    "odp": "application/zip",
    # Python has a matcher Go lacks, so the Go tool keeps the default. (The Go
    # tool's own Debian type is nondeterministic: its archive matchers iterate a
    # map, so the same bytes come out as either the Debian or the `ar` string.)
    "deb": "application/vnd.debian.binary-package",
    "avif": _DEFAULT_MIME,
    "br": _DEFAULT_MIME,
    # Python's JPX layout; a real JP2 signature is matched below as `image/jp2`.
    "jpx": _DEFAULT_MIME,
    "lz4": _DEFAULT_MIME,
    "lzo": _DEFAULT_MIME,
    "xcf": _DEFAULT_MIME,
}

# Types the Go library detects and the Python port has no matcher for at all.
# These are matched by hand below, in the library's own byte layout. A real JP2
# signature is the one overlap: Python answers `jpx`, Go answers `jp2`, and the
# override table is keyed by the Python extension, so the Go signature is
# checked first.
_GO_JP2_MIME = "image/jp2"
_GO_MACHO_MIME = "application/x-mach-binary"
_GO_DEX_MIME = "application/vnd.android.dex"
_GO_DEY_MIME = "application/vnd.android.dey"
_GO_ISO_MIME = "application/x-iso9660-image"

_MACHO_MAGICS = (
    b"\xfe\xed\xfa\xcf",
    b"\xfe\xed\xfa\xce",
    b"\xbe\xba\xfe\xca",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)

# The names of the transformations, spelled the way the Go tool puts them in
# the `conversions` array (they name internal Go calls).
_QUOTE_CONVERSION = "strconv.Quote"
_BASE64_CONVERSION = "base64.StdEncoding.EncodeToString"


class LocalStorageError(Exception):
    """A local storage read that failed, worded the way the Go tool words it."""


class _DirectoryCopyError(Exception):
    """The leveldb directory could not be walked.

    The Go tool prints this error on its own when copying the directory and
    again as the LevelDB open error that ends the mode; the wording carries the
    OS error, so the message is built exactly where the walk fails.
    """


@dataclass(frozen=True)
class _Record:
    """One ``_`` entry with its charset prefix already stripped."""

    storage_key: bytes
    script_key: bytes
    charset: str
    value: bytes


# --- Reading the LevelDB directory ----------------------------------------


def _decode_string(raw: bytes) -> tuple[bytes, str]:
    """Split a charset-prefixed field into its Go-string bytes and charset name.

    The Go reference holds a decoded value as a byte string: the UTF-16 branch
    decodes and re-encodes to UTF-8, the raw branch keeps the bytes unchanged.
    Keeping bytes here means quoting and sniffing see what the Go tool sees.
    """
    if not raw:
        raise LocalStorageError("failed to decode UTF-16-LE: unexpected EOF")

    prefix = raw[0]
    if prefix == 0:
        try:
            text = raw[1:].decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise LocalStorageError(f"failed to decode UTF-16-LE: {exc}") from exc
        return text.encode("utf-8"), "UTF-16-LE"
    if prefix == 1:
        return raw[1:], "ISO-8859-1"
    raise LocalStorageError(f"unknown string encoding prefix: {prefix}")


def _consume_varint(data: bytes, pos: int) -> tuple[int | None, int]:
    """Decode a base-128 varint; ``None`` when the field is truncated."""
    value = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            return None, pos
    return None, pos


def _parse_metadata(value: bytes) -> None:
    """Validate a ``META:`` value: a two-field varint protobuf message.

    Field 1 is a Chromium microsecond timestamp and field 2 is the size in
    bytes. Nothing is emitted from this; it is parsed because the Go tool
    parses it, and a malformed message aborts the dump there too.
    """
    tag, pos = _consume_varint(value, 0)
    field, wire = (None, None) if tag is None else (tag >> 3, tag & 0x07)
    if field != 1 or wire != 0:
        raise LocalStorageError(
            "Expected field number 1 with varint type, got field number "
            f"{field} with wire type {wire}"
        )

    timestamp, pos = _consume_varint(value, pos)
    if timestamp is None:
        raise LocalStorageError("Failed to decode timestamp")

    tag, pos = _consume_varint(value, pos)
    field, wire = (None, None) if tag is None else (tag >> 3, tag & 0x07)
    if field != 2 or wire != 0:
        raise LocalStorageError(
            "Expected field number 2 with varint type, got field number "
            f"{field} with wire type {wire}"
        )

    size, _ = _consume_varint(value, pos)
    if size is None:
        raise LocalStorageError("Failed to decode size")


def _record_from_entry(key: bytes, value: bytes) -> _Record | None:
    """Turn a ``_`` entry into a record, or ``None`` when it does not split."""
    parts = key[len(_RECORD_PREFIX) :].split(_SEPARATOR, 1)
    if len(parts) != 2:
        return None

    try:
        script_key, _ = _decode_string(parts[1])
    except LocalStorageError as exc:
        raise LocalStorageError(f"failed to decode script key: {exc}") from exc

    try:
        decoded, charset = _decode_string(value)
    except LocalStorageError as exc:
        raise LocalStorageError(f"failed to decode value: {exc}") from exc

    return _Record(
        storage_key=parts[0], script_key=script_key, charset=charset, value=decoded
    )


def _os_error_text(exc: OSError) -> str:
    """Word an OS error the way Go's ``syscall.Errno`` words it.

    Go's syscall errors read lowercase (``no such file or directory``);
    Python capitalizes the platform's message.
    """
    if exc.strerror is None:
        return str(exc)
    return exc.strerror.lower()


def _copy_leveldb(leveldb_dir: Path, target: Path) -> None:
    """Copy the source directory's files into ``target`` the way the Go tool does.

    The Go tool walks the source directory to copy the LevelDB files it finds.
    A path it cannot stat aborts that walk with ``lstat <path>: <reason>`` and
    a directory it cannot list aborts it with ``open <path>: <reason>``. A
    source that is not a directory is a single file with no LevelDB files to
    copy, which the Go tool walks past without error.
    """
    try:
        info = os.lstat(leveldb_dir)
    except OSError as exc:
        raise _DirectoryCopyError(
            f"lstat {leveldb_dir}: {_os_error_text(exc)}"
        ) from exc

    if not stat.S_ISDIR(info.st_mode):
        return

    try:
        names = os.listdir(leveldb_dir)
    except OSError as exc:
        raise _DirectoryCopyError(
            f"open {leveldb_dir}: {_os_error_text(exc)}"
        ) from exc

    for name in names:
        source = leveldb_dir / name
        if source.is_file():
            # Copy contents only: the live directory's permissions say nothing
            # about what the copy needs, and the copy is opened for writing.
            shutil.copyfile(source, target / name)


def _load_records(leveldb_dir: Path) -> list[_Record]:
    """Read every record from a copy of ``leveldb_dir``, in key order."""
    with tempfile.TemporaryDirectory(prefix="chromedbpy-leveldb-") as tmp:
        target = Path(tmp) / "leveldb"
        target.mkdir()
        _copy_leveldb(leveldb_dir, target)

        records: list[_Record] = []
        with plyvel.DB(str(target), create_if_missing=True) as db:
            for key, value in db:
                if key.startswith(_META_PREFIX):
                    # Parsed for validation, never emitted, matching the Go tool.
                    _parse_metadata(value)
                elif key.startswith(_RECORD_PREFIX):
                    record = _record_from_entry(key, value)
                    if record is not None:
                        records.append(record)
        return records


# --- Value conversion ------------------------------------------------------


class _NumberLiteral(str):
    """A JSON number kept as the literal text Go's ``json.Number`` holds."""


class _InvalidJson(Exception):
    """Raised internally when the decoded value is not a JSON document."""


def _reject_constant(name: str) -> object:
    raise _InvalidJson(name)


def _parse_json(data: bytes) -> object:
    """Decode ``data`` the way Go's ``json`` package does, numbers as literals.

    Invalid UTF-8 inside a string is accepted, as in Go, so the bytes are
    replaced before parsing; the bytes themselves are what gets emitted.
    """
    return json.loads(
        data.decode("utf-8", "replace"),
        parse_int=_NumberLiteral,
        parse_float=_NumberLiteral,
        parse_constant=_reject_constant,
    )


def _is_valid_json(data: bytes) -> bool:
    try:
        _parse_json(data)
    except (ValueError, RecursionError, _InvalidJson):
        return False
    return True


def _overflows_float64(literal: _NumberLiteral) -> bool:
    """Whether ``literal`` cannot be converted to a float64, as Go reports it."""
    try:
        value = float(literal)
    except (OverflowError, ValueError):
        return True
    return math.isinf(value)


def _go_only_mime(raw: bytes) -> str | None:
    """Match the types only the Go library knows, in its own byte layout."""
    if len(raw) > 3 and raw[:4] in _MACHO_MAGICS:
        return _GO_MACHO_MIME
    # ``dey`` wraps a ``dex`` at a fixed offset; check it before plain ``dex``.
    if (
        len(raw) > 100
        and raw[0:4] == b"dey\n"
        and raw[40:44] == b"dex\n"
        and raw[76] == 0x70
    ):
        return _GO_DEY_MIME
    if len(raw) > 36 and raw[0:4] == b"dex\n" and raw[36] == 0x70:
        return _GO_DEX_MIME
    if len(raw) > 32773 and raw[32769:32774] == b"CD001":
        return _GO_ISO_MIME
    if (
        len(raw) > 12
        and raw[0:4] == b"\x00\x00\x00\x0c"
        and raw[4:8] == b"\x6a\x50\x20\x20"
        and raw[8:12] == b"\x0d\x0a\x87\x0a"
        and raw[12] == 0x00
    ):
        return _GO_JP2_MIME
    return None


def _magic_mime(raw: bytes) -> str:
    """Match magic bytes and answer with the MIME string the Go tool reports."""
    go_only = _go_only_mime(raw)
    if go_only is not None:
        return go_only
    magic = filetype.match(raw)
    if magic is None:
        return _DEFAULT_MIME
    return _GO_MIME_OVERRIDES.get(magic.extension, magic.mime)


def _convert_value(record: _Record) -> tuple[str, list[str], bytes]:
    """Return ``(mime, conversions, value fragment)`` for a record's value."""
    raw = record.value

    if _is_valid_json(raw):
        parsed = _parse_json(raw)
        if isinstance(parsed, _NumberLiteral) and _overflows_float64(parsed):
            # Go's decoder cannot turn it into a float64, so it is emitted as
            # a string instead of the bare number.
            return _JSON_MIME, [], b'"' + parsed.encode("ascii") + b'"'
        return _JSON_MIME, [], raw

    quoted = goencoding.quote(raw)
    if _is_valid_json(quoted):
        # A quotable value's mime comes from content sniffing (the Go tool
        # computes ``text/plain`` first and overwrites it, so only the sniffed
        # type is observable).
        mime = detect_content_type(raw).split(";")[0]
        return mime, [_QUOTE_CONVERSION], quoted

    encoded = base64.standard_b64encode(raw)
    return (
        _magic_mime(raw),
        [_BASE64_CONVERSION, _QUOTE_CONVERSION],
        goencoding.quote(encoded),
    )


# --- Writing the line ------------------------------------------------------


def _record_to_json(record: _Record) -> bytes:
    """Render one record as a JSON object, fields in the Go struct's order."""
    mime, conversions, value = _convert_value(record)
    converted = b",".join(
        goencoding.json_string(item.encode("ascii")) for item in conversions
    )
    return (
        b'{"storage_key":'
        + goencoding.json_string(record.storage_key)
        + b',"script_key":'
        + goencoding.json_string(record.script_key)
        + b',"charset":'
        + goencoding.json_string(record.charset.encode("ascii"))
        + b',"mime":'
        + goencoding.json_string(mime.encode("ascii"))
        + b',"conversions":['
        + converted
        + b'],"value":'
        + goencoding.compact(value)
        + b"}"
    )


def run(profile_path: str) -> int:
    """Dump local storage for the profile at ``profile_path``; return the exit code."""
    leveldb_dir = Path(profile_path) / "Local Storage" / "leveldb"

    try:
        records = _load_records(leveldb_dir)
    except _DirectoryCopyError as exc:
        # The Go tool prints the failing walk once where it copies and again as
        # the LevelDB open error that ends the mode.
        print(f"Error copying directory: {exc}")
        print(f"Error opening LevelDB: {exc}")
        return 1
    except (LocalStorageError, plyvel.Error, OSError) as exc:
        print(f"Error opening LevelDB: {exc}")
        return 1

    for record in records:
        # Written as bytes: a valid JSON value is emitted byte for byte, which
        # can leave invalid UTF-8 in the line.
        sys.stdout.buffer.write(_record_to_json(record) + b"\n")
    return 0