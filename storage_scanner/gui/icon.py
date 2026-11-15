"""Bundled application identity and window icons; no runtime image dependencies."""
import os
import base64
from pathlib import Path
import tkinter as tk

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def set_process_identity():
    if os.name == "nt":
        import ctypes
        try:
            setter = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
            setter.argtypes = [ctypes.c_wchar_p]
            setter.restype = ctypes.c_long
            setter("StorageScanner.Desktop")
        except OSError:
            pass


def set_window_icon(root):
    # Keep the Tk image alive for the full window lifetime.
    root._app_icon = tk.PhotoImage(master=root, data=base64.b64encode((ASSETS / "icon.png").read_bytes()))
    root._app_icons = [root._app_icon, *(root._app_icon.subsample(scale) for scale in (3, 6, 12))]
    root.iconphoto(True, *root._app_icons)


# Preserve the public entry point introduced on main.
apply_window_icon = set_window_icon
