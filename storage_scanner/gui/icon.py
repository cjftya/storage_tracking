"""Tk window icon shared by Windows and Linux."""
from pathlib import Path
import tkinter as tk

_ICON_PATH = Path(__file__).resolve().parent / "assets" / "storage_tracking.png"


def apply_window_icon(root: tk.Tk) -> None:
    """Set the GUI window icon and retain the Tk image reference."""
    icon = tk.PhotoImage(master=root, file=str(_ICON_PATH))
    root.iconphoto(True, icon)
    root._storage_scanner_icon = icon
