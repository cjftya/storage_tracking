from pathlib import Path
import os
import tempfile
from threading import Event, Thread, get_ident
import time
import unittest
from unittest.mock import patch

from storage_scanner.folder.models import FolderConfig
from storage_scanner.folder.scanner import scan
from storage_scanner.folder.results import FolderResultStore
from storage_scanner.platform_fs.paths import path_key


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in range(12):
            directory = self.root / str(folder) / "nested"
            directory.mkdir(parents=True)
            for number in range(10):
                with (directory / str(number)).open("wb") as handle:
                    handle.truncate(number + folder)
        (self.root / "empty").mkdir()

    def test_workers_and_enumerators_have_identical_direct_and_total_bytes(self):
        expected = None
        caller = get_ident()
        for workers in (1, 2, 4, 8):
            for large_fetch in (False, True):
                with self.subTest(workers=workers, large_fetch=large_fetch):
                    config = FolderConfig((str(self.root), str(self.root / "0"), str(self.root)),
                                          workers=workers, large_fetch=large_fetch,
                                          excluded_paths=(str(self.root / "1"),),
                                          boundary_paths=(str(self.root / "2"),))
                    store = FolderResultStore(config.roots)
                    def batch(values):
                        self.assertEqual(get_ident(), caller)
                        self.assertLessEqual(len(values), config.batch_size)
                        store.add_batch(values)
                    result = scan(config, batch)
                    store.finish(complete=True)
                    state = (result.files_scanned, result.directories_scanned,
                             result.bytes_scanned, result.errors,
                             {k: (v.direct_bytes, v.size_bytes) for k, v in store._nodes.items()})
                    if expected is None:
                        expected = state
                    self.assertEqual(state, expected)
                    self.assertEqual(result.errors, 0)

    def test_child_runs_before_parent_enumeration_finishes(self):
        original = os.scandir
        child_done = Event()
        waiting = Event()
        real_share = __import__("storage_scanner.folder.parallel", fromlist=["_Directories"])._Directories.share
        def share(scheduler, path):
            # Ensure idle workers exist before the first child is offered.
            deadline = time.monotonic() + 3
            while scheduler.waiting == 0 and time.monotonic() < deadline:
                time.sleep(.001)
            return real_share(scheduler, path)
        class SlowParent:
            def __enter__(inner):
                inner.entries = original(self.root)
                return inner
            def __iter__(inner):
                yield next(inner.entries)
                waiting.set()
                if not child_done.wait(3):
                    raise RuntimeError("Child was held until parent completed")
                yield from inner.entries
            def __exit__(inner, *args):
                inner.entries.close()
        def enumerate_directory(path, **kwargs):
            if path_key(str(path)) == path_key(str(self.root)):
                return SlowParent()
            child_done.set()
            return original(path)
        with patch("storage_scanner.folder.scanner.scandir", side_effect=enumerate_directory), patch(
                "storage_scanner.folder.parallel._Directories.share", new=share):
            result = scan(FolderConfig((str(self.root),), workers=8), lambda b: None)
        self.assertTrue(waiting.is_set())
        self.assertTrue(child_done.is_set())
        self.assertEqual(result.errors, 0)

    def test_cancel_flushes_results_and_callbacks_fail_without_deadlock(self):
        cancel = Event()
        store = FolderResultStore((str(self.root),))
        def progress(value):
            if value.files_scanned:
                cancel.set()
        result = scan(FolderConfig((str(self.root),), workers=8, progress_interval=1e-9),
                      store.add_batch, progress, cancel.is_set)
        self.assertTrue(result.cancelled)
        self.assertEqual(store.node(path_key(str(self.root))).size_bytes, result.bytes_scanned)
        for callback in ("batch", "progress", "cancel"):
            outcome = []
            def fail(*args):
                raise RuntimeError("callback failure")
            def run():
                try:
                    calls = [fail if callback == "batch" else lambda b: None,
                             fail if callback == "progress" else lambda p: None,
                             fail if callback == "cancel" else lambda: False]
                    scan(FolderConfig((str(self.root),), workers=8, progress_interval=1e-9), *calls)
                except RuntimeError as exc:
                    outcome.append(str(exc))
            thread = Thread(target=run, daemon=True)
            thread.start()
            thread.join(5)
            self.assertFalse(thread.is_alive(), callback)
            self.assertEqual(outcome, ["callback failure"])

    def test_validation_and_pre_cancel(self):
        for workers in (0, 3, 16, True, 1.0):
            with self.assertRaises(ValueError):
                FolderConfig((str(self.root),), workers=workers)
        for workers in (1, 8):
            result = scan(FolderConfig((str(self.root),), workers=workers), lambda b: None,
                          should_cancel=lambda: True)
            self.assertTrue(result.cancelled)
            self.assertEqual(result.files_scanned, 0)
