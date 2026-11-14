"""User-requested file-manager actions; no shell command interpolation."""
import os
import subprocess
import sys


def open_folder(path: str) -> None:
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise NotADirectoryError(f"Folder unavailable: {path}")
    if os.name == "nt":
        os.startfile(path, "open")
    elif sys.platform.startswith("linux"):
        # Called on a background thread so a slow file manager cannot stall Tk.
        command = ["xdg-open", path]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            code = process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            # Some managers keep the launcher alive for the window's lifetime.
            # Never terminate a user-opened file manager when this check expires.
            return
        if code:
            raise subprocess.CalledProcessError(code, command)
    else:
        raise RuntimeError("Only Windows and Linux are supported")
