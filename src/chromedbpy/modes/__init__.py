"""Mode handlers reached from the command line.

Each module exposes a ``run(profile_path) -> int`` entry point returning the
process exit code: :mod:`chromedbpy.modes.cookies` for ``-c`` and
:mod:`chromedbpy.modes.localstorage` for ``-ls``.
"""