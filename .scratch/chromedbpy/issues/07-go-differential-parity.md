# 07: Go differential parity check

**What to build:** A check that the Python tool and the Go reference produce the same output. The Go tool is built from the pinned revision `ea058a1058188e78b5e67262df556cdf8c8787c5` of `github.com/noperator/chromedb` and run against the same synthetic fixtures, and its stdout is compared line by line with the Python tool's.

**Blocked by:** 03, 04, 05, 06.

**Status:** done

- [x] The check builds the Go tool at the pinned revision and runs both tools over the same fixture profile.
- [x] Cookie output matches line by line, including field names, encodings, mime values, conversion names, and error lines.
- [x] Local storage output matches line by line over the same field set.
- [x] The check is skipped, not failed, when Go or the network is unavailable.

## Comments

Implementation: `tests/test_go_parity.py`. The Go binary is built with `go install
github.com/noperator/chromedb/cmd/chromedb@ea058a1058188e78b5e67262df556cdf8c8787c5` and cached at
`$XDG_CACHE_HOME/chromedbpy-go/<revision>/chromedb` (default `~/.cache`), so the build runs once.
The check skips when Go is off `PATH`, when the build fails (e.g. no network), and off macOS.