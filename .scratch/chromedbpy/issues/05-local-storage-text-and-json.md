# 05: Local storage, text and JSON values

**What to build:** `chromedbpy -p <dir> -ls` dumps local storage as one JSON object per line. The leveldb directory is copied to a temporary directory before it is opened, so the live profile is never touched and a running browser does not block the read. Keys beginning with `_` become records, split at the first NUL into a storage key and a script key. Keys beginning with `META:` are parsed and not emitted. A value's leading byte selects its charset, `0` for little-endian UTF-16 and `1` for raw bytes. Text and JSON values are emitted as quoted strings or as raw JSON, with the content type sniffed, and a JSON number too large for a float is emitted as a string.

**Blocked by:** 02.

**Status:** done

- [x] The leveldb directory is copied to a temporary directory and opened there, and the live profile directory is not opened for writing.
- [x] `_` keys split into `storage_key` and `script_key` at the first NUL byte, with the script key decoded by the same prefix rule as the value.
- [x] `META:` keys are parsed (timestamp and size) and are not emitted.
- [x] A value's leading byte selects its charset, reported as `UTF-16-LE` or `ISO-8859-1`.
- [x] A value that is valid JSON is emitted as raw JSON, with `mime` `application/json` (see the note below).
- [x] A value that is not valid JSON is emitted as a quoted string with the sniffed `mime`.
- [x] A JSON number too large for a float is emitted as a string.
- [x] Each line carries `storage_key`, `script_key`, `charset`, `mime`, `conversions`, and `value`.

## Comments

Implementation: `src/chromedbpy/modes/localstorage.py`, tests `tests/test_localstorage.py`
(driven through `chromedbpy.cli.main`, `uv run pytest -q`, 28 passed).

Go reference used as an oracle during development: the pinned `chromedb` revision was built
in a scratch module and run over the same fixture as the Python CLI, comparing stdout byte for
byte over 31 records (JSON objects/arrays/scalars, compacted and HTML-escaped JSON, large and
overflowing numbers, U+2028/U+00A0/U+200B, control bytes, empty, non-ASCII, ISO-8859-1 raw
bytes, base64/magic-byte payloads, both charset-prefix combinations, a record with no NUL, a
`META:` entry, and a non-UTF-8 script key). 29 of 31 lines matched. The two that did not:

- An ICO payload: Go's `image/vnd.microsoft.icon` vs the `filetype` library's `image/x-icon`.
  This is the magic-byte branch, which ticket 06 owns.
- A value that is valid JSON but contains invalid UTF-8 inside a string: Go writes the raw
  byte, which is not valid UTF-8; this port writes U+FFFD. Pathological input, ticket 06/07
  territory.

Note on the "sniffed mime" wording above: the Go reference sets `mime` to `application/json`
for a valid JSON value, and never sniffs it. The oracle confirms this, and the ticket's prose
(and the spec's) reads loosely here; the output follows the Go reference, which the ADR makes
the contract.

Correction (commit `a6c7eab`): a profile with no `Local Storage/leveldb` directory no longer
produces silent success. It reports the missing directory the way the Go tool does, with
`Error copying directory: ...` and `Error opening LevelDB: ...` on stdout and exit code 1, so
spec user story 24 ("a missing or unreadable database reported as an error") is met for the
missing-directory case. `test_cli.py::test_local_storage_mode_dispatches` builds a profile
that *has* a leveldb directory and asserts the one dumped record, not silent success.