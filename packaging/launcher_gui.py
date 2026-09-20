"""PyInstaller entry point for the GUI build.

PyInstaller runs its target file as the top-level `__main__` script, which
breaks the package's internal relative imports (`from .. import __version__`,
etc.) if we point it directly at backuprig/gui/app.py. This tiny launcher
imports the real package normally instead, so relative imports resolve.
"""

import sys

from backuprig.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
