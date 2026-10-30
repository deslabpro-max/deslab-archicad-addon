"""Entry point for the PyInstaller build of deslab-scan2bim.exe."""

import sys

from deslab_scan2bim.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
