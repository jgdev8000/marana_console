"""PyInstaller entry point for the Marana client GUI.

PyInstaller needs a script file (not a package), so this just calls the client's
main(). Build with:  pyinstaller marana-client.spec
"""
import sys

from marana_client.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
