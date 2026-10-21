# chromedbpy: read Chromium cookies and local storage from Python

Status: ready-for-agent

## Problem Statement

A Chromium profile keeps its cookies in an encrypted SQLite database and its local storage in a LevelDB
directory. Reading either one without launching the browser is what the Go tool
`noperator/chromedb` does, and it prints one JSON object per line so other tools can consume it. When
Python is the available runtime, there is no equivalent. No Python program reads the same profile
directory and produces the same JSON, so anything that already parses the Go tool's output has to be
rewritten before it can run without Go.

## Solution

A Python command line tool, `chromedbpy`, reads one Chromium profile directory from disk and writes
either its cookies or its local storage to stdout, one JSON object per line. It offers two modes,
cookies (`-c`) and local storage (`-ls`), and its output matches the Go tool field for field, so an
existing consumer switches without a code change. Cookies are decrypted with the browser's storage
password, which comes from the `BROWSER_PASSWORD` environment variable when set and from the macOS
keychain otherwise. Local storage is unencrypted and needs no password.

## User Stories

1. As a developer automating a logged-in browser session, I want to dump the profile's cookies as JSON,
   so that I can reuse the session's tokens in my own scripts.
2. As a developer, I want to dump the profile's local storage as JSON, so that I can read application
   state that the browser kept client-side.
3. As a developer, I want to point the tool at any profile directory with a single path argument, so
   that I can target a specific browser profile without editing configuration.
4. As a developer, I want to read the data while the browser is closed, so that I do not have to
   interrupt my browsing or log out.
5. As a developer, I want to read the data while the browser is running, so that I do not have to close
   it first.
6. As a developer, I want the storage password taken from `BROWSER_PASSWORD` when I set it, so that I
   can run the tool in a script without an interactive prompt.
7. As a developer on macOS, I want the storage password read from the keychain automatically when
   `BROWSER_PASSWORD` is unset, so that I do not have to export it in every shell.
8. As a developer, I want the keychain lookup to pick the right service for the profile I pointed at,
   so that Arc, Chrome, Brave, and Chromium profiles each decrypt correctly.
9. As a developer, I want cookie values decrypted, so that I get usable cookie values instead of
   ciphertext.
10. As a developer, I want the raw encrypted bytes preserved in the output alongside the decrypted
    value, so that I can inspect or re-decrypt them myself.
11. As a developer, I want cookies from a modern profile (Chrome 130 and later) decrypted correctly, so
    that the newer database format does not produce garbage.
12. As a developer, I want a cookie whose decryption fails reported and still listed with an empty
    value, so that one bad row does not abort the whole dump.
13. As a developer, I want each local storage record's key and script key reported, so that I can tell
    which site and which script wrote it.
14. As a developer, I want each local storage value's character set reported, so that I know how it was
    encoded on disk.
15. As a developer, I want a local storage value that is itself JSON emitted as JSON, so that I can pipe
    it straight into `jq`.
16. As a developer, I want a local storage value that is plain text emitted as a JSON string, so that
    non-JSON values stay readable.
17. As a developer, I want a local storage value that is binary emitted as base64, so that the output
    stays valid JSON and no bytes are lost.
18. As a developer, I want a guessed content type reported for each local storage value, so that I can
    tell text, JSON, images, and documents apart.
19. As a developer, I want the transformations applied to each value named in the output, so that I can
    reverse them.
20. As a developer, I want a value that looks like a number but is too large for a float kept as a
    string, so that big identifiers round-trip unchanged.
21. As a developer, I want exactly one mode per invocation, so that I never get two record types mixed
    into one stream.
22. As a developer, I want a clear error when I forget the profile path, so that I can fix my command
    without guessing.
23. As a developer on Linux or Windows, I want a clear message that the tool is macOS-only, so that I
    do not chase a misleading decryption failure.
24. As a developer, I want a missing or unreadable database reported as an error, so that I know the
    path was wrong rather than getting an empty result.
25. As a developer, I want the same JSON the Go tool produces for the same profile, so that I can swap
    the tool in without touching downstream parsers.
26. As a maintainer, I want the tool installed as a normal Python console script, so that users install
    it with their usual package manager.
