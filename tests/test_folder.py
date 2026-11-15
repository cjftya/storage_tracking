from pathlib import Path
import ast
import os
import tempfile
import time
from threading import Event, Lock
import unittest
from unittest.mock import patch

from storage_scanner.folder.models import DirectoryDelta, FolderConfig, FolderSummary
from storage_scanner.folder.scanner import scan
from storage_scanner.folder.results import FolderResultStore
from storage_scanner.folder.controller import FolderController
from storage_scanner.platform_fs.paths import path_key


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, path, size):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.truncate(size)
        return path

    def collect(self, **kwargs):
        config = FolderConfig((str(self.root),), **kwargs)
        store = FolderResultStore(config.roots)
        summary = scan(config, store.add_batch)
        store.finish(complete=not summary.cancelled and not summary.errors)
        return summary, store

    def test_all_sizes_direct_and_ancestor_totals_once(self):
        mib = 1024 ** 2
        self.file("root", 3)
        self.file("한글 space !/a", mib)
        self.file("한글 space !/nested/b", mib)
        # Sparse logical size, without allocating 100 MiB of contents.
        self.file("large/big", 100 * mib)
        self.file("zero", 0)
        (self.root / "empty").mkdir()
        summary, store = self.collect()
        root = store.node(path_key(str(self.root)))
        self.assertEqual(root.direct_bytes, 3)
        self.assertEqual(root.size_bytes, 102 * mib + 3)
        self.assertEqual(summary.bytes_scanned, root.size_bytes)
        self.assertEqual(store.folder_count, 4)
        from storage_scanner.core.scanner import scan as file_scan
        from storage_scanner.core.models import ScanConfig
        matches = []
        file_scan(ScanConfig((str(self.root),)), matches.extend)
        self.assertEqual([m.size_bytes for m in matches], [100 * mib])

    def test_exclusions_zero_root_and_nested_boundaries(self):
        self.file("keep/a", 7)
        excluded = self.file("excluded/a", 9).parent
        boundary = self.file("mount/a", 11).parent
        hidden = self.file("hidden", 13)
        summary, store = self.collect(excluded_paths=(str(excluded), str(hidden)),
                                      boundary_paths=(str(boundary),))
        self.assertEqual(summary.bytes_scanned, 7)
        self.assertIsNone(store.node(path_key(str(excluded))))
        with self.assertRaisesRegex(ValueError, "excluded"):
            self.collect(excluded_paths=(str(self.root),))

    def test_many_files_store_only_folders_and_bounded_batches(self):
        for i in range(2000):
            self.file(f"data/{i}", 1)
        store = FolderResultStore((str(self.root),))
        batches = []
        def batch(values):
            batches.append(len(values))
            self.assertTrue(all(Path(v.path).is_dir() for v in values))
            store.add_batch(values)
        result = scan(FolderConfig((str(self.root),)), batch)
        self.assertEqual(result.bytes_scanned, 2000)
        self.assertEqual(len(store._nodes), 2)
        self.assertTrue(all(n.is_directory for n in store._nodes.values()))
        self.assertLessEqual(max(batches), 512)

    def test_cancel_flushes_partial_and_restart_is_fresh(self):
        for i in range(300):
            self.file(str(i), 1)
        cancel = Event()
        store = FolderResultStore((str(self.root),))
        def progress(value):
            if value.files_scanned:
                cancel.set()
        summary = scan(FolderConfig((str(self.root),), progress_interval=1e-9),
                       store.add_batch, progress, cancel.is_set)
        store.finish(complete=False)
        self.assertTrue(summary.cancelled)
        self.assertEqual(store.state, "partial")
        self.assertEqual(store.children(None)[0].size_bytes, summary.bytes_scanned)
        final, fresh = self.collect()
        self.assertEqual(final.bytes_scanned, 300)
        self.assertEqual(fresh.state, "complete")

    def test_deep_iterative_walk_duplicate_and_nested_roots(self):
        deep = "/".join(["d"] * 80)
        self.file(deep + "/f", 17)
        child = self.root / "d"
        roots = (str(self.root), str(child), str(self.root))
        store = FolderResultStore(roots)
        result = scan(FolderConfig(roots), store.add_batch)
        self.assertEqual(result.bytes_scanned, 17)
        self.assertEqual(sum(n.size_bytes for n in store.children(None)), 17)

    def test_permission_and_deleted_entry_errors_keep_partial_totals(self):
        self.file("good/a", 5)
        bad = self.root / "bad"
        bad.mkdir()
        actual = os.scandir
        def scandir(path):
            if path_key(path) == path_key(str(bad)):
                raise PermissionError("denied")
            return actual(path)
        with patch("storage_scanner.folder.scanner.os.scandir", side_effect=scandir):
            result, store = self.collect()
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.bytes_scanned, 5)
        self.assertEqual(store.state, "partial")

    def test_no_contents_and_link_policy(self):
        self.file("a", 8)
        self.file("junction/ignored", 13)
        with patch("builtins.open", side_effect=AssertionError("Content read")), patch(
                "storage_scanner.folder.scanner.directory_is_link", return_value=True):
            self.assertEqual(self.collect()[0].bytes_scanned, 8)

    @unittest.skipUnless(os.name == "nt", "Windows junction scenario")
    def test_actual_junction_cycle(self):
        import subprocess
        self.file("sub/file", 1)
        link = self.root / "sub/loop"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.root)], capture_output=True)
        if result.returncode:
            self.skipTest("Junction creation unavailable")
        summary, store = self.collect()
        self.assertEqual(summary.bytes_scanned, 1)
        self.assertEqual(summary.directories_scanned, 2)
        self.assertEqual(store.folder_count, 2)

    def test_file_mount_boundary_and_deleted_entry_race(self):
        rejected = self.file("mount-file", 11)
        self.file("good", 1)
        self.assertEqual(self.collect(boundary_paths=(str(rejected),))[0].bytes_scanned, 1)
        original = os.scandir
        class Vanished:
            def is_dir(self, **kwargs):
                return False
            def is_file(self, **kwargs):
                return True
            def stat(self, **kwargs):
                raise FileNotFoundError("deleted")
        class Entries:
            def __enter__(inner):
                inner.entries = original(self.root)
                return iter([Vanished(), *inner.entries])
            def __exit__(inner, *args):
                inner.entries.close()
        with patch("storage_scanner.folder.scanner.os.scandir", return_value=Entries()):
            summary, store = self.collect()
        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.bytes_scanned, 12)
        self.assertEqual(store.state, "partial")

    def test_file_root_reports_error_instead_of_a_completed_zero_total(self):
        file = self.file("file", 10)
        with self.assertRaisesRegex(ValueError, "root type"):
            scan(FolderConfig((str(file),)), lambda batch: None)

    def test_file_becoming_directory_is_not_counted_as_file_bytes(self):
        import stat
        from types import SimpleNamespace
        class Changed:
            def is_dir(self, **kwargs):
                return False
            def is_file(self, **kwargs):
                return True
            def stat(self, **kwargs):
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_size=4096)
        class Entries:
            def __enter__(self):
                return iter([Changed()])
            def __exit__(self, *args):
                pass
        with patch("storage_scanner.folder.scanner.os.scandir", return_value=Entries()):
            summary, store = self.collect()
        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.bytes_scanned, 0)
        self.assertEqual(summary.files_scanned, 0)

    def test_sorted_pages_only_after_finalization(self):
        store = FolderResultStore((str(self.root),))
        for i in range(450):
            store.add_batch((DirectoryDelta(str(self.root / str(i)), i + 1),))
        key = path_key(str(self.root))
        self.assertEqual(store.children(key)[0].size_bytes, 1)
        store.finish(complete=True)
        page = store.children(key, 200, 200)
        self.assertEqual(len(page), 200)
        self.assertEqual(page[0].size_bytes, 250)
        self.assertEqual(store.node(key).size_bytes, sum(range(1, 451)))

    def test_independent_modules_have_no_cross_imports(self):
        for folder, forbidden in (("folder", ("storage_scanner.core", "storage_scanner.gui.controller")),
                                  ("core", ("storage_scanner.folder",))):
            for file in Path("storage_scanner", folder).glob("*.py"):
                tree = ast.parse(file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                             [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                    self.assertFalse(any(n.startswith(forbidden) for n in names), str(file))


class FolderControllerTests(unittest.TestCase):
    def wait(self, controller):
        end = time.perf_counter() + 5
        while time.perf_counter() < end:
            value = controller.snapshot()
            if value.finished:
                return value
            time.sleep(.002)
        self.fail("Collector did not finish")

    def test_actual_workers_share_gate_across_all_mode_orders(self):
        from storage_scanner.gui.controller import ScanController
        from storage_scanner.core.models import ScanConfig, ScanSummary
        for first_mode, second_mode in (("file", "folder"), ("folder", "file"),
                                        ("file", "file"), ("folder", "folder")):
            with self.subTest(order=(first_mode, second_mode)):
                gate = Lock()
                started, exited = Event(), Event()
                calls = []
                def fake(config, batch, progress, cancel):
                    calls.append(config)
                    if len(calls) == 1:
                        started.set()
                        while not cancel():
                            time.sleep(.001)
                        time.sleep(.02)
                        exited.set()
                    else:
                        self.assertTrue(exited.is_set(), "Overlapping traversal")
                    cls = FolderSummary if isinstance(config, FolderConfig) else ScanSummary
                    return cls(0, 0, 0, 0, 0.0, cancel())
                def create(mode):
                    cls, config = (FolderController, FolderConfig) if mode == "folder" else (ScanController, ScanConfig)
                    return cls(gate), config((str(Path.cwd()),))
                first, cfg1 = create(first_mode)
                second, cfg2 = create(second_mode)
                with patch("storage_scanner.gui.controller.scan", side_effect=fake), patch(
                        "storage_scanner.folder.controller.scan", side_effect=fake):
                    first.start(cfg1)
                    self.assertTrue(started.wait(2))
                    first.close()
                    second.start(cfg2)
                    result = self.wait(second)
                    self.assertIsNone(result.error)
                    self.assertEqual(len(calls), 2)
                second.close()

    def test_failure_and_collector_failure_release_gate(self):
        controller = FolderController()
        self.addCleanup(controller.close)
        config = FolderConfig((str(Path.cwd()),))
        with patch("storage_scanner.folder.controller.scan", side_effect=RuntimeError("worker")):
            controller.start(config)
            result = self.wait(controller)
            self.assertIn("worker", result.error)
            self.assertEqual(result.store.state, "partial")
        def fake(config, batch, progress, cancel):
            for _ in range(30):
                batch((DirectoryDelta(str(Path.cwd()), 1),))
            return FolderSummary(30, 1, 30, 0, .1, False)
        with patch("storage_scanner.folder.controller.scan", side_effect=fake), patch.object(
                FolderResultStore, "add_batch", side_effect=MemoryError("full")) as add_batch:
            controller.start(config)
            result = self.wait(controller)
            self.assertIn("Result collector", result.error)
            self.assertEqual(result.store.state, "partial")
            self.assertTrue(result.store.finalized)
            self.assertEqual(add_batch.call_count, 1)
        self.assertTrue(controller._scan_lock.acquire(blocking=False))
        controller._scan_lock.release()

    def test_restart_drops_late_batches_and_drains_final_batch(self):
        controller = FolderController()
        self.addCleanup(controller.close)
        started = Event()
        root = str(Path.cwd())
        calls = []
        def fake(config, batch, progress, cancel):
            calls.append(True)
            if len(calls) == 1:
                started.set()
                while not cancel():
                    time.sleep(.001)
                batch((DirectoryDelta(root, 999),))
            else:
                for _ in range(50):
                    batch((DirectoryDelta(root, 1),))
            return FolderSummary(50, 1, 50, 0, .1, cancel())
        with patch("storage_scanner.folder.controller.scan", side_effect=fake):
            first = controller.start(FolderConfig((root,)))
            self.assertTrue(started.wait(2))
            second = controller.start(FolderConfig((root,)))
            result = self.wait(controller)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(result.store.node(path_key(root)).size_bytes, 50)
        self.assertEqual(first.store.node(path_key(root)).size_bytes, 0)
        self.assertTrue(result.store.finalized)

    def test_superseded_run_skips_sort_and_cancelled_run_sorts_partial(self):
        root = str(Path.cwd())
        for restart in (False, True):
            with self.subTest(restart=restart):
                controller = FolderController()
                self.addCleanup(controller.close)
                started, release = Event(), Event()
                calls = []
                def fake(config, batch, progress, cancel):
                    if not calls:
                        calls.append(True)
                        batch((DirectoryDelta(str(Path(root) / 'small'), 1),
                               DirectoryDelta(str(Path(root) / 'large'), 20)))
                        started.set()
                        self.assertTrue(release.wait(3))
                    return FolderSummary(2, 3, 21, 0, .1, cancel())
                with patch('storage_scanner.folder.controller.scan', side_effect=fake):
                    controller.start(FolderConfig((root,)))
                    old = controller._run
                    self.assertTrue(started.wait(2))
                    if restart:
                        controller.start(FolderConfig((root,)))
                    else:
                        controller.cancel()
                    release.set()
                    result = self.wait(controller)
                    deadline = time.perf_counter() + 3
                    while not old.finished and time.perf_counter() < deadline:
                        time.sleep(.002)
                self.assertTrue(old.finished)
                self.assertIsNone(result.error)
                self.assertEqual(old.store.finalized, not restart)
                if not restart:
                    self.assertEqual(old.store.state, 'partial')
                    self.assertEqual([n.size_bytes for n in old.store.children(path_key(root))], [20, 1])
                self.assertTrue(controller._scan_lock.acquire(blocking=False))
                controller._scan_lock.release()

    def test_finish_stops_before_sorting_superseded_store(self):
        store = FolderResultStore((str(Path.cwd()),))
        with patch('storage_scanner.folder.results.sorted', create=True,
                   side_effect=AssertionError('Obsolete sort')):
            store.finish(complete=False, should_stop=lambda: True)
        self.assertFalse(store.finalized)
