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

1. Build an arm64 LevelDB into `~/.local/opt/leveldb` (source kept outside this
   repo):

   ```bash
   git clone https://github.com/google/leveldb ~/.cache/chromedbpy-leveldb-src
   git -C ~/.cache/chromedbpy-leveldb-src checkout 1.23
   cmake -S ~/.cache/chromedbpy-leveldb-src -B ~/.cache/chromedbpy-leveldb-src/build \
     -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
     -DBUILD_SHARED_LIBS=OFF \
     -DLEVELDB_BUILD_TESTS=OFF -DLEVELDB_BUILD_BENCHMARKS=OFF \
     -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/leveldb"
   cmake --build ~/.cache/chromedbpy-leveldb-src/build -j
   cmake --install ~/.cache/chromedbpy-leveldb-src/build
   ```

2. Rebuild `plyvel` against that prefix. `CXXFLAGS=-fno-rtti` is required:
   LevelDB builds with RTTI disabled and hidden visibility, so it never exports
   the `leveldb::Comparator` typeinfo that `plyvel`'s C++ otherwise references.
   `uv cache clean plyvel` forces a real rebuild.

   ```bash
   uv cache clean plyvel
   CFLAGS="-I$HOME/.local/opt/leveldb/include" \
   CXXFLAGS="-fno-rtti" \
   LDFLAGS="-L$HOME/.local/opt/leveldb/lib" \
   uv sync --reinstall-package plyvel
   ```

3. Verify with a round-trip:

   ```bash
   uv run python -c "import plyvel,tempfile,shutil; d=tempfile.mkdtemp(); db=plyvel.DB(d, create_if_missing=True); db.put(b'k',b'v'); assert db.get(b'k')==b'v'; db.close(); shutil.rmtree(d); print('PLYVEL OK')"
   ```

Because LevelDB is linked statically, `otool -L` on the built `plyvel` extension
shows only `libc++`/`libSystem` — no `libleveldb` entry.