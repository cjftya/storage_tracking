"""Folder-engine DirEntry subset backed by Win32 bulk directory queries.

Ordinary entries reuse find data; reparse points use lstat without following links.
"""
import ctypes
from ctypes import wintypes
from functools import lru_cache
import ntpath
import os
import stat
from types import SimpleNamespace


UNSUPPORTED_ERRORS = frozenset((1, 50, 87, 120))
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class FindData(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD),
                ("creation", wintypes.FILETIME), ("access", wintypes.FILETIME),
                ("write", wintypes.FILETIME),
                ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                ("reserved0", wintypes.DWORD), ("reserved1", wintypes.DWORD),
                ("name", wintypes.WCHAR * 260), ("alternate", wintypes.WCHAR * 14)]


def extended_path(path):
    path = ntpath.abspath(os.fspath(path))
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


@lru_cache(maxsize=1)
def _api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first, next_file, close = kernel.FindFirstFileExW, kernel.FindNextFileW, kernel.FindClose
    first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(FindData),
                      ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    first.restype = wintypes.HANDLE
    next_file.argtypes = [wintypes.HANDLE, ctypes.POINTER(FindData)]
    next_file.restype = wintypes.BOOL
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return first, next_file, close


class WindowsEntry:
    __slots__ = ("name", "path", "_attributes", "_size", "_info")

    def __init__(self, parent, data):
        self.name = data.name
        self.path = ntpath.join(parent, self.name)
        self._attributes = data.attributes
        self._size = (data.size_high << 32) | data.size_low
        self._info = None

    def stat(self, *, follow_symlinks=False):
        if follow_symlinks:
            return os.stat(extended_path(self.path))
        if self._info is None:
            if self._attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                self._info = os.lstat(extended_path(self.path))
            else:
                mode = (stat.S_IFDIR if self._attributes & stat.FILE_ATTRIBUTE_DIRECTORY
                        else stat.S_IFCHR if self._attributes & stat.FILE_ATTRIBUTE_DEVICE
                        else stat.S_IFREG)
                self._info = SimpleNamespace(st_mode=mode, st_size=self._size,
                                             st_file_attributes=self._attributes)
        return self._info

    def is_dir(self, *, follow_symlinks=False):
        if not follow_symlinks and not self._attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return bool(self._attributes & stat.FILE_ATTRIBUTE_DIRECTORY)
        return stat.S_ISDIR(self.stat(follow_symlinks=follow_symlinks).st_mode)

    def is_file(self, *, follow_symlinks=False):
        if not follow_symlinks and not self._attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return not self._attributes & (stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_DEVICE)
        return stat.S_ISREG(self.stat(follow_symlinks=follow_symlinks).st_mode)


class WindowsScandir:
    def __init__(self, path):
        self.path = os.fspath(path)
        self._handle = None
        self._first_pending = True
        self._data = FindData()
        self._first, self._next, self._close = _api()
        handle = self._first(ntpath.join(extended_path(path), "*"), 1,
                             ctypes.byref(self._data), 0, None, 2)
        if handle == INVALID_HANDLE_VALUE:
            error = ctypes.get_last_error()
            if error != 2:  # An empty wildcard result.
                raise ctypes.WinError(error)
        else:
            self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        while self._handle is not None:
            if self._first_pending:
                self._first_pending = False
            elif not self._next(self._handle, ctypes.byref(self._data)):
                error = ctypes.get_last_error()
                self.close()
                if error != 18:  # ERROR_NO_MORE_FILES
                    raise ctypes.WinError(error)
                break
            if self._data.name not in (".", ".."):
                return WindowsEntry(self.path, self._data)
        raise StopIteration

    def close(self):
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._close(handle)


def scandir(path):
    # Only opening is retried: restarting a partially read directory duplicates bytes.
    try:
        return WindowsScandir(path)
    except (AttributeError, NotImplementedError):
        return os.scandir(path)
    except OSError as exc:
        if getattr(exc, "winerror", None) not in UNSUPPORTED_ERRORS:
            raise
        return os.scandir(path)
