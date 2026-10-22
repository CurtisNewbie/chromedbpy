"""Public library API: read a profile's cookies and local storage as Python data.

Two functions, :func:`read_local_storage` and :func:`read_cookies`, return the
same records the command line prints, as Python objects instead of JSON lines.
Each reuses the mode code the CLI drives, so the field names, meanings, and
value encodings are identical.

Error contract. The CLI prints an error line on stdout and returns an exit code;
these functions never print and instead raise, propagating the exceptions the
modes already raise -- ``localstorage.LocalStorageError``, ``leveldb.Error``,
``OSError``, and ``sqlite3.Error``. They abort on the first failure rather than
reporting it and continuing, because there is no record to return.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from chromedbpy.keys import derive_key
from chromedbpy.modes import cookies, localstorage


def read_local_storage(profile_path: str) -> list[dict]:
    """Return the profile's local storage records as dicts.

    Each dict mirrors one CLI record, same keys/meaning: ``storage_key``,
    ``script_key``, ``charset``, ``mime``, ``conversions``, ``value``.
    ``value`` is the JSON-decoded value.
    """
    leveldb_dir = Path(profile_path) / "Local Storage" / "leveldb"
    records = localstorage._load_records(leveldb_dir)
    return [
        json.loads(localstorage._record_to_json(record).decode("utf-8", "replace"))
        for record in records
    ]


def read_cookies(profile_path: str) -> list[dict]:
    """Return the profile's cookies as dicts.

    Keys: ``domain``, ``name``, ``encrypted_value``, ``value``.
    ``encrypted_value`` is the base64 string, or ``None`` for a NULL column.
    """
    rows, db_version = cookies._read_cookies(os.path.join(profile_path, "Cookies"))

    password = cookies._resolve_password(profile_path)
    if password is None:
        raise RuntimeError(
            "BROWSER_PASSWORD environment variable not set and no keychain "
            "password found"
        )
    key = derive_key(password)

    result: list[dict] = []
    for name, value, host_key, encrypted_value in rows:
        decrypted = value
        if encrypted_value:
            try:
                decrypted = cookies._decrypt_value(
                    encrypted_value, key, db_version, host_key
                )
            except cookies._DecryptError:
                decrypted = ""
        result.append(
            json.loads(
                cookies._format_cookie(host_key, name, encrypted_value, decrypted)
            )
        )
    return result