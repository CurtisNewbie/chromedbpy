# Reproduce the Go reference's output exactly, quirks included

`chromedbpy` replaces the Go tool `noperator/chromedb`, so anything already consuming that tool's JSON
has to keep working unchanged. That makes the Go output the contract: we reproduce its field names, its
value encodings, and even the internal Go call names it puts in the `conversions` array
(`strconv.Quote`, `base64.StdEncoding.EncodeToString`), and we keep the behaviours that read as defects,
such as an mime value that is overwritten immediately after it is set.

The cost of the decision is that the Python code carries behaviour a reader would otherwise want to
clean up. The alternative, emitting a tidier Python-native JSON, would force every consumer to change
and would break drop-in replacement. This is recorded because the parity looks like a bug to a reader
who does not know the Go tool is the reference.

The upstream repository carries no license, so its Go source is a reference to read for behavior rather
than code to copy; the Python implementation is written independently.

## Considered Options

- Clean Python-native JSON. Rejected: breaks drop-in replacement.
- A flag selecting faithful or clean output. Rejected: no consumer needs the second shape, and it
  doubles the output surface to test and document.