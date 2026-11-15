"""Windows fixed/removable volume enumeration and default exclusions."""
import ctypes
import os
import ntpath

from storage_scanner.platform_fs.paths import path_key

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3


def system_volume_root():
    """Ask Windows for its installation directory and owning volume mount."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_windows = kernel.GetWindowsDirectoryW
    get_windows.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_windows.restype = ctypes.c_uint
    get_volume = kernel.GetVolumePathNameW
    get_volume.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    get_volume.restype = ctypes.c_int
    directory = ctypes.create_unicode_buffer(32768)
    size = get_windows(directory, len(directory))
    if not size or size >= len(directory):
        raise ctypes.WinError(ctypes.get_last_error())
    volume = ctypes.create_unicode_buffer(32768)
    if not get_volume(directory.value, volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    return volume.value


def default_exclusions(roots):
    paths = [os.environ.get("SystemRoot", r"C:\Windows")]
    for root in roots:
        paths.extend(ntpath.join(root, name) for name in
                     ("System Volume Information", "$Recycle.Bin", "Recovery", "$WinREAgent"))
    return tuple(paths)


def select_volumes(candidates, include_removable=False):
    """Candidates are (mount path, drive type, stable volume identity)."""
    selected = []
    seen = set()
    for path, kind, identity in sorted(candidates, key=lambda c: (len(c[0]), c[0])):
        if kind != DRIVE_FIXED and not (include_removable and kind == DRIVE_REMOVABLE):
            continue
        if identity.casefold() not in seen:
            selected.append(path_key(path, windows=True))
            seen.add(identity.casefold())
    return tuple(selected)


def enumerate_candidates():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    candidates = []
    if hasattr(os, "listvolumes") and hasattr(os, "listmounts"):
        for volume in os.listvolumes():
            try:
                for mount in os.listmounts(volume):
                    candidates.append((mount, kernel.GetDriveTypeW(mount), volume))
            except OSError:
                continue
    # Drive letters also expose network drives as boundaries, and support 3.11.
    mask = kernel.GetLogicalDrives()
    for index in range(26):
        if mask & (1 << index):
            path = chr(65 + index) + ":\\"
            if not any(path_key(c[0], windows=True) == path_key(path, windows=True)
                       for c in candidates):
                candidates.append((path, kernel.GetDriveTypeW(path), path))
    return tuple(candidates)
