# chromedbpy

Python version of [chromedb](https://github.com/noperator/chromedb): read a Chromium
profile's cookies or local storage and print one JSON object per line.

## Install

Requires Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

This exposes the `chromedbpy` console script.

## Usage

```bash
chromedbpy -p <profile-dir> -c    # cookies
chromedbpy -p <profile-dir> -ls   # local storage
```

`-p` is required and exactly one of `-c` or `-ls` must be given.

### LevelDB / `plyvel` build note

`plyvel` links a system LevelDB library, so it must match the CPU architecture
of the Python interpreter. This machine's Python is arm64 (Apple Silicon), while
the only LevelDB available from Homebrew is x86_64 — the Intel Homebrew under
`/usr/local` (there is no `/opt/homebrew`). An arm64 linker skips that x86_64
`libleveldb.dylib`, so the extension is built with no LevelDB linked and
`import plyvel` fails with `symbol not found in flat namespace
'__ZTVN7leveldb10ComparatorE'` (or similar). Build an arm64 LevelDB from source
into a private prefix and link `plyvel` against it.

LevelDB must also be built **with snappy**. Chrome compresses its LevelDB table
blocks with snappy, so a snappy-less LevelDB cannot read a real profile and
fails with `Corruption: corrupted compressed block contents`. The snappy build
on this machine is Intel-only too, so build an arm64 snappy first.

On a fresh machine, build both and rebuild `plyvel` against them with one
idempotent script (the notes above explain why; a fresh machine or a Python /
architecture change is when this is needed):

```bash
scripts/setup-native-deps.sh
```

The script is the source of truth for this chain. It encodes the flags that make
LevelDB's snappy probe succeed (the probe links with the **C** compiler while
libsnappy is C++, and LevelDB then `#include <snappy.h>`), fails loudly if the
build silently ends up snappy-less, and verifies the result with a real `plyvel`
round-trip. Re-running it is a fast no-op.

It installs snappy into `~/.local/opt/snappy` and LevelDB into
`~/.local/opt/leveldb`; override with `SNAPPY_PREFIX` / `LEVELDB_PREFIX`. Source
checkouts live under `$HOME/.cache/chromedbpy-*`, overridable with `SNAPPY_SRC` /
`LEVELDB_SRC`. Because LevelDB is linked statically, `otool -L` on the built
`plyvel` extension shows only `libc++`/`libSystem` — no `libleveldb` entry.