27. As a maintainer, I want the extraction covered by tests built from synthetic fixture databases, so
    that the suite runs anywhere with no browser present.
28. As a maintainer, I want the output compared against the Go tool on the same fixture, so that the
    parity claim is checked by a test rather than asserted in prose.

## Implementation Decisions

**Command line surface.** A single executable command, `chromedbpy`, built on the standard library
`argparse`. One required path argument for the profile directory, and a choice of exactly one mode:

| flag | meaning |
|---|---|
| `-p <dir>` | path to the browser profile directory; required |
| `-c` | dump cookies |
| `-ls` | dump local storage |

The Go tool's third mode, session storage (`-ss`), is not implemented and the flag is absent. Supplying
no mode, or the profile path omitted, exits non-zero after printing a usage line.

**Where the storage password comes from.** A key source module resolves the password in this order:
`BROWSER_PASSWORD` if it is set and non-empty, otherwise the macOS keychain. The keychain lookup infers
the service name from the profile directory path (`Arc`, `Chrome`, `Brave Software`, `Chromium`, `Edge`
map to their `... Safe Storage` service names) and falls back to trying the common service names in
turn. The password is trimmed of surrounding whitespace before use, matching the Go tool.

**Cookie extraction.** A cookie module reads the `Cookies` SQLite database and returns one record per
row, selecting `name`, `value`, `host_key`, and `encrypted_value`. Before reading the rows it reads the
`meta` table's `version` value; when that read fails the version is taken as `0`. Decryption follows the
macOS scheme the Go tool uses:

| step | detail |
|---|---|
| key | `PBKDF2-HMAC-SHA1(password, salt=b"saltysalt", iterations=1003, length=16)` |
| cipher | AES-128-CBC |
| IV | sixteen space characters |
| prefix | the value must start with `v10`; the three prefix bytes are dropped before decryption |
| padding | PKCS7, removed by the last decrypted byte |
| version 24 and up | the first 32 decrypted bytes are a SHA256 digest of `host_key`, checked against the row's `host_key` and then dropped |

Because the decryption key comes from `cryptography` and the key derivation from the standard library,
both are recorded as dependencies. A cookie whose decryption raises is reported on stdout in the Go
tool's wording and is still emitted, with an empty `value`.

**Local storage extraction.** A local storage module reads the profile's local storage LevelDB. It reads
through the Go tool's approach: the leveldb directory is copied to a temporary directory and opened
there, because the Python LevelDB binding cannot open a database read-only and must not touch the live
profile directory. Iteration then handles two key shapes:

- keys beginning with `_` are records. The key splits on the first NUL byte into a storage key and a
  script key, and the script key is decoded by the same prefix rule as the value.
- keys beginning with `META:` are storage metadata. The value is a two-field protobuf message (a
  Chromium microsecond timestamp and a size). It is parsed but never emitted, matching the Go tool.

A value's first byte selects its decoding: `0` means little-endian UTF-16 and reports `UTF-16-LE`, `1`
means raw bytes and reports `ISO-8859-1`. Any other prefix is an error. Leading-byte removal applies to
both the script key and the value.

Each record's value is turned into JSON, matching the Go tool's output exactly:

- When the decoded value is itself valid JSON, the value is emitted as raw JSON and the mime is set to
  `application/json`.
- When it is not, the value is emitted as a quoted string. A value that survives quoting keeps the
  sniffed mime; a value that does not is base64-encoded, quoted, and its mime taken from magic-byte
  matching.
- Conversions records the transformations applied, naming the same internal calls the Go tool names.
- A JSON number too large for a float is emitted as a string instead.

The sniffed content type comes from a mimesniff library, which implements the same WHATWG algorithm as
Go's `net/http.DetectContentType`; only the part before the first semicolon is kept. Magic-byte matching
comes from the `filetype` library, which is the Python port of the `h2non/filetype` library the Go tool
uses.

**Output contract.** One JSON object per line on stdout, in struct order, with no trailing wrapper. Error
lines go to stdout in the Go tool's wording, not to stderr. The authoritative field sets:

| field | type | value |
|---|---|---|
| `domain` | string | the cookie's `host_key` |
| `name` | string | the cookie name |
| `encrypted_value` | string | base64 of the raw encrypted bytes |
| `value` | string | the decrypted value, empty when decryption failed |

