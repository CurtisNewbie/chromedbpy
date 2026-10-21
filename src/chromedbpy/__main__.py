"""Allow ``python -m chromedbpy`` to run the command line tool."""

import sys

from chromedbpy.cli import main

if __name__ == "__main__":
    sys.exit(main())