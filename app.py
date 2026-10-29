from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
import traceback

from ios_log_viewer.gui import LogViewerApp


def _enable_windows_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    print(f"Starting UI with Python {sys.version.split()[0]}", flush=True)
    print(f"Application directory: {os.path.dirname(os.path.abspath(__file__))}", flush=True)
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    LogViewerApp(root)
    print("GUI initialized; showing window.", flush=True)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Application startup failed:", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)
