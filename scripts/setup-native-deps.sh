#!/bin/bash
# NOTE: /bin/bash (Apple's universal shell) keeps this running natively arm64,
# even when an Intel Homebrew bash earlier on PATH would run it under Rosetta.
#
# setup-native-deps.sh - provision the native prerequisites chromedbpy needs on
# Apple Silicon macOS and rebuild the plyvel extension against them.
#
# Why this exists: plyvel links a system LevelDB, and the interpreter is arm64
# while the only Homebrew LevelDB is x86_64. chrome also compresses its LevelDB
# table blocks with snappy, so LevelDB must be built with snappy enabled. That
# chain has fragile, counter-intuitive flags (see step 2), so it lives here as
# the source of truth rather than as prose in the README.
#
# Steps (each skipped when its artifact is already present and correct, so a
# second run is a fast no-op):
#   1. Build google/snappy (tag 1.2.1) for arm64 into $SNAPPY_PREFIX.
#   2. Build google/leveldb (tag 1.23) for arm64, static, with snappy enabled,
#      into $LEVELDB_PREFIX. LevelDB's snappy probe links with the C compiler
#      while libsnappy is C++, so the probe needs -lc++ in the exe linker flags
#      and snappy's include dir on the compile line. Verified afterwards:
#      HAVE_SNAPPY=1 in the config and snappy symbols in the archive.
#   3. Rebuild plyvel against both prefixes (static LevelDB, -fno-rtti, -lsnappy).
#   4. Verify `import plyvel` and a real LevelDB round-trip, then print a
#      one-line success summary naming the prefixes.
#
# Overridable environment variables:
#   SNAPPY_PREFIX     default: $HOME/.local/opt/snappy
#   LEVELDB_PREFIX    default: $HOME/.local/opt/leveldb
#   CHROMEDBPY_CACHE  default: $HOME/.cache   (parent of the source checkouts)
#   SNAPPY_SRC        default: $CHROMEDBPY_CACHE/chromedbpy-snappy-src
#   LEVELDB_SRC       default: $CHROMEDBPY_CACHE/chromedbpy-leveldb-src
#
# Usage: scripts/setup-native-deps.sh

set -euo pipefail

SNAPPY_PREFIX="${SNAPPY_PREFIX:-$HOME/.local/opt/snappy}"
LEVELDB_PREFIX="${LEVELDB_PREFIX:-$HOME/.local/opt/leveldb}"
CHROMEDBPY_CACHE="${CHROMEDBPY_CACHE:-$HOME/.cache}"
SNAPPY_SRC="${SNAPPY_SRC:-$CHROMEDBPY_CACHE/chromedbpy-snappy-src}"
LEVELDB_SRC="${LEVELDB_SRC:-$CHROMEDBPY_CACHE/chromedbpy-leveldb-src}"

SNAPPY_URL="https://github.com/google/snappy"
SNAPPY_TAG="1.2.1"
LEVELDB_URL="https://github.com/google/leveldb"
LEVELDB_TAG="1.23"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------

preflight() {
    [[ "$(uname -s)" == "Darwin" ]] || die "this script targets macOS (found $(uname -s))"
    # sysctl reports the real hardware, so this holds even under Rosetta.
    [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]] \
        || die "this script targets Apple Silicon arm64 (uname -m is $(uname -m))"

    local tool
    for tool in cmake git curl nm lipo uv; do
        command -v "$tool" >/dev/null 2>&1 || die "required tool not found on PATH: $tool"
    done
}

# True when a static archive is built for arm64.
is_arm64() {
    local archs
    archs="$(lipo -archs "$1" 2>/dev/null || true)"
    [[ "$archs" == *arm64* ]]
}

# Clone the source checkout at a tag if missing, else make sure it is checked out.
ensure_src() {
    local dir="$1" url="$2" ref="$3"
    if [[ ! -d "$dir/.git" ]]; then
        say "cloning $url ($ref) into $dir"
        mkdir -p "$(dirname "$dir")"
        git clone --quiet --depth 1 --branch "$ref" "$url" "$dir"
    else
        git -C "$dir" checkout --quiet "$ref" 2>/dev/null || true
    fi
}

# --- snappy ------------------------------------------------------------------

snappy_ok() {
    [[ -f "$SNAPPY_PREFIX/lib/libsnappy.a" ]] || return 1
    [[ -f "$SNAPPY_PREFIX/include/snappy.h" ]] || return 1
    is_arm64 "$SNAPPY_PREFIX/lib/libsnappy.a"
}

