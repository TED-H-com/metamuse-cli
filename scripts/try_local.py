"""Compatibility entrypoint for the installed MetaMuse demo."""

import sys

from metamuse.cli import main

if __name__ == "__main__":
    raise SystemExit(main([*sys.argv[1:], "demo"]))
