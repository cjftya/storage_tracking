"""Host path operations, kept outside the traversal engine."""
import ntpath
import os
import posixpath
import stat


def path_key(path: str, *, windows: bool | None = None) -> str:
    if windows is None:
        windows = os.name == "nt"
    module = ntpath if windows else posixpath
    normalized = module.normpath(path)
    # Avoid GetFullPathNameW on already fully qualified Windows entry paths.
    if windows and module.splitdrive(normalized)[0] and module.isabs(normalized):
        return module.normcase(normalized)
    return module.normcase(module.abspath(normalized))


def entry_path_key(path: str) -> str:
    """Key for a scandir entry below an already normalized absolute root."""
    return os.path.normcase(path)


def contains_path(parent: str, child: str, *, windows: bool | None = None) -> bool:
    if windows is None:
        windows = os.name == "nt"
    sep = "\\" if windows else "/"
    return child == parent or child.startswith(parent.rstrip(sep) + sep)


def directory_is_link(entry) -> bool:
    # Windows caches find-data in DirEntry, including file attributes.
    if os.name == "nt":
        return bool(entry.stat(follow_symlinks=False).st_file_attributes &
                    stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False  # is_dir(follow_symlinks=False) already rejected POSIX links.


def root_metadata(path: str):
    """Inspect a root once, rejecting links and ordinary reparse points."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return None
    if os.name == "nt" and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        # A volume mount is a safe scan root; ordinary junctions are not.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_volume = kernel.GetVolumeNameForVolumeMountPointW
        get_volume.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_volume.restype = wintypes.BOOL
        buffer = ctypes.create_unicode_buffer(1024)
        if not get_volume(path.rstrip("\\/") + "\\", buffer, len(buffer)):
            return None
    return info


def root_is_directory(path: str) -> bool:
    info = root_metadata(path)
    return info is not None and stat.S_ISDIR(info.st_mode)


def validate_scan_roots(roots, excluded, *, directories_only=False):
    """Fail before publishing results; root failures are not traversal errors."""
    if not roots:
        raise ValueError("No scan roots selected")
    inspected = []
    for root in roots:
        if excluded.excludes(root):
            raise ValueError(f"Scan root is excluded by path policy: {root}")
        try:
            info = root_metadata(root)
            if info is None:
                raise ValueError(f"Unsupported symbolic link, junction or reparse scan root: {root}")
            if stat.S_ISDIR(info.st_mode):
                # Open only: no entries or file contents are read during preflight.
                with os.scandir(root):
                    pass
            elif directories_only or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Unsupported scan root type: {root}")
        except OSError as exc:
            raise ValueError(f"Cannot access scan root {root}: {exc}") from exc
        inspected.append((root, info))
    return tuple(inspected)
