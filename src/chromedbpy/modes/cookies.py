"""Cookies mode: dump the profile's cookies as JSON lines.

Filled in by ticket 03.
"""

from __future__ import annotations


def run(profile_path: str) -> int:
    """Dump cookies for the profile at ``profile_path``; return the exit code."""
    return 0