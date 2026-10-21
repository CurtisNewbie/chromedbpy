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
- A value that survives quoting has its mime set to ``text/plain`` and then
  immediately overwritten by the sniffed content type.
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
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import filetype
import plyvel
from mimesniff import what as detect_content_type

_META_PREFIX = b"META:"
_RECORD_PREFIX = b"_"
_SEPARATOR = b"\x00"

_DEFAULT_MIME = "application/octet-stream"
_JSON_MIME = "application/json"

# The names of the transformations, spelled the way the Go tool puts them in
# the `conversions` array (they name internal Go calls).
_QUOTE_CONVERSION = "strconv.Quote"
_BASE64_CONVERSION = "base64.StdEncoding.EncodeToString"

_JSON_WHITESPACE = frozenset(b" \t\n\r")


class LocalStorageError(Exception):
    """A local storage read that failed, worded the way the Go tool words it."""


class _DirectoryCopyError(Exception):
    """The leveldb directory could not be walked.

    The Go tool prints this error on its own when copying the directory and
    again as the LevelDB open error that ends the mode; the wording carries the
    OS error, so the message is built exactly where the walk fails.
    """


@dataclass(frozen=True)
class StorageMetadata:
    """A ``META:`` entry: parsed for validation, never emitted."""

    storage_key: str
    timestamp_microseconds: int
    size: int


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


def _parse_metadata(storage_key: bytes, value: bytes) -> StorageMetadata:
    """Decode a ``META:`` value: a two-field varint protobuf message.

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

    return StorageMetadata(
        storage_key=storage_key.decode("utf-8", "replace"),
        timestamp_microseconds=timestamp,
        size=size,
    )


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
                    _parse_metadata(key[len(_META_PREFIX) :], value)
                elif key.startswith(_RECORD_PREFIX):
                    record = _record_from_entry(key, value)
                    if record is not None:
                        records.append(record)
        return records


# --- Value conversion ------------------------------------------------------


def _decode_rune(raw: bytes, pos: int) -> tuple[int | None, int]:
    """Decode one UTF-8 rune the way Go's ``utf8.DecodeRune`` does.

    An invalid sequence is reported as ``(None, 1)``: Go answers with the
    replacement rune and a width of one byte.
    """
    first = raw[pos]
    if 0xC2 <= first <= 0xDF:
        width = 2
    elif 0xE0 <= first <= 0xEF:
        width = 3
    elif 0xF0 <= first <= 0xF4:
        width = 4
    else:
        return None, 1

    chunk = raw[pos : pos + width]
    if len(chunk) < width:
        return None, 1
    try:
        return ord(chunk.decode("utf-8")), width
    except UnicodeDecodeError:
        return None, 1


def _is_print(rune: int) -> bool:
    """Go's ``strconv.IsPrint``: letters, marks, numbers, punctuation, symbols."""
    if 0x20 <= rune <= 0x7E:
        return True
    return unicodedata.category(chr(rune))[0] in "LMNPS"


# Go's named escapes; everything else below the printable range becomes \xNN.
_NAMED_ESCAPES = {
    0x07: "\\a",
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0B: "\\v",
    0x0C: "\\f",
    0x0D: "\\r",
}


def _go_quote(raw: bytes) -> str:
    """Quote ``raw`` the way Go's ``strconv.Quote`` does.

    This is deliberately not ``json.dumps``: Go leaves printable non-ASCII
    alone, uses ``\\xNN`` for control bytes outside its named escapes, and
    escapes runes it does not consider printable as ``\\uNNNN``. Its output is
    not always valid JSON, which is the whole point of the branch that reads it.
    """
    out = ['"']
    pos = 0
    while pos < len(raw):
        byte = raw[pos]
        if byte < 0x80:
            pos += 1
            if byte in (0x22, 0x5C):
                out.append("\\" + chr(byte))
            elif byte in _NAMED_ESCAPES:
                out.append(_NAMED_ESCAPES[byte])
            elif 0x20 <= byte <= 0x7E:
                out.append(chr(byte))
            else:
                out.append(f"\\x{byte:02x}")
            continue

        rune, width = _decode_rune(raw, pos)
        if rune is None:
            out.append(f"\\x{byte:02x}")
        elif _is_print(rune):
            out.append(raw[pos : pos + width].decode("utf-8"))
        elif rune < 0x10000:
            out.append(f"\\u{rune:04x}")
        else:
            out.append(f"\\U{rune:08x}")
        pos += width
    out.append('"')
    return "".join(out)


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

    quoted = _go_quote(raw)
    if _is_valid_json(quoted.encode("utf-8")):
        # The Go tool sets text/plain here and then overwrites it with the
        # sniffed type; the overwrite is intentional, so it is reproduced.
        mime = detect_content_type(raw).split(";")[0]
        return mime, [_QUOTE_CONVERSION], quoted.encode("utf-8")

    encoded = base64.standard_b64encode(raw).decode("ascii")
    magic = filetype.match(raw)
    mime = magic.mime if magic is not None else _DEFAULT_MIME
    return mime, [_BASE64_CONVERSION, _QUOTE_CONVERSION], _go_quote(
        encoded.encode("utf-8")
    ).encode("utf-8")


