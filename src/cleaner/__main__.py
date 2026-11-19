"""Module & PyInstaller entry point for Discord DM Cleaner."""

from __future__ import annotations

import contextlib
import ctypes
import sys

from cleaner.main import main

if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW("Discord DM Cleaner")
    try:
        main()
    except SystemExit as exc:
        if getattr(sys, "frozen", False) and sys.platform == "win32" and sys.stdin.isatty():
            with contextlib.suppress(EOFError, KeyboardInterrupt):
                input("\nPress Enter to exit...")
        raise exc
