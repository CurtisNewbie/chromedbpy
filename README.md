# chromedbpy

Python version of [chromedb](https://github.com/noperator/chromedb): read a Chromium
profile's cookies or local storage and print one JSON object per line.

## Install

Requires Python 3.10–3.14 and [`uv`](https://docs.astral.sh/uv/).

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

### LevelDB

Nothing to build. LevelDB (compiled with snappy) ships inside the `plyvel`
wheel, so `uv sync` / `pip install` needs no compiler and no system library.

Upstream `plyvel` has no macOS wheels and links a system LevelDB. This project
instead depends on a maintained fork chosen by Python version: `plyvel-ci`
(import name `plyvel`) on Python 3.10–3.11, `plyvel-next` (import name
`plyvel_next`) on Python 3.12–3.14. Both publish self-contained macOS universal2
wheels (arm64 slice included) that bundle their own `libleveldb` and `libsnappy`
under `@loader_path/.dylibs/`. `chromedbpy.leveldb` picks the right binding, so
the rest of the code does not care which one is installed.

Supported Python: 3.10 through 3.14.