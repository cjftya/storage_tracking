from pathlib import Path
import gc
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

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
        self.addCleanup(self._destroy_root)
        self.view = ResultTree(self.root)
        self.view.pack(fill="both", expand=True)
        self.errors = []
        errors = self.errors
        self.root.report_callback_exception = lambda *args: errors.append(args)
        info_patch = patch("storage_scanner.gui.app.messagebox.showinfo")
        error_patch = patch("storage_scanner.gui.app.messagebox.showerror")
        self.showinfo = info_patch.start()
        self.showerror = error_patch.start()
        self.addCleanup(info_patch.stop)
        self.addCleanup(error_patch.stop)

    def _destroy_root(self):
        self.root.destroy()
        # Dispose unreachable Tk variables on the GUI thread, before a later
        # test's worker allocation can trigger cyclic garbage collection.
        gc.collect()

    def make_app(self, directory=None, minimum=0, update_ms=50):
        from storage_scanner.gui.app import ScannerApp
        if self.view.winfo_exists():
            self.view.destroy()
        settings = {"roots": (directory,) if directory else None, "min_size_bytes": minimum, "extra_exclusions": (),
                    "include_removable": False, "use_default_exclusions": True, "update_ms": update_ms}
        app = ScannerApp(self.root, settings)

        def cleanup():
            app.controller.close()
            for timer in (app._timer, app._discover_timer, app._notification_timer):
                if timer:
                    self.root.after_cancel(timer)

        self.addCleanup(cleanup)
        deadline = time.perf_counter() + 5
        while app.discovery is None and time.perf_counter() < deadline:
            self.root.update()
            time.sleep(0.002)
        self.assertIsNotNone(app.discovery, app.status.get())
        return app

    def wait_until(self, predicate, timeout=5):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("GUI condition did not become true")

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
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with (Path(directory) / "data").open("wb") as handle:
                handle.truncate(1024)
            app = self.make_app(directory)
            end = time.perf_counter() + 10
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
            self.assertIn("Matches 1", app.status.get())
            self.assertEqual(self.showinfo.call_count, 1)
            self.assertFalse(self.errors)

    def test_live_large_result_stream_keeps_gui_responsive(self):
        from storage_scanner.core.models import ScanProgress, ScanSummary
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            app = self.make_app(directory)
            deadline = time.perf_counter() + 10
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
                        if not app.controller.snapshot().finished:
                            self.assertIs(app.results.store, app.controller.snapshot().store)
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
                        self.assertIs(app.results.store, snapshot.store)
                        time.sleep(0.002)
                    self.assertTrue(snapshot.finished)
                    self.assertEqual(snapshot.store.match_count, 20000)
                    self.assertGreater(len(heartbeats), 3)
                    self.assertFalse(self.errors)
            finally:
                if heartbeat_timer is not None:
                    self.root.after_cancel(heartbeat_timer)

    def test_completed_tree_totals_and_notification_are_not_refreshed_each_tick(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            for name in ("a", "b", "c"):
                folder = Path(directory) / name
                folder.mkdir()
                with (folder / "matching.bin").open("wb") as handle:
                    handle.truncate(100)
            with (Path(directory) / "small.bin").open("wb") as handle:
                handle.truncate(99)
            app = self.make_app(directory, minimum=100)
            with patch.object(app.results, "reset", wraps=app.results.reset) as reset, patch.object(
                    app.results, "refresh", wraps=app.results.refresh) as refresh:
                app.start()
                self.wait_until(lambda: self.showinfo.called)
                self.assertEqual(reset.call_count, 1)  # Attach live store once, never rebuild at completion.
                self.wait_until(lambda: not app.results._pending and not app.results._poll_changes
                                and app.results._render_timer is None)
                folder = path_key(directory)
                item = app.results._items[folder]
                self.assertEqual(app.results.tree.item(item, "values")[1], "300")
                before = refresh.call_count
                for _ in range(3):
                    self.root.after_cancel(app._timer)
                    app._tick()
                    self.root.update()
                self.assertEqual(reset.call_count, 1)
                self.assertEqual(refresh.call_count, before)
                self.assertEqual(self.showinfo.call_count, 1)
                app.results.tree.item(item, open=True)
                app.results.tree.focus(item)
                app.results.tree.event_generate("<<TreeviewOpen>>")
                self.wait_until(lambda: app.results._offsets[folder] == 3)
                children = app.results.tree.get_children(item)
                self.assertEqual(len(children), 3)
                self.assertTrue(all(app.results.tree.item(child, "values")[1] == "100" for child in children))
                self.assertEqual(app.results.tree.item(item, "values")[1], "300")
                self.assertFalse(self.errors)

    def test_context_menu_opens_only_the_right_clicked_folder(self):
        folder = path_key(str(Path.cwd() / "context folder"))
        store = MemoryResultStore((folder,))
        store.add_batch((FileMatch(str(Path(folder) / "file.bin"), 300),))
        opened = []
        self.view.on_open_folder = opened.append
        self.view.reset(store)
        self.root.update()
        item = self.view._items[folder]
        event = SimpleNamespace(type=SimpleNamespace(name="ButtonPress"), y=10, x_root=20, y_root=30)
        with patch.object(self.view.tree, "identify_row", return_value=item), patch.object(
                self.view.menu, "tk_popup") as popup, patch.object(self.view.menu, "grab_release"):
            self.assertEqual(self.view._show_context_menu(event), "break")
            popup.assert_called_once_with(20, 30)
        self.assertEqual(self.view.tree.selection(), (item,))
        self.view.menu.invoke(0)
        self.assertEqual(opened, [folder])
        self.view.tree.item(item, open=True)
        self.view.tree.focus(item)
        self.view._open()
        self.root.update()
        file_item = self.view.tree.get_children(item)[0]
        with patch.object(self.view.tree, "identify_row", return_value=file_item), patch.object(
                self.view.menu, "tk_popup") as popup:
            self.view._show_context_menu(event)
            popup.assert_not_called()
        self.view.reset(None)
        self.view._open_context_folder()
        self.assertEqual(opened, [folder])

    def test_folder_open_errors_are_reported_on_gui_thread(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            app = self.make_app(directory)
            with patch("storage_scanner.gui.app.open_folder", side_effect=OSError("folder disappeared")):
                app.open_folder(directory)
                self.wait_until(lambda: self.showerror.called)
            self.assertEqual(self.showerror.call_args.args[0], "Cannot open folder")
            self.assertIn("folder disappeared", self.showerror.call_args.args[1])
            self.assertFalse(self.errors)

    def test_cancelled_and_superseded_runs_do_not_show_completion_dialog(self):
        from storage_scanner.gui.controller import RunSnapshot
        from storage_scanner.core.models import ScanProgress, ScanSummary
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            app = self.make_app(directory)
            app.run_id = 2
            cancelled = RunSnapshot(2, MemoryResultStore((directory,)), ScanProgress(),
                                    ScanSummary(0, 0, 0, 0, 0.1, True), True, None, 0.1)
            with patch.object(app.controller, "snapshot", return_value=cancelled):
                self.root.after_cancel(app._timer)
                app._tick()
                self.root.update()
            old = RunSnapshot(1, cancelled.store, ScanProgress(),
                              ScanSummary(0, 0, 0, 0, 0.1, False), True, None, 0.1)
            app._notify_finished(old)
            self.showinfo.assert_not_called()

    def test_one_second_live_updates_and_cancel_final_batch(self):
        from threading import Event
        from storage_scanner.core.models import ScanProgress, ScanSummary
        next_batch = Event()
        finish = Event()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            app = self.make_app(directory, update_ms=1000)
            folder = path_key(directory)
            subfolder = str(Path(folder) / "nested")

            def stream(config, on_batch, on_progress, should_cancel):
                on_batch((FileMatch(str(Path(subfolder) / "first"), 100),))
                on_progress(ScanProgress(1, 2, 1, 0, subfolder))
                if next_batch.wait(8):
                    on_batch((FileMatch(str(Path(subfolder) / "second"), 200),))
                    on_progress(ScanProgress(2, 2, 2, 0, subfolder))
                finish.wait(8)
                # Emulate the engine's final partial batch on cancellation.
                on_batch((FileMatch(str(Path(subfolder) / "last"), 400),))
                on_progress(ScanProgress(3, 2, 3, 0, subfolder))
                return ScanSummary(3, 2, 3, 0, 0.1, should_cancel())

            try:
                with patch("storage_scanner.gui.controller.scan", side_effect=stream):
                    app.start()
                    self.wait_until(lambda: folder in app.results._items)
                    self.assertFalse(app.controller.snapshot().finished)
                    root_item = app.results._items[folder]
                    app.results.tree.item(root_item, open=True)
                    app.results.tree.focus(root_item)
                    app.results._open()
                    self.wait_until(lambda: subfolder in app.results._items)
                    nested_item = app.results._items[subfolder]
                    self.assertEqual(len(app.results.tree.get_children(nested_item)), 1)
                    self.assertEqual(len(app.results._paths), 2)  # Closed children stay lazy.
                    next_batch.set()
                    self.wait_until(lambda: app.results.tree.item(root_item, "values")[1] == "300")
                    self.assertFalse(app.controller.snapshot().finished)
                    self.assertEqual(app.results.tree.item(nested_item, "values")[1], "300")
                    self.assertEqual(len(app.results._paths), 2)
                    app.results.tree.item(nested_item, open=True)
                    app.results.tree.focus(nested_item)
                    app.results._open()
                    self.wait_until(lambda: app.results._offsets[subfolder] == 2)
                    app.cancel()
                    finish.set()
                    self.wait_until(lambda: app.status.get().startswith("Cancelled"))
                    self.wait_until(lambda: app.results._offsets[subfolder] == 3)
                    snapshot = app.controller.snapshot()
                    self.assertEqual(snapshot.store.match_count, snapshot.summary.files_matched)
                    self.assertEqual(snapshot.store.match_count, 3)
                    self.assertEqual(app.results.tree.item(root_item, "values")[1], "700")
                    self.assertEqual(app.results._items[folder], root_item)
                    self.assertEqual(len(app.results.tree.get_children(nested_item)), 3)
                    self.assertFalse(self.showinfo.called)
                    self.assertFalse(self.errors)
            finally:
                next_batch.set()
                finish.set()

    def test_live_paging_preserves_loaded_range_and_selection(self):
        folder = path_key(str(Path.cwd() / "live-paging"))
        store = MemoryResultStore((folder,))
        store.add_batch(tuple(FileMatch(str(Path(folder) / f"{i}.bin"), i) for i in range(250)))
        self.view.reset(store)
        self.wait_until(lambda: folder in self.view._items)
        item = self.view._items[folder]
        self.view.tree.item(item, open=True)
        self.view.tree.focus(item)
        self.view._open()
        self.wait_until(lambda: self.view._offsets[folder] == 200)
        first_page = self.view.tree.get_children(item)[:200]
        self.view.tree.focus(item + ":more")
        self.view._more()
        self.wait_until(lambda: self.view._offsets[folder] == 250)
        self.view.tree.selection_set(first_page[10])
        self.root.geometry("900x500")
        self.root.deiconify()
        self.root.update()
        self.view.tree.yview_moveto(0.4)
        self.root.update_idletasks()
        top_row = self.view.tree.identify_row(40)
        self.assertTrue(top_row)
        store.add_batch(tuple(FileMatch(str(Path(folder) / f"{i}.bin"), i) for i in range(250, 450)))
        self.view.poll_changes()
        self.wait_until(lambda: self.view._offsets[folder] == 400)
        self.assertEqual(self.view.tree.get_children(item)[:200], first_page)
        self.assertEqual(self.view.tree.selection(), (first_page[10],))
        self.assertEqual(self.view.tree.identify_row(40), top_row)
        self.assertTrue(self.view.tree.item(item, "open"))
        self.assertEqual(len(self.view.tree.get_children(item)), 401)
        for _ in range(3):
            self.view.poll_changes()
            self.view.refresh()
        self.assertEqual(len(self.view._paths), 401)
        self.assertEqual(self.view._offsets[folder], 400)
        self.assertFalse(self.errors)

    def test_change_backlog_yields_and_is_not_lost(self):
        folder = path_key(str(Path.cwd() / "dirty-backlog"))
        store = MemoryResultStore((folder,))
        store.add_batch(tuple(FileMatch(str(Path(folder) / str(i) / "file"), 1) for i in range(1800)))
        self.view.reset(store)
        self.wait_until(lambda: folder in self.view._items)
        item = self.view._items[folder]
        self.view.tree.item(item, open=True)
        self.view.tree.focus(item)
        self.view._open()
        self.wait_until(lambda: self.view._offsets[folder] == 200)
        store.add_batch(tuple(FileMatch(str(Path(folder) / str(i) / "next"), 2) for i in range(1800)))
        self.view.poll_changes()
        self.view.refresh()
        self.assertTrue(self.view._dirty or self.view._poll_changes)
        heartbeat = []
        self.root.after(0, lambda: heartbeat.append(True))
        self.wait_until(lambda: bool(heartbeat))
        self.wait_until(lambda: not self.view._poll_changes and not self.view._dirty and not self.view._pending)
        self.assertEqual(self.view.tree.item(item, "values")[1], "5,400")
        for child in self.view.tree.get_children(item)[:200]:
            self.assertEqual(self.view.tree.item(child, "values")[1], "3")
        self.assertEqual(len(self.view._paths), 201)
        self.assertFalse(self.errors)

    def test_large_reset_uses_leaf_deletes_and_isolates_stale_callbacks(self):
        # A single 20,000-row subtree would defeat deletion split by root.
        parent = self.view.tree.insert("", "end", open=True, text="old")
        self.view._paths[parent] = "old"
        for i in range(20000):
            item = self.view.tree.insert(parent, "end", text=str(i))
            self.view._paths[item] = str(i)
        old_tree = self.view.tree
        generation = self.view._generation
        folder = path_key(str(Path.cwd() / "new-run"))
        store = MemoryResultStore((folder,))
        store.add_batch((FileMatch(str(Path(folder) / "new-file"), 123),))
        self.view.reset(store, run_id=2)
        self.view._render_pending(generation)
        self.view._stripe_visible_rows(generation)
        self.view._on_scroll(generation, "0", "1")
        self.view._open(SimpleNamespace(widget=old_tree))
        self.assertFalse(self.view.tree.get_children())
        self.assertIsNot(self.view.tree, old_tree)
        actual_delete = old_tree.delete
        deleted = []

        def leaf_delete(item):
            self.assertFalse(old_tree.get_children(item))
            deleted.append(item)
            actual_delete(item)

        heartbeat = []
        self.root.after(0, lambda: heartbeat.append(True))
        with patch.object(old_tree, "delete", side_effect=leaf_delete):
            for run_id in range(3, 7):
                self.view.reset(store, run_id=run_id)
            self.wait_until(lambda: bool(heartbeat))
            self.assertTrue(self.view._retired)
            self.wait_until(lambda: not self.view._retired, timeout=10)
        self.wait_until(lambda: folder in self.view._items)
        self.assertEqual(len(deleted), 20001)
        self.assertEqual(self.view.run_id, 6)
        self.assertEqual(len(self.view._paths), 1)
        self.assertFalse(self.errors)

    def test_folder_picker_replaces_single_scope_and_clears_it(self):
        app = self.make_app()
        self.assertEqual(app.roots_list.size(), 0)
        self.assertEqual(str(app.start_button["state"]), "disabled")
        self.assertIsNone(app.controller.snapshot())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with patch("storage_scanner.gui.app.filedialog.askdirectory", return_value=directory) as picker:
                app.add_folder()
                app.add_folder()
                self.assertTrue(picker.call_args.kwargs["mustexist"])
                self.assertIs(picker.call_args.kwargs["parent"], self.root)
            self.assertEqual(app.roots_list.get(0, "end"), (path_key(directory),))
            self.assertEqual(str(app.start_button["state"]), "normal")
            replacement = Path(directory) / "replacement"
            replacement.mkdir()
            with patch("storage_scanner.gui.app.filedialog.askdirectory", return_value=str(replacement)):
                app.add_folder()
            self.assertEqual(app.roots_list.get(0, "end"), (path_key(str(replacement)),))
            with patch("storage_scanner.gui.app.filedialog.askdirectory", return_value=""):
                app.add_folder()
            self.assertEqual(app.roots_list.get(0, "end"), (path_key(str(replacement)),))
            app.remove_folders()
            self.assertEqual(app.roots_list.size(), 0)
            self.assertEqual(str(app.start_button["state"]), "disabled")
        self.assertFalse(self.errors)

    def test_refresh_keeps_selected_scope_and_edited_exclusions(self):
        from storage_scanner.gui.app import configured_scan
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            app = self.make_app(directory)
            defaults = {path_key(p) for p in (*app.discovery.default_exclusions,
                                            *app.discovery.mandatory_exclusions)}
            self.assertTrue(defaults.issubset(set(app._lines(app.excludes_text))))
            extra = Path(directory) / "another"
            extra.mkdir()
            with patch("storage_scanner.gui.app.filedialog.askdirectory", return_value=str(extra)):
                app.add_folder()
            edited = str(extra / "skip")
            app.excludes_text.delete("1.0", "end")
            app.excludes_text.insert("1.0", edited)
            expected_scope = app.roots_list.get(0, "end")
            self.assertEqual(expected_scope, (path_key(str(extra)),))
            with patch("storage_scanner.gui.app.configured_scan", wraps=configured_scan) as discover:
                app.discover()
                self.wait_until(lambda: app._discover_timer is None)
            self.assertTrue(discover.call_args.args[0]["include_removable"])
            self.assertEqual(app.roots_list.get(0, "end"), expected_scope)
            self.assertEqual(app.excludes_text.get("1.0", "end-1c"), edited)
            with patch.object(app.controller, "start", wraps=app.controller.start) as start:
                app.start()
            config = start.call_args.args[0]
            self.assertEqual(config.roots, expected_scope)
            self.assertEqual(set(config.excluded_paths), {path_key(edited), *app.discovery.mandatory_exclusions})
            self.wait_until(lambda: app.controller.snapshot().finished)
        self.assertFalse(self.errors)

    def test_explicit_usb_selection_and_network_rejection(self):
        from storage_scanner.platform_fs.volumes import Discovery, make_config
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            local, usb, network = (path_key(str(Path(directory) / name)) for name in ("local", "usb", "network"))
            for path in (local, usb, network):
                Path(path).mkdir()
            (Path(usb) / "match.bin").write_bytes(b"test")
            discovery = Discovery((local, usb), (local, usb, network), ())
            with patch("storage_scanner.gui.app.configured_scan",
                       return_value=(discovery, make_config(discovery, roots=(local,)))) as discover:
                app = self.make_app(local)
            self.assertTrue(discover.call_args.args[0]["include_removable"])
            self.assertFalse(hasattr(app, "removable_button"))
            with patch("storage_scanner.gui.app.filedialog.askdirectory", return_value=usb):
                app.add_folder()
            self.assertEqual(app.roots_list.get(0, "end"), (usb,))
            app.start()
            self.wait_until(lambda: app.controller.snapshot().finished)
            self.assertEqual(app.controller.snapshot().store.match_count, 1)
            app.roots_list.delete(0, "end")
            app.roots_list.insert("end", network)
            with patch.object(app.controller, "start") as start:
                app.start()
                start.assert_not_called()
            self.assertEqual(self.showerror.call_args.args[0], "Invalid settings")
            app.roots_list.insert("end", usb)
            with patch.object(app.controller, "start") as start:
                app.start()
                start.assert_not_called()
            self.assertEqual(self.showerror.call_args.args[1], "Select one folder only.")
        self.assertFalse(self.errors)

    def test_row_colors_alternate_after_expand_collapse_scroll_and_reset(self):
        folder = path_key(str(Path.cwd() / "striped"))
        store = MemoryResultStore((folder,))
        store.add_batch(tuple(FileMatch(str(Path(folder) / "nested" / f"{i}.bin"), i) for i in range(20))
                        + tuple(FileMatch(str(Path(folder) / f"{i}.bin"), i) for i in range(250)))
        self.root.geometry("900x500")
        self.root.deiconify()
        self.view.reset(store)
        self.wait_until(lambda: folder in self.view._items)
        root_item = self.view._items[folder]
        self.view.tree.item(root_item, open=True)
        self.view.tree.focus(root_item)
        self.view._open()
        self.wait_until(lambda: self.view._offsets[folder] == 200)

        def assert_striped():
            self.root.update()
            rows = []

            def visible(parent=""):
                for item in self.view.tree.get_children(parent):
                    if self.view.tree.bbox(item):
                        rows.append(item)
                    if self.view.tree.item(item, "open"):
                        visible(item)

            visible()
            self.assertGreater(len(rows), 2)
            tags = [next(tag for tag in self.view.tree.item(item, "tags")
                         if tag in self.view.ROW_TAGS) for item in rows]
            self.assertTrue(all(a != b for a, b in zip(tags, tags[1:])), list(zip(rows, tags)))
            return rows

        assert_striped()
        nested = self.view._items[str(Path(folder) / "nested")]
        self.view.tree.item(nested, open=True)
        self.view.tree.focus(nested)
        self.view._open()
        self.wait_until(lambda: self.view._offsets[str(Path(folder) / "nested")] == 20)
        assert_striped()
        self.view.tree.item(nested, open=False)
        self.view.tree.event_generate("<<TreeviewClose>>")
        assert_striped()
        self.view.tree.yview_moveto(0.5)
        rows = assert_striped()
        self.view.tree.selection_set(rows[1])
        self.view.tree.yview_scroll(1, "units")
        assert_striped()
        self.assertEqual(self.view.tree.selection(), (rows[1],))
        generation = self.view._generation
        self.view.reset(store)
        self.view._stripe_visible_rows(generation)
        self.wait_until(lambda: folder in self.view._items)
        self.assertEqual(str(self.view.tree.tag_configure(self.view.ROW_TAGS[0], "background")), "#f5f5f5")
        self.assertEqual(str(self.view.tree.tag_configure(self.view.ROW_TAGS[1], "background")), "#e8e8e8")
        self.assertFalse(self.errors)