build_snappy() {
    say "building snappy $SNAPPY_TAG for arm64 -> $SNAPPY_PREFIX"
    ensure_src "$SNAPPY_SRC" "$SNAPPY_URL" "$SNAPPY_TAG"
    rm -rf "$SNAPPY_SRC/build"
    cmake -S "$SNAPPY_SRC" -B "$SNAPPY_SRC/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_INSTALL_PREFIX="$SNAPPY_PREFIX" \
        -DSNAPPY_BUILD_TESTS=OFF \
        -DSNAPPY_BUILD_BENCHMARKS=OFF
    cmake --build "$SNAPPY_SRC/build" -j
    cmake --install "$SNAPPY_SRC/build"
    snappy_ok || die "snappy build did not produce a correct arm64 $SNAPPY_PREFIX/lib/libsnappy.a"
}

# --- leveldb -----------------------------------------------------------------

# Correct means: static arm64 archive, snappy enabled, and snappy symbols present.
leveldb_ok() {
    local leveldb_lib="$LEVELDB_PREFIX/lib/libleveldb.a"
    [[ -f "$leveldb_lib" ]] || return 1
    is_arm64 "$leveldb_lib" || return 1
    grep -q 'HAVE_SNAPPY:INTERNAL=1' "$LEVELDB_SRC/build/CMakeCache.txt" 2>/dev/null || return 1

    local snappy_syms
    snappy_syms="$(nm "$leveldb_lib" 2>/dev/null | grep -c snappy || true)"
    [[ "$snappy_syms" -gt 0 ]]
}

build_leveldb() {
    say "building leveldb $LEVELDB_TAG for arm64 (static, snappy) -> $LEVELDB_PREFIX"
    ensure_src "$LEVELDB_SRC" "$LEVELDB_URL" "$LEVELDB_TAG"
    rm -rf "$LEVELDB_SRC/build"
    cmake -S "$LEVELDB_SRC" -B "$LEVELDB_SRC/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DBUILD_SHARED_LIBS=OFF \
        -DLEVELDB_BUILD_TESTS=OFF \
        -DLEVELDB_BUILD_BENCHMARKS=OFF \
        -DCMAKE_INSTALL_PREFIX="$LEVELDB_PREFIX" \
        -DCMAKE_PREFIX_PATH="$SNAPPY_PREFIX" \
        -DCMAKE_C_FLAGS="-I$SNAPPY_PREFIX/include" \
        -DCMAKE_CXX_FLAGS="-I$SNAPPY_PREFIX/include" \
        -DCMAKE_EXE_LINKER_FLAGS="-L$SNAPPY_PREFIX/lib -lc++"
    cmake --build "$LEVELDB_SRC/build" -j
    cmake --install "$LEVELDB_SRC/build"

    grep -q 'HAVE_SNAPPY:INTERNAL=1' "$LEVELDB_SRC/build/CMakeCache.txt" \
        || die "leveldb did not find snappy (HAVE_SNAPPY is not 1 in $LEVELDB_SRC/build/CMakeCache.txt)"
    local snappy_syms
    snappy_syms="$(nm "$LEVELDB_PREFIX/lib/libleveldb.a" 2>/dev/null | grep -c snappy || true)"
    [[ "$snappy_syms" -gt 0 ]] \
        || die "leveldb archive has no snappy symbols - refusing a silently snappy-less build"
}

# --- plyvel ------------------------------------------------------------------

PLYVEL_CHECK='import plyvel,tempfile,shutil
d = tempfile.mkdtemp()
db = plyvel.DB(d, create_if_missing=True)
db.put(b"k", b"v")
assert db.get(b"k") == b"v"
db.close()
shutil.rmtree(d)
print("PLYVEL OK")'

plyvel_ok() {
    ( cd "$REPO_ROOT" && uv run --quiet python -c "$PLYVEL_CHECK" >/dev/null 2>&1 )
}

rebuild_plyvel() {
    say "rebuilding plyvel against $LEVELDB_PREFIX and $SNAPPY_PREFIX"
    cd "$REPO_ROOT"
    uv cache clean plyvel >/dev/null 2>&1 || true
    CFLAGS="-I$LEVELDB_PREFIX/include" \
        CXXFLAGS="-fno-rtti" \
        LDFLAGS="-L$LEVELDB_PREFIX/lib -L$SNAPPY_PREFIX/lib -lsnappy" \
        uv sync --reinstall-package plyvel
}

# --- main --------------------------------------------------------------------

preflight

rebuilt_native=0

if snappy_ok; then
    say "snappy already present at $SNAPPY_PREFIX - skipping"
else
    build_snappy
    rebuilt_native=1
fi

if leveldb_ok; then
    say "leveldb already present with snappy at $LEVELDB_PREFIX - skipping"
else
    build_leveldb
    rebuilt_native=1
fi

if [[ "$rebuilt_native" -eq 1 ]]; then
    rebuild_plyvel
elif plyvel_ok; then
    say "plyvel already linked and verified - skipping"
else
    rebuild_plyvel
fi

plyvel_ok || die "plyvel verification failed after rebuild"

say "success: snappy=$SNAPPY_PREFIX leveldb=$LEVELDB_PREFIX plyvel=linked+verified"