"""Local storage mode: dump local storage as JSON lines.

Filled in by ticket 05.
"""

from __future__ import annotations


def run(profile_path: str) -> int:
    """Dump local storage for the profile at ``profile_path``; return the exit code."""
    return 0