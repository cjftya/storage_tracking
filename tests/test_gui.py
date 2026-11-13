from pathlib import Path
import tempfile
import time
import unittest

from storage_scanner.core.models import FileMatch
from storage_scanner.core.results import MemoryResultStore
from storage_scanner.platform_fs.paths import path_key


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            from storage_scanner.gui.tree_view import ResultTree
        except ImportError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.view = ResultTree(self.root)
        self.view.pack(fill="both", expand=True)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def test_lazy_render_and_paging_for_20000_matches(self):
        folder = path_key(str(Path.cwd() / "stress"))
        store = MemoryResultStore((folder,))
        store.add_batch(tuple(FileMatch(str(Path(folder) / f"{i}.dat"), i) for i in range(20000)))
        self.view.reset(store)
        self.assertEqual(self.view.refresh(), 1)  # Only the matching root, still collapsed.
        item = self.view._items[folder]
        self.assertEqual(len(self.view.tree.get_children(item)), 1)  # Placeholder.
        self.view.tree.item(item, open=True)
        self.view.tree.focus(item)
        self.view.tree.event_generate("<<TreeviewOpen>>")
        self.root.update()
        heartbeat = []
        self.root.after_idle(lambda: heartbeat.append(True))
        for _ in range(30):
            inserted = self.view.refresh()
            self.assertLessEqual(inserted, self.view.INSERT_BUDGET)
            self.root.update()
        self.assertTrue(heartbeat)
        self.assertEqual(self.view._offsets[folder], 200)
        self.assertEqual(len(self.view.tree.get_children(item)), 201)
        more = item + ":more"
        self.view.tree.focus(more)
        self.view._more()
        for _ in range(30):
            self.view.refresh()
            self.root.update()
        self.assertEqual(self.view._offsets[folder], 400)
        self.assertLess(len(self.view.tree.get_children(item)), 500)
        self.assertFalse(self.errors)

    def test_application_scan_cancel_restart_and_final_stats(self):
        from storage_scanner.gui.app import ScannerApp
        self.view.destroy()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with (Path(directory) / "data").open("wb") as handle:
                handle.truncate(1024)
            settings = {"roots": (directory,), "min_size_bytes": 0, "extra_exclusions": (),
                        "include_removable": False, "use_default_exclusions": True, "update_ms": 50}
            app = ScannerApp(self.root, settings)
            self.addCleanup(app.controller.close)
            end = time.perf_counter() + 10
            while app.discovery is None and time.perf_counter() < end:
                self.root.update()
                time.sleep(0.002)
            self.assertIsNotNone(app.discovery, app.status.get())
            app.start()
            old_id = app.run_id
            app.cancel()
            app.start()
            self.assertGreater(app.run_id, old_id)
            while time.perf_counter() < end:
                self.root.update()
                result = app.controller.snapshot()
                if result.finished and app.status.get().startswith("Completed"):
                    break
                time.sleep(0.002)
            self.assertTrue(result.finished)
            self.assertEqual(result.store.match_count, 1)
            self.assertIn("matches 1", app.status.get())
            self.assertFalse(self.errors)
            for timer in (app._timer, app._discover_timer):
                if timer:
                    self.root.after_cancel(timer)

    def test_live_large_result_stream_keeps_gui_responsive(self):
        from unittest.mock import patch
        from storage_scanner.core.models import ScanProgress, ScanSummary
        from storage_scanner.gui.app import ScannerApp
        self.view.destroy()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            settings = {"roots": (directory,), "min_size_bytes": 0, "extra_exclusions": (),
                        "include_removable": False, "use_default_exclusions": True, "update_ms": 50}
            app = ScannerApp(self.root, settings)
            self.addCleanup(app.controller.close)
            deadline = time.perf_counter() + 10
            while app.discovery is None and time.perf_counter() < deadline:
                self.root.update()
                time.sleep(0.002)
            self.assertIsNotNone(app.discovery)
            heartbeats = []
            heartbeat_timer = None

            def heartbeat():
                nonlocal heartbeat_timer
                heartbeats.append(time.perf_counter())
                heartbeat_timer = self.root.after(10, heartbeat)

            def stream(config, on_batch, on_progress, should_cancel):
                count = 0
                tick = time.perf_counter()
                for offset in range(0, 20000, 256):
                    if should_cancel():
                        break
                    batch = tuple(FileMatch(str(Path(directory) / f"{i}.dat"), i)
                                  for i in range(offset, min(offset + 256, 20000)))
                    on_batch(batch)
                    count += len(batch)
                    on_progress(ScanProgress(count, 1, count, 0, directory))
                    time.sleep(0.005)
                return ScanSummary(count, 1, count, 0, time.perf_counter() - tick, should_cancel())

            try:
                with patch("storage_scanner.gui.controller.scan", side_effect=stream):
                    heartbeat()
                    app.start()
                    self.root.after(40, app.cancel)
                    while not app.controller.snapshot().finished and time.perf_counter() < deadline:
                        self.root.update()
                        time.sleep(0.002)
                    first = app.controller.snapshot()
                    self.assertTrue(first.summary.cancelled)
                    self.assertLess(first.store.match_count, 20000)
                    app.start()
                    while time.perf_counter() < deadline:
                        self.root.update()
                        snapshot = app.controller.snapshot()
                        if snapshot.finished:
                            break
                        time.sleep(0.002)
                    self.assertTrue(snapshot.finished)
                    self.assertEqual(snapshot.store.match_count, 20000)
                    self.assertGreater(len(heartbeats), 3)
                    self.assertFalse(self.errors)
            finally:
                for timer in (heartbeat_timer, app._timer, app._discover_timer):
                    if timer:
                        self.root.after_cancel(timer)
