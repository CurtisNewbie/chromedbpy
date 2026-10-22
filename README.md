# chromedbpy

Python version of [chromedb](https://github.com/noperator/chromedb): read a Chromium
profile's cookies or local storage and print one JSON object per line.

## Install

Requires macOS and Python 3.10 through 3.14. The package is not on PyPI yet, so
install from a clone.

With [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
```

Or with pip, inside a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
```

Either way the `chromedbpy` console script becomes available. On the pip path,
install into the virtual environment. Some interpreters refuse to install outside
one (PEP 668), and the rest would put the tool into the interpreter your other
projects share. Installing needs a network connection, because the LevelDB
binding arrives as a prebuilt wheel. Running the tool does not.

### LevelDB

Nothing to build. LevelDB, compiled with snappy, ships inside the `plyvel` wheel,
so neither path above needs a compiler or a system library.

Upstream `plyvel` has no macOS wheels and links a system LevelDB. This project
instead depends on a maintained fork chosen by Python version: `plyvel-ci`
(import name `plyvel`) on Python 3.10 and 3.11, `plyvel-next` (import name
`plyvel_next`) on Python 3.12 through 3.14. Both publish self-contained macOS
universal2 wheels (arm64 slice included) that bundle their own `libleveldb` and
`libsnappy` under `@loader_path/.dylibs/`. `chromedbpy.leveldb` picks the right
binding, so the rest of the code does not care which one is installed.

## Usage

```bash
chromedbpy -p <profile-dir> -c    # cookies
chromedbpy -p <profile-dir> -ls   # local storage
```

`-p` is required and exactly one of `-c` or `-ls` must be given.

## Library usage

```python
from chromedbpy import read_cookies, read_local_storage

cookies = read_cookies("/path/to/profile")       # list[dict]
records = read_local_storage("/path/to/profile")  # list[dict]
```

Each dict holds the same fields the CLI prints, with values already decoded.
Unlike the CLI, these functions raise instead of printing on error.