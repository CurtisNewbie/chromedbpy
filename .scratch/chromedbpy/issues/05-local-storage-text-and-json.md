# 05: Local storage, text and JSON values

**What to build:** `chromedbpy -p <dir> -ls` dumps local storage as one JSON object per line. The leveldb directory is copied to a temporary directory before it is opened, so the live profile is never touched and a running browser does not block the read. Keys beginning with `_` become records, split at the first NUL into a storage key and a script key. Keys beginning with `META:` are parsed and not emitted. A value's leading byte selects its charset, `0` for little-endian UTF-16 and `1` for raw bytes. Text and JSON values are emitted as quoted strings or as raw JSON, with the content type sniffed, and a JSON number too large for a float is emitted as a string.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] The leveldb directory is copied to a temporary directory and opened there, and the live profile directory is not opened for writing.
- [ ] `_` keys split into `storage_key` and `script_key` at the first NUL byte, with the script key decoded by the same prefix rule as the value.
- [ ] `META:` keys are parsed (timestamp and size) and are not emitted.
- [ ] A value's leading byte selects its charset, reported as `UTF-16-LE` or `ISO-8859-1`.
- [ ] A value that is valid JSON is emitted as raw JSON with the sniffed `mime`.
- [ ] A value that is not valid JSON is emitted as a quoted string with the sniffed `mime`.
- [ ] A JSON number too large for a float is emitted as a string.
- [ ] Each line carries `storage_key`, `script_key`, `charset`, `mime`, `conversions`, and `value`.