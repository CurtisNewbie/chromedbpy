"""Command line entry point for chromedbpy.

A single executable command built on the standard library ``argparse``. It takes
one required profile path (``-p <dir>``) and exactly one mode flag, ``-c`` for
cookies or ``-ls`` for local storage. The Go tool's ``-ss`` mode is not
implemented and its flag is absent.

Error and usage lines go to stdout, mirroring the Go tool's wording where it
applies. ``main`` returns the process exit code so the test seam can drive the
command in-process.
"""

from __future__ import annotations

import argparse
import sys

from chromedbpy.modes import cookies as cookies_mode
from chromedbpy.modes import localstorage as localstorage_mode

PROGRAM = "chromedbpy"

USAGE = f"""Usage of {PROGRAM}:
  -c\tcookies
  -ls
    \tlocal storage
  -p string
    \tpath to browser profile directory (required)
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM)
    parser.add_argument("-p", dest="profile", default="")
    parser.add_argument("-c", dest="cookies", action="store_true")
    parser.add_argument("-ls", dest="local_storage", action="store_true")
    return parser


def _print_usage() -> None:
    sys.stdout.write(USAGE)


def _is_macos() -> bool:
    return sys.platform == "darwin"


def main(argv: list[str] | None = None) -> int:
    """Run the command line tool and return its exit code."""
    if argv is None:
        argv = sys.argv[1:]

    args = _build_parser().parse_args(argv)

    if args.profile == "":
        print("Error: -p flag (path to browser profile directory) is required")
        _print_usage()
        return 1

    mode_count = int(args.cookies) + int(args.local_storage)
    if mode_count != 1:
        print("Error: Please specify exactly one of -c or -ls")
        _print_usage()
        return 1

    if not _is_macos():
        print(f"Error: {PROGRAM} is only supported on macOS")
        return 1

    if args.cookies:
        return cookies_mode.run(args.profile)

    return localstorage_mode.run(args.profile)