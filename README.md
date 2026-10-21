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

1. Build an arm64 snappy into `~/.local/opt/snappy` (source kept outside this
   repo):

   ```bash
   git clone --depth 1 --branch 1.2.1 https://github.com/google/snappy \
     ~/.cache/chromedbpy-snappy-src
   cmake -S ~/.cache/chromedbpy-snappy-src -B ~/.cache/chromedbpy-snappy-src/build \
     -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
     -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/snappy" \
     -DSNAPPY_BUILD_TESTS=OFF -DSNAPPY_BUILD_BENCHMARKS=OFF
   cmake --build ~/.cache/chromedbpy-snappy-src/build -j
   cmake --install ~/.cache/chromedbpy-snappy-src/build
   ```

2. Build an arm64 LevelDB into `~/.local/opt/leveldb`, pointing CMake at that
   snappy prefix so leveldb's snappy check succeeds:

   ```bash
   git clone https://github.com/google/leveldb ~/.cache/chromedbpy-leveldb-src
   git -C ~/.cache/chromedbpy-leveldb-src checkout 1.23
   cmake -S ~/.cache/chromedbpy-leveldb-src -B ~/.cache/chromedbpy-leveldb-src/build \
     -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
     -DBUILD_SHARED_LIBS=OFF \
     -DLEVELDB_BUILD_TESTS=OFF -DLEVELDB_BUILD_BENCHMARKS=OFF \
     -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/leveldb" \
     -DCMAKE_PREFIX_PATH="$HOME/.local/opt/snappy" \
     -DCMAKE_C_FLAGS="-I$HOME/.local/opt/snappy/include" \
     -DCMAKE_CXX_FLAGS="-I$HOME/.local/opt/snappy/include" \
     -DCMAKE_EXE_LINKER_FLAGS="-L$HOME/.local/opt/snappy/lib -lc++"
   cmake --build ~/.cache/chromedbpy-leveldb-src/build -j
   cmake --install ~/.cache/chromedbpy-leveldb-src/build
   ```

   The two flags that are not obvious both come from leveldb finding snappy:

   - leveldb detects snappy with `check_library_exists(snappy snappy_compress
     "" HAVE_SNAPPY)`, which links its probe with the **C** compiler. libsnappy
     is C++, so the probe needs `-lc++` in `CMAKE_EXE_LINKER_FLAGS` or the check
     silently reports "not found".
   - once found, leveldb compiles `port/port_stdcxx.h`, which includes
     `<snappy.h>`, so the snappy include directory must be on the compile line
     (`CMAKE_C_FLAGS`/`CMAKE_CXX_FLAGS`) — it has no automatic include path for
     a private prefix.

   Confirm the check took before building on:

   ```bash
   grep HAVE_SNAPPY ~/.cache/chromedbpy-leveldb-src/build/CMakeCache.txt   # HAVE_SNAPPY:INTERNAL=1
   nm ~/.local/opt/leveldb/lib/libleveldb.a | grep -c snappy               # > 0
   ```

3. Rebuild `plyvel` against that prefix. `CXXFLAGS=-fno-rtti` is required:
   LevelDB builds with RTTI disabled and hidden visibility, so it never exports
   the `leveldb::Comparator` typeinfo that `plyvel`'s C++ otherwise references.
   `libleveldb.a` is static and now references snappy, so the plyvel link must
   also pull in snappy (`-lsnappy`). `uv cache clean plyvel` forces a rebuild.

   ```bash
   uv cache clean plyvel
   CFLAGS="-I$HOME/.local/opt/leveldb/include" \
   CXXFLAGS="-fno-rtti" \
   LDFLAGS="-L$HOME/.local/opt/leveldb/lib -L$HOME/.local/opt/snappy/lib -lsnappy" \
   uv sync --reinstall-package plyvel
   ```

4. Verify with a round-trip:

   ```bash
   uv run python -c "import plyvel,tempfile,shutil; d=tempfile.mkdtemp(); db=plyvel.DB(d, create_if_missing=True); db.put(b'k',b'v'); assert db.get(b'k')==b'v'; db.close(); shutil.rmtree(d); print('PLYVEL OK')"
   ```

Because LevelDB is linked statically, `otool -L` on the built `plyvel` extension
shows only `libc++`/`libSystem` — no `libleveldb` entry.