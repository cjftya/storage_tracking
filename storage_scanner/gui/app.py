from datetime import datetime
from pathlib import Path
import queue
import os
from threading import Thread, Lock
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from storage_scanner.report import write_report
from storage_scanner.core.filters import mib_to_bytes, bytes_to_mib
from storage_scanner.core.models import ScanConfig
from storage_scanner.gui.theme import ThemeManager
from storage_scanner.gui.icon import set_window_icon
from storage_scanner.gui.controller import ScanController
from storage_scanner.gui.tree_view import ResultTree
from storage_scanner.gui.folder_tree import FolderTree
from storage_scanner.folder.controller import FolderController
from storage_scanner.folder.models import FolderConfig
from storage_scanner.options import configured_scope
from storage_scanner.platform_fs.actions import open_folder
from storage_scanner.platform_fs.paths import path_key
from storage_scanner.platform_fs.filters import PathFilter
from storage_scanner.platform_fs.volumes import resolve_scope, validate_roots, default_scan_root


class ScannerApp:
    def __init__(self, root, settings, *, notify_on_completion=True):
        self.root = root
        self.settings = dict(settings)
        if self.settings.get("roots"):
            self.settings["roots"] = tuple(self.settings["roots"][:1])
        self.update_ms = settings.get("update_ms", 1000)
        if not isinstance(self.update_ms, int) or self.update_ms < 50:
            raise ValueError("gui_update_ms must be an integer >= 50")
        self._scan_lock = Lock()
        self.scan_mode = settings.get("scan_mode", "folder")
        if self.scan_mode not in ("folder", "file"):
            raise ValueError("scan_mode must be folder or file")
        self.controller = self._new_controller()
        self.discovery = None
        self.run_id = None
        self._presented_run_id = None
        self._notified_run_id = None
        self.notify_on_completion = notify_on_completion
        self._closed = False
        self._timer = None
        self._discover_timer = None
        self._notification_timer = None
        self._discover_results = queue.Queue(maxsize=1)
        self._folder_open_results = queue.Queue(maxsize=1)
        self._opening_folder = False
        self._location_edited = False
        self.report_directory = Path.cwd() / "reports"
        self._last_report_path = None
        self._report_results = queue.Queue()
        self._report_workers = []
        self._scan_config = None
        self._scan_started_at = None
        self.root.title("Storage Scanner")
        set_window_icon(self.root)
        self.root.geometry("1240x820")
        self.root.minsize(980, 780)
        self._build_interface(settings)
        self.theme_manager = ThemeManager(root, self._apply_palette,
                                          settings.get("theme_mode", "system"))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.discover(initial=True)
        self._timer = self.root.after(self.update_ms, self._tick)

    def _build_interface(self, settings):
        shell = ttk.Frame(self.root, padding=16)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 16))
        brand = ttk.Frame(header)
        brand.pack(side="left")
        ttk.Label(brand, text="Storage Scanner", style="Title.TLabel").pack(anchor="w")
        appearance = ttk.Frame(header)
        appearance.pack(side="right")
        ttk.Label(appearance, text="Theme", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.theme = tk.StringVar(value=settings.get("theme_mode", "system").title())
        self.theme_selector = ttk.Combobox(appearance, textvariable=self.theme,
                                         values=("System", "Day", "Night"), state="readonly", width=12)
        self.theme_selector.pack()
        self.theme_selector.bind("<<ComboboxSelected>>", lambda e: self.theme_manager.select(self.theme.get().lower()))
        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, style="Card.TFrame", padding=16, width=300)
        sidebar.pack(side="left", fill="y", padx=(0, 20))
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="Scan mode", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        self.mode = tk.StringVar(value=self.scan_mode)
        for label, value in (("Folder usage", "folder"), ("Large files", "file")):
            ttk.Radiobutton(sidebar, text=label, variable=self.mode, value=value,
                            command=self.change_mode).pack(fill="x", pady=3)
        ttk.Label(sidebar, text="Minimum file size (MiB)", style="CardMuted.TLabel").pack(anchor="w", pady=(10, 6))
        self.minimum = tk.StringVar(value=bytes_to_mib(settings["min_size_bytes"]))
        self.minimum_entry = ttk.Entry(sidebar, textvariable=self.minimum)
        self.minimum_entry.pack(fill="x")
        self.minimum_entry.configure(state="normal" if self.scan_mode == "file" else "disabled")
        location = ttk.Frame(shell, style="Card.TFrame", padding=14)
        location.pack(fill="x", before=body, pady=(0, 16))
        ttk.Label(location, text="Scan location", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        location_row = ttk.Frame(location, style="Card.TFrame")
        location_row.pack(fill="x")
        path_area = ttk.Frame(location_row, style="Card.TFrame")
        path_area.pack(side="left", fill="x", expand=True)
        self.roots_list = tk.Listbox(path_area, height=2, selectmode="browse", exportselection=False)
        self.roots_list.pack(fill="x")
        for path in (path_key(p) for p in self.settings.get("roots") or ()):
            self.roots_list.insert("end", path)
        horizontal = ttk.Scrollbar(path_area, orient="horizontal", command=self.roots_list.xview)
        horizontal.pack(fill="x")
        self.roots_list.configure(xscrollcommand=horizontal.set)
        actions = ttk.Frame(location_row, style="Card.TFrame")
        actions.pack(side="right", padx=(14, 0))
        self.add_folder_button = ttk.Button(actions, text="Select folder", command=self.add_folder)
        self.add_folder_button.pack(side="left")
        self.remove_folder_button = ttk.Button(actions, text="Clear", command=self.remove_folders, state="disabled")
        self.remove_folder_button.pack(side="left", padx=(6, 0))
        self.roots_list.bind("<<ListboxSelect>>", self._update_scope_buttons)
        ttk.Separator(sidebar).pack(fill="x", pady=10)
        ttk.Label(sidebar, text="Excluded paths", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="One path per line", style="CardMuted.TLabel").pack(anchor="w", pady=(4, 8))
        excludes = ttk.Frame(sidebar, style="Card.TFrame")
        excludes.pack(fill="both", expand=True)
        self.excludes_text = tk.Text(excludes, height=4, wrap="none", undo=True)
        self.excludes_text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(excludes, orient="vertical", command=self.excludes_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.excludes_text.configure(yscrollcommand=scrollbar.set)
        self.discover_button = ttk.Button(sidebar, text="Refresh drives", command=self.discover)
        self.discover_button.pack(fill="x", pady=(16, 0))
        self.report_location_button = ttk.Button(sidebar, text="Report save location...",
                                                 command=self.choose_report_directory)
        self.report_location_button.pack(fill="x", pady=(8, 0))
        self.report_location = tk.StringVar(value=str(self.report_directory))
        ttk.Label(sidebar, textvariable=self.report_location, style="CardMuted.TLabel",
                  wraplength=260).pack(fill="x", pady=(6, 0))
        workspace = ttk.Frame(body)
        workspace.pack(side="left", fill="both", expand=True)
        toolbar = ttk.Frame(workspace)
        toolbar.pack(fill="x", pady=(0, 14))
        ttk.Label(toolbar, text="Results", style="Section.TLabel").pack(side="left")
        self.start_button = ttk.Button(toolbar, text="Scan / Restart", style="Accent.TButton", command=self.start, state="disabled")
        self.start_button.pack(side="right")
        self.cancel_button = ttk.Button(toolbar, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=(0, 8))
        self.report_button = ttk.Button(toolbar, text="Report folder", state="disabled",
                                        command=lambda: self.open_folder(str(self._last_report_path.parent)))
        self.report_button.pack(side="right", padx=(0, 8))
        self.meaning = tk.StringVar(value=self._mode_description())
        self.description_label = ttk.Label(workspace, textvariable=self.meaning, style="Muted.TLabel", wraplength=640)
        self.description_label.pack(fill="x", pady=(0, 16))
        workspace.bind("<Configure>", lambda e: self.description_label.configure(wraplength=max(200, e.width - 10)))
        self.result_host = ttk.Frame(workspace, style="Card.TFrame", padding=1)
        self.result_host.pack(fill="both", expand=True)
        self._trees = {"file": ResultTree(self.result_host, on_open_folder=self.open_folder),
                       "folder": FolderTree(self.result_host, on_open_folder=self.open_folder)}
        self.results = self._trees[self.scan_mode]
        self.results.pack(fill="both", expand=True)
        footer = ttk.Frame(workspace, style="Card.TFrame", padding=14)
        footer.pack(fill="x", pady=(14, 0))
        self.scan_activity = tk.StringVar(value="Ready to scan")
        ttk.Label(footer, textvariable=self.scan_activity, style="CardTitle.TLabel").pack(fill="x", pady=(0, 10))
        self.status = tk.StringVar(value="Checking drives...")
        self.current_path = tk.StringVar()
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Card.TLabel", wraplength=640)
        self.status_label.pack(fill="x")
        self.report_status = tk.StringVar()
        ttk.Label(footer, textvariable=self.report_status, style="CardMuted.TLabel", width=1).pack(fill="x", pady=(6, 0))
        footer.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(200, e.width - 28)))
        ttk.Label(footer, textvariable=self.current_path, style="CardMuted.TLabel", width=1).pack(fill="x", pady=(6, 0))

    def _apply_palette(self, palette):
        for widget in (self.roots_list, self.excludes_text):
            widget.configure(background=palette["input"], foreground=palette["text"],
                             selectbackground=palette["accent"], selectforeground="#ffffff",
                             highlightbackground=palette["border"], highlightcolor=palette["accent"],
                             highlightthickness=1, borderwidth=0, font=("Segoe UI", 10))
        self.excludes_text.configure(insertbackground=palette["text"], padx=10, pady=8)
        for tree in self._trees.values():
            tree.apply_palette(palette)

    def _new_controller(self):
        cls = FolderController if self.scan_mode == "folder" else ScanController
        return cls(scan_lock=self._scan_lock)

    def _mode_description(self):
        if self.scan_mode == "folder":
            return "Total file size in each folder. Cancelled scans show partial totals."
        return "Files at or above the selected minimum size."

    def change_mode(self):
        mode = self.mode.get()
        if mode == self.scan_mode:
            return
        self.controller.close()
        self.scan_activity.set("Ready to scan")
        self.report_status.set("")
        self.report_button.configure(state="disabled")
        self.results.reset(None)
        self.results.pack_forget()
        self.scan_mode = mode
        self.controller = self._new_controller()
        self.run_id = self._presented_run_id = self._notified_run_id = None
        if self._notification_timer is not None:
            self.root.after_cancel(self._notification_timer)
            self._notification_timer = None
        self.results = self._trees[mode]
        self.results.reset(None)
        self.results.pack(fill="both", expand=True)
        self.minimum_entry.configure(state="normal" if mode == "file" else "disabled")
        self.meaning.set(self._mode_description())
        self.current_path.set("")
        self.cancel_button.configure(state="disabled")
        self.status.set("Ready - press Scan / Restart.")

    @staticmethod
    def _lines(widget):
        return tuple(path_key(line.strip()) for line in widget.get("1.0", "end").splitlines() if line.strip())

    def _update_scope_buttons(self, event=None):
        ready = self.discovery is not None and self._discover_timer is None
        self.start_button.configure(state="normal" if ready and self.roots_list.size() else "disabled")
        self.remove_folder_button.configure(state="normal" if self.roots_list.size() else "disabled")
        if ready and self.run_id is None:
            self.status.set("Ready" if self.roots_list.size() else "Select a folder to scan.")

    def add_folder(self):
        options = dict(parent=self.root, title="Select folder", mustexist=True)
        if self.roots_list.size():
            options["initialdir"] = self.roots_list.get(0)
        folder = filedialog.askdirectory(**options)
        if not folder:
            return
        folder = path_key(folder)
        self._location_edited = True
        self.roots_list.delete(0, "end")
        self.roots_list.insert("end", folder)
        self.roots_list.selection_set(0)
        self.roots_list.see(0)
        self._update_scope_buttons()

    def remove_folders(self):
        self._location_edited = True
        self.roots_list.delete(0, "end")
        self._update_scope_buttons()

    def discover(self, initial=False):
        self.discover_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Checking drives…")
        settings = dict(self.settings)
        # A folder selection opts into that local drive, including USB drives.
        # Initial discovery selects only the confirmed system volume when unset.
        settings["include_removable"] = True
        if not initial:
            settings["roots"] = None
        results = self._discover_results

        def work():
            try:
                discovery, scope = configured_scope(settings)
                default = default_scan_root(discovery) if initial and not settings.get("roots") else None
                result = (discovery, scope, initial, default)
            except Exception as exc:
                result = exc
            results.put(result)

        Thread(target=work, name="volume-discovery", daemon=True).start()
        self._discover_timer = self.root.after(100, self._poll_discovery)

    def _poll_discovery(self):
        try:
            result = self._discover_results.get_nowait()
        except queue.Empty:
            self._discover_timer = self.root.after(100, self._poll_discovery)
            return
        self._discover_timer = None
        self.discover_button.configure(state="normal")
        if isinstance(result, Exception):
            self.discovery = None
            self.status.set(f"Drives unavailable: {result}")
            return
        self.discovery, config, initial, default = result
        if initial:
            if default is not None and not self._location_edited and self.roots_list.size() == 0:
                self.roots_list.insert("end", default)
                self.roots_list.selection_set(0)
            self.excludes_text.insert("1.0", "\n".join(config.excluded_paths) + "\n")
        self._update_scope_buttons()
        self.status.set("Ready" if self.roots_list.size() else "Select a folder to scan.")

    def start(self):
        if self.discovery is None:
            return
        try:
            roots = tuple(self.roots_list.get(0, "end"))
            if not roots:
                raise ValueError("Select a folder first.")
            if len(roots) != 1:
                raise ValueError("Select one folder only.")
            # A root may be a subdirectory of an allowed local volume, never an
            # explicitly rejected mount (network, virtual, alias).
            validate_roots(self.discovery, roots)
            scope = resolve_scope(self.discovery, roots=roots,
                                  excluded_paths=self._lines(self.excludes_text))
            if self.scan_mode == "folder":
                config = FolderConfig(scope.roots, scope.excluded_paths, scope.boundary_paths,
                                      allowed_paths=scope.allowed_paths,
                                      workers=self.settings.get("folder_workers", 8 if os.name == "nt" else 1),
                                      large_fetch=self.settings.get("large_fetch", os.name == "nt"))
            else:
                config = ScanConfig(scope.roots, mib_to_bytes(self.minimum.get()),
                                    scope.excluded_paths, scope.boundary_paths,
                                    allowed_paths=scope.allowed_paths)
            if PathFilter(scope.excluded_paths, allowed_paths=scope.allowed_paths).excludes(roots[0]):
                raise ValueError(f"Scan root is excluded by path policy: {roots[0]}")
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return
        self._scan_config = config
        self._scan_started_at = datetime.now().astimezone()
        self.report_status.set("")
        self.report_button.configure(state="disabled")
        snapshot = self.controller.start(config)
        self.run_id = snapshot.run_id
        self._presented_run_id = None
        self._notified_run_id = None
        if self._notification_timer is not None:
            self.root.after_cancel(self._notification_timer)
            self._notification_timer = None
        self.results.reset(snapshot.store, snapshot.run_id)
        self.cancel_button.configure(state="normal")
        self.scan_activity.set("Scanning...")
        self.status.set("Scanning…")
        self.current_path.set("")

    def cancel(self):
        self.controller.cancel()
        self.scan_activity.set("Cancelling - finishing remaining results...")
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelling…")

    def _tick(self):
        self._timer = None
        self.theme_manager.poll()
        self._poll_folder_open()
        self._poll_reports()
        snapshot = self.controller.snapshot()
        if snapshot and snapshot.run_id == self.run_id:
            progress = snapshot.progress
            state = "Scanning"
            if not snapshot.finished or self._presented_run_id != snapshot.run_id:
                self.results.poll_changes()
            if snapshot.finished:
                self.cancel_button.configure(state="disabled")
                state = "Failed" if snapshot.error else "Cancelled" if snapshot.summary.cancelled else "Completed"
                self.scan_activity.set(state)
                if self._presented_run_id != snapshot.run_id:
                    # finished is published only after the collector's final
                    # batch. Drain its remaining changes without rebuilding.
                    self._presented_run_id = snapshot.run_id
                    if not snapshot.error and not snapshot.summary.cancelled:
                        self.results.expand_root()
                    self._start_report(snapshot)
            result_count = (f"Folders stored {snapshot.store.folder_count:,} | Bytes {progress.bytes_scanned:,}"
                            if self.scan_mode == "folder" else f"Matches {progress.files_matched:,}")
            if self.scan_mode == "folder" and snapshot.finished and snapshot.store.state == "partial":
                state += " (partial totals)"
            self.status.set(f"{state} | {self.scan_mode.title()} scan | Files {progress.files_scanned:,} | Folders {progress.directories_scanned:,}"
                            f" | {result_count} | Errors {progress.errors:,}"
                            f" | {snapshot.elapsed_seconds:.1f}s")
            self.current_path.set(snapshot.error or progress.current_directory)
            if snapshot.finished and self._notified_run_id != snapshot.run_id:
                self._notified_run_id = snapshot.run_id
                if self.notify_on_completion and (snapshot.error or not snapshot.summary.cancelled):
                    self._notification_timer = self.root.after_idle(self._notify_finished, snapshot)
        if not self._closed:
            self._timer = self.root.after(self.update_ms, self._tick)

    def _start_report(self, snapshot):
        if self._scan_config is None:
            return
        self.report_status.set("Saving Markdown report...")
        config, mode = self._scan_config, self.scan_mode
        started, directory = self._scan_started_at, self.report_directory
        results = self._report_results

        def work():
            try:
                path = write_report(directory, mode=mode, config=config,
                                    snapshot=snapshot, started_at=started)
                results.put((snapshot.store, path, None))
            except Exception as exc:
                results.put((snapshot.store, None, str(exc)))

        self._report_workers = [thread for thread in self._report_workers if thread.is_alive()]
        worker = Thread(target=work, name="markdown-report", daemon=True)
        self._report_workers.append(worker)
        worker.start()

    def choose_report_directory(self):
        selected = filedialog.askdirectory(parent=self.root, title="Report save location",
                                          initialdir=str(self.report_directory if self.report_directory.is_dir()
                                                         else self.report_directory.parent), mustexist=True)
        if selected:
            self.report_directory = Path(selected).resolve()
            self.report_location.set(str(self.report_directory))

    def _poll_reports(self):
        while True:
            try:
                store, path, error = self._report_results.get_nowait()
            except queue.Empty:
                break
            current = self.controller.snapshot()
            if current is None or current.store is not store:
                continue
            if error:
                self.report_status.set(f"Report could not be saved: {error}")
            else:
                self._last_report_path = path
                self.report_status.set(f"Report: {path}")
                self.report_button.configure(state="normal")

    def _notify_finished(self, snapshot):
        self._notification_timer = None
        current = self.controller.snapshot()
        if self._closed or snapshot.run_id != self.run_id or current is None or current.store is not snapshot.store:
            return
        if snapshot.error:
            messagebox.showerror("Scan failed", snapshot.error, parent=self.root)
            return
        summary = snapshot.summary
        result_text = (f"Folders stored: {snapshot.store.folder_count:,}\nLogical bytes: {summary.bytes_scanned:,}\n"
                       f"Totals: {snapshot.store.state}\n" if self.scan_mode == "folder"
                       else f"Matches: {summary.files_matched:,}\n")
        messagebox.showinfo(
            "Scan complete",
            result_text +
            f"Files: {summary.files_scanned:,}\n"
            f"Errors: {summary.errors:,}\n"
            f"Time: {summary.elapsed_seconds:.1f}s", parent=self.root)

    def open_folder(self, path):
        if self._opening_folder:
            return
        self._opening_folder = True

        def work():
            error = None
            try:
                open_folder(path)
            except Exception as exc:
                error = getattr(exc, "stderr", None) or str(exc) or type(exc).__name__
            self._folder_open_results.put(error)

        Thread(target=work, name="open-folder", daemon=True).start()

    def _poll_folder_open(self):
        if not self._opening_folder:
            return
        try:
            error = self._folder_open_results.get_nowait()
        except queue.Empty:
            return
        self._opening_folder = False
        if error:
            messagebox.showerror("Cannot open folder", error, parent=self.root)

    def close(self):
        self._closed = True
        self.theme_manager.close()
        self.controller.close()
        for timer in (self._timer, self._discover_timer, self._notification_timer):
            if timer:
                self.root.after_cancel(timer)
        for tree in self._trees.values():
            tree.destroy()
        self.root.destroy()
