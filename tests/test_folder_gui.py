from pathlib import Path
import tempfile
import unittest
from threading import Event
import time
from unittest.mock import patch

from tests import test_gui as helpers
from storage_scanner.folder.models import DirectoryDelta
from storage_scanner.folder.models import FolderSummary
from storage_scanner.folder.results import FolderResultStore
from storage_scanner.gui.folder_tree import FolderTree
from storage_scanner.platform_fs.paths import path_key


class FolderGuiTests(unittest.TestCase):
    setUp = helpers.GuiTests.setUp
    _destroy_root = helpers.GuiTests._destroy_root
    make_app = helpers.GuiTests.make_app
    wait_until = helpers.GuiTests.wait_until

    def test_leaf_placeholder_clears_and_later_children_still_appear(self):
        self.view.destroy()
        view = FolderTree(self.root)
        view.pack(fill="both", expand=True)
        self.addCleanup(view.destroy)
        key = path_key(str(Path.cwd() / "fixture"))
        store = FolderResultStore((key,))
        store.add_batch((DirectoryDelta(key, 10),))
        view.reset(store)
        self.wait_until(lambda: key in view._items)
        item = view._items[key]
        view.tree.item(item, open=True)
        view.tree.focus(item)
        view._open()
        self.wait_until(lambda: not view.tree.get_children(item))
        child = path_key(str(Path(key) / "later"))
        store.add_batch((DirectoryDelta(child, 5),))
        view.poll_changes()
        self.wait_until(lambda: child in view._items)
        self.assertEqual(view._paths[view.tree.get_children(item)[0]], child)
        self.assertFalse(self.errors)

    def test_default_mode_switch_and_all_run_orders(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            (Path(directory) / "a").write_bytes(b"123")
            app = self.make_app(directory, minimum=100 * 1024 ** 2, scan_mode=None)
            self.assertEqual(app.scan_mode, "folder")
            self.assertEqual(str(app.minimum_entry["state"]), "disabled")
            for mode in ("folder", "file", "file", "folder", "folder", "file"):
                app.mode.set(mode)
                app.change_mode()
                if app.run_id is None:
                    self.assertIsNone(app.controller.snapshot(), "Selection must not auto-scan")
                app.start()
                self.wait_until(lambda: app.controller.snapshot().finished)
                self.wait_until(lambda: app._presented_run_id == app.run_id)
                snapshot = app.controller.snapshot()
                if mode == "folder":
                    self.assertEqual(snapshot.summary.bytes_scanned, 3)
                    self.assertEqual(snapshot.store.folder_count, 1)
                    self.assertNotIn("Matches", app.status.get())
                else:
                    self.assertEqual(snapshot.store.match_count, 0)
                    self.assertEqual(str(app.minimum_entry["state"]), "normal")
            self.assertFalse(self.errors)

    def test_folder_ignores_invalid_minimum_and_stale_file_notification(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            (Path(directory) / "a").write_bytes(b"a")
            app = self.make_app(directory, scan_mode="file")
            app.start()
            self.wait_until(lambda: app.controller.snapshot().finished)
            previous = app.controller.snapshot()
            app.mode.set("folder")
            app.change_mode()
            app.minimum.set("invalid file threshold")
            app.start()
            self.wait_until(lambda: app.controller.snapshot().finished)
            before = self.showinfo.call_count
            app._notify_finished(previous)
            self.assertEqual(self.showinfo.call_count, before)
            self.assertEqual(app.controller.snapshot().summary.bytes_scanned, 1)
            self.assertFalse(self.showerror.called)

    def test_final_sort_preserves_selection_expansion_and_lazy_pages(self):
        self.view.destroy()
        view = FolderTree(self.root)
        view.pack(fill="both", expand=True)
        self.addCleanup(view.destroy)
        root = path_key(str(Path.cwd() / "fixture"))
        store = FolderResultStore((root,))
        store.add_batch(tuple(DirectoryDelta(str(Path(root) / str(i)), i + 1) for i in range(600)))
        view.reset(store, 1)
        self.wait_until(lambda: root in view._items)
        item = view._items[root]
        view.tree.item(item, open=True)
        view.tree.focus(item)
        view._open()
        self.wait_until(lambda: view._offsets[root] == 200)
        chosen = view._items[path_key(str(Path(root) / "0"))]
        view.tree.selection_set(chosen)
        view.tree.item(chosen, open=True)
        store.finish(complete=True)
        view.poll_changes()
        self.wait_until(lambda: view._sorted and not view._pending and not view._dirty and not view._poll_changes)
        self.assertEqual(view.tree.selection(), (chosen,))
        self.assertTrue(view.tree.item(chosen, "open"))
        children = view.tree.get_children(item)
        self.assertEqual(view._paths[children[0]], path_key(str(Path(root) / "599")))
        self.assertLessEqual(len(view._paths), 402)
        self.assertEqual(view._offsets[root], 200)
        view.tree.focus(item + ":more")
        view._more()
        self.wait_until(lambda: view._offsets[root] == 400)
        self.assertFalse(self.errors)

    def test_partial_heading_and_large_mode_switch_retires_widgets(self):
        app = self.make_app(scan_mode="folder")
        root = path_key(str(Path.cwd() / "fixture"))
        store = FolderResultStore((root,))
        store.add_batch(tuple(DirectoryDelta(str(Path(root) / str(i)), 1) for i in range(4000)))
        view = app.results
        view.reset(store, 5)
        self.wait_until(lambda: root in view._items)
        item = view._items[root]
        view.tree.item(item, open=True)
        view._limits[root] = 4000
        view.tree.focus(item)
        view._open()
        self.wait_until(lambda: view._offsets[root] == 4000)
        store.finish(complete=False)
        view.poll_changes()
        self.wait_until(lambda: view._sorted)
        self.assertIn("partial", view.tree.heading("size", "text"))
        generation = view._generation
        for mode in ("file", "folder", "file", "folder"):
            app.mode.set(mode)
            app.change_mode()
        view._render_pending(generation)
        self.wait_until(lambda: not view._retired)
        self.assertIsNone(app.results.store)
        self.assertFalse(self.errors)

    def test_folder_one_second_updates_and_cancel_final_delta(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            app = self.make_app(directory, scan_mode="folder", update_ms=1000)
            begin = Event()
            def slow_scan(config, batch, progress, cancel):
                while not begin.wait(.002):
                    if cancel():
                        return FolderSummary(0, 0, 0, 0, .1, True)
                batch((DirectoryDelta(directory, 10),))
                while not cancel():
                    time.sleep(.002)
                batch((DirectoryDelta(directory, 4),))
                return FolderSummary(2, 1, 14, 0, 1.0, True)
            with patch("storage_scanner.folder.controller.scan", side_effect=slow_scan):
                app.start()
                key = path_key(directory)
                self.wait_until(lambda: key in app.results._items)
                item = app.results._items[key]
                self.assertEqual(app.update_ms, 1000)
                begin.set()
                self.wait_until(lambda: app.controller.snapshot().store.node(key).size_bytes == 10)
                self.assertEqual(app.results.tree.item(item, "values")[1], "0")
                self.wait_until(lambda: app.results.tree.item(item, "values")[1] == "10")
                app.cancel()
                self.wait_until(lambda: app.controller.snapshot().finished)
                self.wait_until(lambda: "partial" in app.status.get())
                self.wait_until(lambda: app.results.tree.item(item, "values")[1] == "14")
                self.assertFalse(self.showinfo.called)
                self.assertFalse(self.errors)
