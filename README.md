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

`plyvel` links the system LevelDB library, so it must match the CPU architecture
of the Python interpreter. On an Apple Silicon host whose Homebrew LevelDB was
installed x86_64 (e.g. an Intel Homebrew under `/usr/local`), the linker ignores
the library and `import plyvel` fails with an unresolved `leveldb::*` symbol.
Point the build at an arm64 LevelDB (an `/opt/homebrew` Homebrew or a source
build) before `uv sync`.

When building `plyvel` from source against a LevelDB whose comparator symbols
are hidden, also pass `CXXFLAGS=-fno-rtti`:

```bash
CXXFLAGS=-fno-rtti uv pip install --no-binary plyvel --reinstall plyvel
```