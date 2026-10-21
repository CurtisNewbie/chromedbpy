"""Go's string and JSON encoding, shared by the cookies and local storage modes.

The reference tool's stdout is the contract (see
``docs/adr/0001-faithful-parity-with-go-chromedb.md``), and Python's own ``json``
module and ``str`` handling disagree with Go in ways that show up in the output.
Go's algorithms are reproduced here instead, once, for both modes:

- :func:`json_string` mirrors ``encoding/json``'s string encoding: short escapes
  for ``\\b`` and ``\\f``, ``\\u00XX`` for the other control bytes, HTML escaping
  of ``<``, ``>`` and ``&``, and U+2028/U+2029 escaped. An invalid UTF-8 byte
  becomes ``\\ufffd``.
- :func:`quote` mirrors ``strconv.Quote``: printable non-ASCII is kept, control
  bytes outside its named escapes become ``\\xNN``, unprintable runes become
  ``\\uNNNN``/``\\UNNNNNNNN``. Its output is not always valid JSON.
- :func:`compact` mirrors marshalling a ``json.RawMessage``: insignificant space
  outside strings is dropped and the HTML and U+2028/U+2029 escapes applied, byte
  for byte, leaving invalid UTF-8 untouched (so the result can itself be invalid
  UTF-8).
"""

from __future__ import annotations

import unicodedata

_JSON_WHITESPACE = frozenset(b" \t\n\r")

# Go's named escapes for strconv.Quote; anything else below the printable range
# becomes \xNN.
_QUOTE_NAMED_ESCAPES = {
    0x07: b"\\a",
    0x08: b"\\b",
    0x09: b"\\t",
    0x0A: b"\\n",
    0x0B: b"\\v",
    0x0C: b"\\f",
    0x0D: b"\\r",
}


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


def quote(raw: bytes) -> bytes:
    """Quote ``raw`` the way Go's ``strconv.Quote`` does."""
    out = bytearray(b'"')
    pos = 0
    while pos < len(raw):
        byte = raw[pos]
        if byte < 0x80:
            pos += 1
            if byte in (0x22, 0x5C):
                out += b"\\" + bytes([byte])
            elif byte in _QUOTE_NAMED_ESCAPES:
                out += _QUOTE_NAMED_ESCAPES[byte]
            elif 0x20 <= byte <= 0x7E:
                out.append(byte)
            else:
                out += f"\\x{byte:02x}".encode("ascii")
            continue

        rune, width = _decode_rune(raw, pos)
        if rune is None:
            out += f"\\x{byte:02x}".encode("ascii")
        elif _is_print(rune):
            out += raw[pos : pos + width]
        elif rune < 0x10000:
            out += f"\\u{rune:04x}".encode("ascii")
        else:
            out += f"\\U{rune:08x}".encode("ascii")
        pos += width
    out.append(0x22)
    return bytes(out)


def json_string(raw: bytes) -> bytes:
    """Encode ``raw`` the way Go's ``encoding/json`` encodes a string field."""
    out = bytearray(b'"')
    pos = 0
    while pos < len(raw):
        byte = raw[pos]
        if byte < 0x80:
            pos += 1
            if byte == 0x22:
                out += b'\\"'
            elif byte == 0x5C:
                out += b"\\\\"
            elif byte == 0x0A:
                out += b"\\n"
            elif byte == 0x0D:
                out += b"\\r"
            elif byte == 0x09:
                out += b"\\t"
            elif byte == 0x08:
                out += b"\\b"
            elif byte == 0x0C:
                out += b"\\f"
            elif byte < 0x20:
                out += f"\\u{byte:04x}".encode("ascii")
            elif byte == 0x3C:
                out += b"\\u003c"
            elif byte == 0x3E:
                out += b"\\u003e"
            elif byte == 0x26:
                out += b"\\u0026"
            else:
                out.append(byte)
            continue

        rune, width = _decode_rune(raw, pos)
        if rune is None:
            out += b"\\ufffd"
        elif rune in (0x2028, 0x2029):
            out += f"\\u{rune:04x}".encode("ascii")
        else:
            out += raw[pos : pos + width]
        pos += width
    out.append(0x22)
    return bytes(out)


def compact(fragment: bytes) -> bytes:
    """Compact and HTML-escape ``fragment`` the way Go marshals a ``RawMessage``.

    ``json.RawMessage`` goes through ``json.Marshal``, which drops whitespace
    outside strings and HTML-escapes the fragment just like a plain string. It
    copies any other bytes, including invalid UTF-8, unchanged.
    """
    out = bytearray()
    in_string = False
    pos = 0
    while pos < len(fragment):
        byte = fragment[pos]
        if byte == 0x5C and in_string:
            out += fragment[pos : pos + 2]
            pos += 2
            continue
        if byte == 0x22:
            in_string = not in_string
            out.append(byte)
            pos += 1
            continue
        if byte in _JSON_WHITESPACE and not in_string:
            pos += 1
            continue
        if byte == 0x3C:
            out += b"\\u003c"
            pos += 1
            continue
        if byte == 0x3E:
            out += b"\\u003e"
            pos += 1
            continue
        if byte == 0x26:
            out += b"\\u0026"
            pos += 1
            continue
        if (
            byte == 0xE2
            and pos + 2 < len(fragment)
            and fragment[pos + 1] == 0x80
            and fragment[pos + 2] & ~1 == 0xA8
        ):
            # U+2028/U+2029 (E2 80 A8 / E2 80 A9), escaped like Go's json.
            out += b"\\u202" + bytes([0x38 + (fragment[pos + 2] & 1)])
            pos += 3
            continue
        out.append(byte)
        pos += 1
    return bytes(out)