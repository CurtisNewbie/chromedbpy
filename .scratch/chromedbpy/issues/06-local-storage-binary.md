# 06: Local storage, binary values

**What to build:** Local storage values that are neither valid JSON nor harmlessly quotable are emitted as base64, so the output stays valid JSON and no bytes are lost. Their content type comes from matching magic bytes, and `conversions` names the transformations applied.

**Blocked by:** 05.

**Status:** done

- [x] A value that survives JSON quoting is emitted as a quoted string, and one that does not is base64-encoded and then quoted.
- [x] A binary value's `mime` comes from magic-byte matching.
- [x] `conversions` names the transformations applied, using the same names the Go tool uses.
- [x] A value that is neither valid JSON nor harmlessly quotable is base64-encoded and then
  quoted, so those lines stay valid JSON and no bytes are lost. (A value that is itself valid
  JSON is emitted byte for byte, so it can contain invalid UTF-8, matching the Go tool.)