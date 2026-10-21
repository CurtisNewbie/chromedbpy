# 02: Synthetic profile fixture builder

**What to build:** A test helper that writes a Chromium profile directory to a temporary location, so every later slice can run against real on-disk databases with no browser installed. The profile holds a SQLite `Cookies` database with a chosen `meta.version` and rows whose `encrypted_value` was produced with the tool's own key derivation, and a `Local Storage/leveldb` directory holding `_` records and `META:` entries.

**Blocked by:** 01.

**Status:** done

- [x] A helper builds a temporary profile directory containing a `Cookies` SQLite database and a `Local Storage/leveldb` LevelDB directory.
- [x] The helper can set `meta.version` to a chosen value and insert cookie rows carrying encrypted values it produces from the tool's key derivation.
- [x] The helper can write local storage `_` records (storage key, script key, value) and `META:` entries.
- [x] The helper removes its temporary directory when the fixture is discarded.
- [x] Fixture creation is covered by a test that opens the databases back and sees the inserted rows.