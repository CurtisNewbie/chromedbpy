# 01: CLI scaffold and mode validation

**What to build:** An installable command named `chromedbpy`, built on `argparse`. It takes one required profile path and exactly one mode flag, `-c` for cookies or `-ls` for local storage. Omitting the path, omitting the mode, or passing both modes prints a usage message and exits non-zero. On any platform other than macOS the command reports that the feature is unsupported instead of producing a wrong result.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] The project installs and exposes a `chromedbpy` console script.
- [ ] `chromedbpy -p <dir> -c` and `chromedbpy -p <dir> -ls` each parse and dispatch to their mode.
- [ ] Supplying no mode, or both modes, prints usage and exits non-zero.
- [ ] Omitting the profile path prints usage and exits non-zero.
- [ ] On a non-macOS platform the command reports the feature as unsupported and exits non-zero; covered by a test that simulates another platform.
- [ ] A test drives the command line entry point in-process and asserts on stdout and the exit code.