from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch

from storage_scanner.core.models import FileMatch, ScanConfig, ScanProgress, ScanSummary
from storage_scanner.gui.controller import ScanController


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = ScanController()
        self.addCleanup(self.controller.close)

    def wait_finished(self, timeout=5):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            snapshot = self.controller.snapshot()
            if snapshot.finished:
                return snapshot
            time.sleep(0.002)
        self.fail("Worker/collector failed to finish")

    def test_cancel_restart_isolates_late_results_and_single_traversal(self):
        started = Event()
        old_done = Event()
        old = str(Path.cwd() / "old")
        new = str(Path.cwd() / "new")

        def fake_scan(config, on_batch, on_progress, should_cancel):
            if config.roots[0] == old:
                started.set()
                while not should_cancel():
                    time.sleep(0.002)
                on_batch((FileMatch(str(Path(old) / "late"), 1),))
                old_done.set()
                return ScanSummary(1, 1, 1, 0, 0.01, True)
            self.assertTrue(old_done.is_set(), "Two traversals ran concurrently")
            on_batch((FileMatch(str(Path(new) / "new"), 2),))
            on_progress(ScanProgress(1, 1, 1, 0, new))
            return ScanSummary(1, 1, 1, 0, 0.01, False)

        with patch("storage_scanner.gui.controller.scan", side_effect=fake_scan):
            first = self.controller.start(ScanConfig((old,), 0))
            self.assertTrue(started.wait(2))
            self.assertEqual(self.controller._run.queue.maxsize, 8)
            second = self.controller.start(ScanConfig((new,), 0))
            result = self.wait_finished()
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(result.run_id, second.run_id)
        self.assertEqual(result.store.match_count, 1)
        self.assertEqual(result.store.children(None)[0].path, str(Path(new)).lower()
                         if __import__("os").name == "nt" else new)
        self.assertEqual(first.store.match_count, 0)

    def test_collector_drains_before_finished(self):
        root = str(Path.cwd() / "many")
        def fake_scan(config, on_batch, on_progress, should_cancel):
            for batch in range(50):
                on_batch(tuple(FileMatch(str(Path(root) / f"{batch}-{i}"), 1) for i in range(256)))
            return ScanSummary(12800, 1, 12800, 0, 0.1, False)
        with patch("storage_scanner.gui.controller.scan", side_effect=fake_scan):
            self.controller.start(ScanConfig((root,), 0))
            result = self.wait_finished()
        self.assertEqual(result.store.match_count, result.summary.files_matched)
        self.assertIsNone(result.error)

    def test_worker_failure_is_reported(self):
        with patch("storage_scanner.gui.controller.scan", side_effect=RuntimeError("test failure")):
            self.controller.start(ScanConfig((str(Path.cwd()),)))
            result = self.wait_finished()
        self.assertIn("test failure", result.error)

    def test_collector_failure_drains_and_does_not_deadlock(self):
        root = str(Path.cwd())
        def fake_scan(config, on_batch, on_progress, should_cancel):
            for _ in range(30):
                on_batch((FileMatch(str(Path(root) / "f"), 1),))
            return ScanSummary(30, 1, 30, 0, 0.1, False)
        with patch("storage_scanner.gui.controller.scan", side_effect=fake_scan), patch(
                "storage_scanner.gui.controller.MemoryResultStore.add_batch", side_effect=MemoryError("full")):
            self.controller.start(ScanConfig((root,), 0))
            result = self.wait_finished()
        self.assertIn("Result collector", result.error)
