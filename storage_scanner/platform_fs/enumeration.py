"""Select the directory adapter for folder scans only."""
import os


def scandir(path, *, large_fetch=False):
    if os.name == "nt" and large_fetch:
        from storage_scanner.platform_fs.windows_scandir import scandir as windows_scandir
        return windows_scandir(path)
    return os.scandir(path)
