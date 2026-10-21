# 04: Cookies on Chrome 130+ databases

**What to build:** Profiles written by Chrome 130 and later put a SHA256 digest of the cookie's host key in front of the decrypted plaintext. The tool reads `meta.version` from the Cookies database and, when it is 24 or higher, checks that digest against the row's `host_key` and drops the first 32 bytes before returning the value.

**Blocked by:** 03.

**Status:** ready-for-agent

- [ ] The tool reads `meta.version`, defaulting to 0 when the value cannot be read.
- [ ] When the version is 24 or higher, the first 32 decrypted bytes are compared with `SHA256(host_key)` and dropped.
- [ ] A digest mismatch is reported as an error rather than returned as a value.
- [ ] Cookies dumped from a fixture at version 24 carry the same `value` as the same cookies at an older version.