"""PyInstaller entry point for the CLI build. See launcher_gui.py for why
this indirection is needed."""

import sys

from backuprig.cli import main

if __name__ == "__main__":
    sys.exit(main())