# --- Writing the line ------------------------------------------------------


def _json_string(raw: bytes) -> str:
    """Quote a string field the way Go's ``json.Marshal`` does.

    Differences from Python's ``json``: ``<``, ``>`` and ``&`` are escaped,
    U+2028 and U+2029 are escaped, and an invalid UTF-8 byte becomes the
    replacement character escape.
    """
    out = ['"']
    pos = 0
    while pos < len(raw):
        byte = raw[pos]
        if byte < 0x80:
            pos += 1
            if byte == 0x22:
                out.append('\\"')
            elif byte == 0x5C:
                out.append("\\\\")
            elif byte == 0x0A:
                out.append("\\n")
            elif byte == 0x0D:
                out.append("\\r")
            elif byte == 0x09:
                out.append("\\t")
            elif byte < 0x20:
                out.append(f"\\u{byte:04x}")
            elif byte == 0x3C:
                out.append("\\u003c")
            elif byte == 0x3E:
                out.append("\\u003e")
            elif byte == 0x26:
                out.append("\\u0026")
            else:
                out.append(chr(byte))
            continue

        rune, width = _decode_rune(raw, pos)
        if rune is None:
            out.append("\\ufffd")
        elif rune in (0x2028, 0x2029):
            out.append(f"\\u{rune:04x}")
        else:
            out.append(raw[pos : pos + width].decode("utf-8"))
        pos += width
    out.append('"')
    return "".join(out)


def _compact(fragment: bytes) -> str:
    """Compact and HTML-escape a raw JSON fragment, as Go's ``json`` does.

    ``json.RawMessage`` goes through ``json.Marshal``, which drops whitespace
    outside strings and HTML-escapes the fragment just like a plain string.
    """
    out: list[str] = []
    in_string = False
    pos = 0
    while pos < len(fragment):
        byte = fragment[pos]
        if byte == 0x5C and in_string:
            out.append(fragment[pos : pos + 2].decode("ascii", "replace"))
            pos += 2
            continue
        if byte == 0x22:
            in_string = not in_string
            out.append('"')
            pos += 1
            continue
        if byte in _JSON_WHITESPACE and not in_string:
            pos += 1
            continue
        if byte == 0x3C:
            out.append("\\u003c")
            pos += 1
            continue
        if byte == 0x3E:
            out.append("\\u003e")
            pos += 1
            continue
        if byte == 0x26:
            out.append("\\u0026")
            pos += 1
            continue

        if byte < 0x80:
            out.append(chr(byte))
            pos += 1
            continue
        rune, width = _decode_rune(fragment, pos)
        if rune is None:
            out.append("\ufffd")
        elif rune in (0x2028, 0x2029):
            out.append(f"\\u{rune:04x}")
        else:
            out.append(fragment[pos : pos + width].decode("utf-8"))
        pos += width
    return "".join(out)


def _record_to_json(record: _Record) -> str:
    """Render one record as a JSON object, fields in the Go struct's order."""
    mime, conversions, value = _convert_value(record)
    converted = ",".join(_json_string(item.encode("ascii")) for item in conversions)
    return (
        '{"storage_key":'
        + _json_string(record.storage_key)
        + ',"script_key":'
        + _json_string(record.script_key)
        + ',"charset":'
        + _json_string(record.charset.encode("ascii"))
        + ',"mime":'
        + _json_string(mime.encode("ascii"))
        + ',"conversions":['
        + converted
        + '],"value":'
        + _compact(value)
        + "}"
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
        print(_record_to_json(record))
    return 0