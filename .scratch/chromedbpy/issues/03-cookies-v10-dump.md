# 03: Cookies dump with v10 values

**What to build:** `chromedbpy -p <dir> -c` dumps the profile's cookies as one JSON object per line. The storage password comes from `BROWSER_PASSWORD` when set, and from the macOS keychain otherwise, with the service name inferred from the profile path and a fallback over the common service names. Encrypted values carry a `v10` prefix and are decrypted with AES-128-CBC under a key derived with PBKDF2-HMAC-SHA1. Each line carries `domain`, `name`, `encrypted_value` as base64 of the raw bytes, and the decrypted `value`. A row whose decryption fails prints the Go tool's error line and is still emitted with an empty `value`.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] `-c` reads `name`, `value`, `host_key`, and `encrypted_value` from the Cookies database.
- [ ] The key is derived as PBKDF2-HMAC-SHA1 over the salt `saltysalt`, 1003 iterations, 16 bytes, with the password trimmed of surrounding whitespace.
- [ ] `BROWSER_PASSWORD` takes precedence, and the keychain is read when it is unset.
- [ ] The keychain service is inferred from the profile path, with a fallback that tries the common service names.
- [ ] Each emitted line carries `domain`, `name`, `encrypted_value`, and `value` with the Go tool's encodings.
- [ ] A row whose decryption fails is reported in the Go tool's wording and emitted with an empty `value`.