| field | type | value |
|---|---|---|
| `storage_key` | string | the record's storage key, usually an origin |
| `script_key` | string | the key the writing script used |
| `charset` | string | `UTF-16-LE` or `ISO-8859-1` |
| `mime` | string | the sniffed or matched content type |
| `conversions` | array of string | names of the transformations applied to the value |
| `value` | any JSON | the value, emitted as raw JSON |

**Platform guard.** The tool is macOS-only, matching the Go tool. On any other platform the command
reports that it is unsupported and exits non-zero, before either mode runs.

**Dependencies.** LevelDB reading uses `plyvel`, which links the LevelDB library. The LevelDB installed
by the Intel Homebrew under `/usr/local` is x86_64, and an arm64 Python cannot load it, so LevelDB is
built from source for arm64 into a private prefix and `plyvel` is compiled against it with
`CXXFLAGS=-fno-rtti`; leveldb keeps the `Comparator` typeinfo hidden, and that flag stops `plyvel` from
referring to it. The install steps are documented in the README. The remaining dependencies are `cryptography`
for AES-CBC, `mimesniff` for content-type sniffing, `filetype` for magic-byte matching, and `keyring`
for the keychain. Key derivation, SQLite access, JSON, base64, and argument parsing come from the
standard library.

**Packaging.** `pyproject.toml` in PEP 621 form, dependencies managed with `uv`, package name
`chromedbpy`, a console-script entry point named `chromedbpy`, and a Python floor of 3.10.

**Independent implementation.** The Python code is written from scratch. The Go tool is a behavioral
reference for output parity only, and its source is never copied, translated, or vendored into this
repository.

## Testing Decisions

A good test here drives the tool the way a user does and asserts on what a user sees: the JSON lines on
stdout. It does not call internal functions, and it does not assert on private state, so the modules
behind the command line can be reshaped without touching tests.

There is one seam, the command line entry point, driven in-process with an argument list and captured
stdout. Both modes are covered through it. Everything the tool does is reachable this way, because the
risky logic (key derivation, AES-CBC and padding, the version-24 digest, the value-prefix decoding, the
JSON, quoting, and base64 branches) is all observable in the output for a fixture built to exercise it.

The fixtures are synthetic and live in the test suite. A helper builds a profile directory on disk: a
SQLite `Cookies` database with a chosen `meta.version` and rows carrying deliberately encrypted values,
and a LevelDB directory holding `_` and `META:` entries. The helper is test infrastructure rather than a
seam. Building the encrypted values needs the same key derivation the tool uses, so the helper shares
that one piece rather than re-deriving it.

Parity is checked, not asserted. The Go `chromedb` tool is built from the pinned revision
`ea058a1058188e78b5e67262df556cdf8c8787c5` of `github.com/noperator/chromedb` and run against the same
fixture directory, and its stdout is compared line by line with the Python tool's stdout. That check is a
verification step rather than a seam, and it is the evidence behind the claim that a consumer can swap
the tool in unchanged. It is skipped when Go or the network is unavailable, so the rest of the suite stays
hermetic.

Development follows test-driven development at this seam: write the failing command-line test for a
behaviour, then make it pass.

## Out of Scope

- Session storage. The Go tool's `-ss` mode is not ported and no flag is provided for it.
- Cookie decryption on Linux and Windows. Only the macOS key derivation is implemented.
- Domain filtering. The Go tool has it commented out, and this port does not add it.
- A supported Python library surface. The package ships a command line tool; the internal modules are
  not a public API and carry no compatibility promise.
- Any browser other than Chromium-based ones.
- Fixing the Go tool's output quirks. They are reproduced on purpose, as recorded in the parity ADR.

## Further Notes

The behavioral reference is `github.com/noperator/chromedb` at commit
`ea058a1058188e78b5e67262df556cdf8c8787c5` (2025-05-07). It is available on the development machine, so
the differential test can be run locally rather than only in CI. That revision's output is the single
source of truth for every field name, mime value, conversion name, and error string.

The decision to reproduce the Go output exactly, quirks included, is recorded in the ADR
`docs/adr/0001-faithful-parity-with-go-chromedb.md`.