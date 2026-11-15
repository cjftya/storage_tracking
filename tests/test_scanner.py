import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storage_scanner.core.models import ScanConfig
from storage_scanner.core.scanner import scan


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name, size):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.truncate(size)
        return path

    def run_scan(self, **kwargs):
        matches = []
        progress = []
        summary = scan(ScanConfig((str(self.root),), **kwargs), matches.extend, progress.append)
        self.assertEqual(progress[-1].files_matched, summary.files_matched)
        return summary, matches

    def test_threshold_nested_empty_unicode_and_excluded_file(self):
        self.file("small", 99)
        equal = self.file("한글 공백/[special] #/equal", 100)
        large = self.file("large", 101)
        excluded = self.file("skip/file", 1000)
        self.file("skipBackup/file", 102)
        self.file("exact-file", 100)
        (self.root / "empty").mkdir()
        summary, matches = self.run_scan(min_size_bytes=100,
            excluded_paths=(str(excluded.parent), str(self.root / "exact-file")))
        self.assertEqual(summary.files_scanned, 5)
        self.assertEqual({Path(m.path) for m in matches}, {equal, large, self.root / "skipBackup/file"})
        self.assertEqual(summary.errors, 0)

    def test_duplicate_and_nested_roots_once(self):
        self.file("a", 1)
        nested = self.file("nested/b", 1).parent
        matches = []
        summary = scan(ScanConfig((str(self.root), str(nested), str(self.root / ".")), 0), matches.extend)
        self.assertEqual(summary.files_scanned, 2)
        self.assertEqual(len(matches), 2)

    def test_unselected_mount_is_boundary(self):
        self.file("a", 1)
        mount = self.file("network/b", 1).parent
        summary, matches = self.run_scan(min_size_bytes=0, boundary_paths=(str(mount),))
        self.assertEqual(summary.files_scanned, 1)

    def test_file_mount_boundary_and_selected_file_root(self):
        selected = self.file("selected-file", 1)
        rejected = self.file("network-file", 1)
        matches = []
        config = ScanConfig((str(self.root), str(selected)), 0,
                            boundary_paths=(str(selected), str(rejected)))
        result = scan(config, matches.extend)
        self.assertEqual(result.files_scanned, 1)
        self.assertEqual([Path(m.path) for m in matches], [selected])

    def test_symlink_cycle_and_file_link(self):
        target = self.file("sub/file", 1)
        try:
            os.symlink(self.root, self.root / "sub/loop", target_is_directory=True)
            os.symlink(target, self.root / "file-link")
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable (Windows may require Developer Mode)")
        summary, matches = self.run_scan(min_size_bytes=0)
        self.assertEqual(summary.files_scanned, 1)
        self.assertEqual(summary.directories_scanned, 2)

    def test_directory_reparse_policy_skips_reentry(self):
        self.file("junction/file", 1)
        self.file("normal", 1)
        with patch("storage_scanner.core.scanner.directory_is_link", return_value=True):
            summary, matches = self.run_scan(min_size_bytes=0)
        self.assertEqual(summary.files_scanned, 1)
        self.assertEqual(summary.directories_scanned, 1)

    @unittest.skipUnless(os.name == "nt", "Windows junction scenario")
    def test_actual_windows_junction_cycle_and_root(self):
        import subprocess
        from storage_scanner.platform_fs.paths import root_is_directory
        self.file("sub/file", 1)
        link = self.root / "sub" / "loop"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.root)],
                                capture_output=True)
        if result.returncode:
            self.skipTest("Windows junction creation unavailable")
        self.assertFalse(root_is_directory(str(link)))
        summary, matches = self.run_scan(min_size_bytes=0)
        self.assertEqual(summary.files_scanned, 1)
        self.assertEqual(summary.directories_scanned, 2)

    def test_no_file_contents_read(self):
        self.file("large", 1000)
        with patch("builtins.open", side_effect=AssertionError("Scanner read file contents")):
            summary, matches = self.run_scan(min_size_bytes=1)
        self.assertEqual(len(matches), 1)

    def test_missing_and_permission_denied_directories(self):
        self.file("good/file", 1)
        blocked = self.file("blocked/file", 1).parent
        original = os.scandir

        def scandir(path):
            if Path(path) == blocked:
                raise PermissionError("test permission denied")
            return original(path)

        matches = []
        with patch("storage_scanner.core.scanner.os.scandir", side_effect=scandir):
            result = scan(ScanConfig((str(self.root),), 0), matches.extend)
        self.assertEqual(result.errors, 1)
        self.assertEqual(len(matches), 1)

    def test_deleted_entry_race_does_not_abort_directory(self):
        self.file("good", 1)
        real_scandir = os.scandir

        class Vanished:
            def is_dir(self, **kwargs):
                return False
            def is_file(self, **kwargs):
                return True
            def stat(self, **kwargs):
                raise FileNotFoundError("deleted between enumeration and stat")

        class Entries:
            def __enter__(inner):
                inner.entries = real_scandir(self.root)
                return iter([Vanished(), *inner.entries])
            def __exit__(inner, *args):
                inner.entries.close()

        with patch("storage_scanner.core.scanner.os.scandir", return_value=Entries()):
            summary, matches = self.run_scan(min_size_bytes=0)
        self.assertEqual(summary.errors, 1)
        self.assertEqual(len(matches), 1)

    def test_iterative_deep_walk(self):
        folder = self.root
        for _ in range(90):
            folder /= "d"
            folder.mkdir()
        with (folder / "f").open("wb") as handle:
            handle.truncate(1)
        old = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(80)
            summary, matches = self.run_scan(min_size_bytes=1)
        finally:
            sys.setrecursionlimit(old)
        self.assertEqual(len(matches), 1)
        self.assertEqual(summary.directories_scanned, 91)

    def test_batch_size_and_cancel_final_flush(self):
        for i in range(600):
            self.file(str(i), 1)
        batches = []
        def callback(batch):
            batches.append(batch)
        result = scan(ScanConfig((str(self.root),), 0, batch_size=256,
                                cancellation_check_interval=32), callback,
                      should_cancel=lambda: bool(batches))
        self.assertTrue(result.cancelled)
        self.assertEqual(sum(map(len, batches)), result.files_matched)
        self.assertTrue(all(0 < len(b) <= 256 for b in batches))
        complete, _ = self.run_scan(min_size_bytes=0)
        self.assertEqual(complete.files_matched, 600)

    def test_timed_partial_batch_flush(self):
        self.file("f", 1)
        batches = []
        # Deterministic advancing clock avoids a sleep per file.
        ticks = iter(range(10000))
        with patch("storage_scanner.core.scanner.time.perf_counter", side_effect=lambda: next(ticks)):
            summary = scan(ScanConfig((str(self.root),), 0, cancellation_check_interval=1),
                           batches.append, should_cancel=lambda: bool(batches))
        self.assertTrue(summary.cancelled)
        self.assertEqual(len(batches[0]), 1)

    def test_stat_type_changes_and_denial_do_not_hide_good_files(self):
        import stat
        from types import SimpleNamespace
        real_scandir = os.scandir
        self.file('good', 12)
        calls = []
        class Changed:
            def __init__(self, mode=None):
                self.mode = mode
            def is_dir(self, **kwargs):
                return False
            def is_file(self, **kwargs):
                return True
            def stat(self, **kwargs):
                calls.append(self.mode)
                if self.mode is None:
                    raise PermissionError('denied')
                return SimpleNamespace(st_mode=self.mode, st_size=4096)
        class Entries:
            def __enter__(inner):
                inner.actual = real_scandir(self.root)
                return iter([Changed(stat.S_IFDIR), Changed(stat.S_IFLNK),
                             Changed(stat.S_IFIFO), Changed(), *inner.actual])
            def __exit__(inner, *args):
                inner.actual.close()
        with patch('storage_scanner.core.scanner.os.scandir', return_value=Entries()):
            summary, matches = self.run_scan(min_size_bytes=0)
        self.assertEqual(len(calls), 4)  # One stat per enumerated entry.
        self.assertEqual(summary.errors, 4)
        self.assertEqual(summary.files_scanned, 1)
        self.assertEqual([m.size_bytes for m in matches], [12])
