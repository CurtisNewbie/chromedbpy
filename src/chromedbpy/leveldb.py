"""LevelDB binding shim.

Upstream ``plyvel`` ships no macOS wheels and links a system LevelDB, which is
why this project used to build snappy, LevelDB, and the extension by hand. Two
maintained forks publish self-contained macOS universal2 wheels that bundle
their own LevelDB and snappy instead, so the binding is chosen by Python
version:

- ``plyvel-next`` on Python 3.12+ (import name ``plyvel_next``)
- ``plyvel-ci`` on older Pythons (import name ``plyvel``)

Both expose the same ``DB``/``Error`` surface this project uses, so callers
import this module rather than either binding.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from plyvel_next import DB, Error
else:
    from plyvel import DB, Error

__all__ = ["DB", "Error"]