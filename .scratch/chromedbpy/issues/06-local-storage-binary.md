# 06: Local storage, binary values

**What to build:** Local storage values that are neither valid JSON nor harmlessly quotable are emitted as base64, so the output stays valid JSON and no bytes are lost. Their content type comes from matching magic bytes, and `conversions` names the transformations applied.

**Blocked by:** 05.

**Status:** done

- [x] A value that survives JSON quoting is emitted as a quoted string, and one that does not is base64-encoded and then quoted.
- [x] A binary value's `mime` comes from magic-byte matching.
- [x] `conversions` names the transformations applied, using the same names the Go tool uses.
- [x] Output stays valid JSON for a value containing arbitrary bytes.