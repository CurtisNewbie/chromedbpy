"""Mode handlers reached from the command line.

Each module exposes a ``run(profile_path) -> int`` entry point returning the
process exit code. The modules are filled in by later tickets; the command line
dispatch surface stays fixed.
"""