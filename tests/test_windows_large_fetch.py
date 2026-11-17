import ctypes
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from storage_scanner.platform_fs.windows_scandir import (
    FindData, WindowsEntry, WindowsScandir, extended_path, scandir)


class AdapterTests(unittest.TestCase):
    def test_extended_drive_and_unc_paths(self):
        self.assertEqual(extended_path("C:\\Data"), "\\\\?\\C:\\Data")
        self.assertEqual(extended_path("\\\\server\\share\\a"), "\\\\?\\UNC\\server\\share\\a")
        self.assertEqual(extended_path("\\\\?\\C:\\Data"), "\\\\?\\C:\\Data")

    def test_size_reuses_find_data_and_reparse_uses_lstat(self):
        data = FindData()
        data.name, data.size_high, data.size_low = "file", 2, 123
        entry = WindowsEntry("C:\\Data", data)
        with patch("os.lstat", side_effect=AssertionError("Repeated metadata")):
            self.assertTrue(entry.is_file())
            self.assertFalse(entry.is_dir())
            self.assertEqual(entry.stat().st_size, (2 << 32) + 123)
        data.attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
        link = WindowsEntry("C:\\Data", data)
        with patch("os.lstat", return_value=os.stat_result((stat.S_IFLNK, 0, 0, 0, 0, 0, 0, 0, 0, 0))) as lstat:
            self.assertFalse(link.is_file())
            self.assertFalse(link.is_dir())
            self.assertEqual(lstat.call_count, 1)

    def test_only_unsupported_open_errors_fall_back(self):
        for code in (1, 50, 87, 120, 5, 3, 1117):
            error = OSError("native error")
            error.winerror = code
            with patch("storage_scanner.platform_fs.windows_scandir.WindowsScandir", side_effect=error), patch(
                    "storage_scanner.platform_fs.windows_scandir.os.scandir") as fallback:
                if code in (1, 50, 87, 120):
                    scandir("C:\\Data")
                    fallback.assert_called_once()
                else:
                    with self.assertRaises(OSError):
                        scandir("C:\\Data")
                    fallback.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Native Windows API")
class NativeTests(unittest.TestCase):
    def test_native_matches_scandir_unicode_empty_large_file_and_long_paths(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            (root / "empty").mkdir()
            deep = root
            for _ in range(15):
                deep /= "long-directory-name"
            os.makedirs(extended_path(str(deep)))
            for name, size in (("한글 space !", 17), ("zero", 0), ("big", (1 << 32) + 7)):
                with open(extended_path(str(deep / name)), "wb") as handle:
                    if size > 1 << 32:
                        # Mark sparse before extending: test logical size without allocating 4 GiB.
                        import msvcrt
                        from ctypes import wintypes
                        ioctl = ctypes.WinDLL("kernel32", use_last_error=True).DeviceIoControl
                        ioctl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
                                          wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
                        ioctl.restype = wintypes.BOOL
                        returned = wintypes.DWORD()
                        if not ioctl(msvcrt.get_osfhandle(handle.fileno()), 0x900C4,
                                     None, 0, None, 0, ctypes.byref(returned), None):
                            self.skipTest("Fixture filesystem does not support sparse files")
                    handle.truncate(size)
            from storage_scanner.folder.models import FolderConfig
            from storage_scanner.folder.scanner import scan
            expected = scan(FolderConfig((directory,)), lambda b: None)
            actual = scan(FolderConfig((directory,), workers=8, large_fetch=True), lambda b: None)
            self.assertEqual(actual.bytes_scanned, expected.bytes_scanned)
            self.assertEqual(actual.files_scanned, 3)
            self.assertEqual(actual.directories_scanned, expected.directories_scanned)
            self.assertEqual(actual.errors, 0)
            with WindowsScandir(str(root / "empty")) as entries:
                self.assertEqual(list(entries), [])

    def test_handle_closes_once_on_early_exit_and_next_error(self):
        from storage_scanner.platform_fs.windows_scandir import _api
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "file").touch()
            first, next_file, close = _api()
            for broken in (False, True):
                def fail(*args):
                    ctypes.set_last_error(1117)
                    return False
                with patch("storage_scanner.platform_fs.windows_scandir._api",
                           return_value=(first, fail if broken else next_file, close)):
                    entries = WindowsScandir(directory)
                    with patch.object(entries, "_close", wraps=close) as closing:
                        if broken:
                            with self.assertRaises(OSError):
                                with entries:
                                    list(entries)
                        else:
                            with entries:
                                next(entries)
                        entries.close()
                        closing.assert_called_once()

    def test_junction_cycle_is_not_followed(self):
        import subprocess
        from storage_scanner.folder.models import FolderConfig
        from storage_scanner.folder.scanner import scan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sub").mkdir()
            (root / "sub/file").write_bytes(b"123")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "sub/loop"), directory],
                                    capture_output=True)
            if result.returncode:
                self.skipTest("Junction creation unavailable")
            actual = scan(FolderConfig((directory,), workers=8, large_fetch=True), lambda b: None)
            self.assertEqual((actual.bytes_scanned, actual.directories_scanned, actual.errors), (3, 2, 0))
