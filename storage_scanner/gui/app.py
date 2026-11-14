import queue
from threading import Thread, Lock
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from storage_scanner.core.filters import mib_to_bytes, bytes_to_mib
from storage_scanner.core.models import ScanConfig
from storage_scanner.gui.controller import ScanController
from storage_scanner.gui.icon import apply_window_icon
from storage_scanner.gui.tree_view import ResultTree
from storage_scanner.gui.folder_tree import FolderTree
from storage_scanner.folder.controller import FolderController
from storage_scanner.folder.models import FolderConfig
from storage_scanner.options import configured_scope
from storage_scanner.platform_fs.actions import open_folder
from storage_scanner.platform_fs.paths import path_key
from storage_scanner.platform_fs.volumes import resolve_scope, validate_roots


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
        self.root.title("Storage Scanner")
        apply_window_icon(self.root)
        self.root.geometry("1100x800")
        self.root.minsize(760, 600)
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        mode_controls = ttk.Frame(frame)
        mode_controls.pack(fill="x", pady=(0, 6))
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 10))
        self.mode = tk.StringVar(value=self.scan_mode)
        for label, value in (("Folder scan", "folder"), ("Large file scan", "file")):
            ttk.Radiobutton(mode_controls, text=label, variable=self.mode, value=value,
                            command=self.change_mode).pack(side="left", padx=(0, 8))
        ttk.Label(controls, text="Min size (MiB)").pack(side="left")
        self.minimum = tk.StringVar(value=bytes_to_mib(settings["min_size_bytes"]))
        self.minimum_entry = ttk.Entry(controls, textvariable=self.minimum, width=12)
        self.minimum_entry.pack(side="left", padx=8)
        self.minimum_entry.configure(state="normal" if self.scan_mode == "file" else "disabled")
        self.start_button = ttk.Button(controls, text="Scan / Restart", command=self.start, state="disabled")
        self.start_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        self.discover_button = ttk.Button(controls, text="Refresh drives", command=self.discover)
        self.discover_button.pack(side="right")
        scope = ttk.LabelFrame(frame, text="Scan scope", padding=8)
        scope.pack(fill="x", pady=(0, 8))
        ttk.Label(scope, text="Folder").grid(row=0, column=0, sticky="nw", padx=(0, 8))
        folders = ttk.Frame(scope)
        folders.grid(row=0, column=1, sticky="ew")
        self.roots_list = tk.Listbox(folders, height=1, selectmode="browse", exportselection=False)
        self.roots_list.grid(row=0, column=0, sticky="nsew")
        for path in (path_key(p) for p in self.settings.get("roots") or ()):
            self.roots_list.insert("end", path)
        horizontal = ttk.Scrollbar(folders, orient="horizontal", command=self.roots_list.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.roots_list.configure(xscrollcommand=horizontal.set)
        actions = ttk.Frame(folders)
        actions.grid(row=0, column=1, sticky="n", padx=(8, 0))
        self.add_folder_button = ttk.Button(actions, text="Select folder", command=self.add_folder)
        self.add_folder_button.pack(side="left")
        self.remove_folder_button = ttk.Button(actions, text="Clear", command=self.remove_folders, state="disabled")
        self.remove_folder_button.pack(side="left", padx=(4, 0))
        folders.columnconfigure(0, weight=1)
        self.roots_list.bind("<<ListboxSelect>>", self._update_scope_buttons)
        ttk.Label(scope, text="Excluded paths").grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=(8, 0))
        excludes = ttk.Frame(scope)
        excludes.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self.excludes_text = tk.Text(excludes, height=4, wrap="none", undo=True)
        self.excludes_text.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(excludes, orient="vertical", command=self.excludes_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.excludes_text.configure(yscrollcommand=scrollbar.set)
        excludes.columnconfigure(0, weight=1)
        scope.columnconfigure(1, weight=1)
        ttk.Label(scope, text="Choose one local or USB folder. Exclusions: one path per line.").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.meaning = tk.StringVar(value=self._mode_description())
        ttk.Label(frame, textvariable=self.meaning).pack(fill="x", pady=(0, 6))
        self.result_host = ttk.Frame(frame)
        self.result_host.pack(fill="both", expand=True)
        self._trees = {"file": ResultTree(self.result_host, on_open_folder=self.open_folder),
                       "folder": FolderTree(self.result_host, on_open_folder=self.open_folder)}
        self.results = self._trees[self.scan_mode]
        self.results.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Checking drives…")
        self.current_path = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, anchor="w").pack(fill="x", pady=(8, 0))
        ttk.Label(frame, textvariable=self.current_path, anchor="w", width=1).pack(fill="x")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.discover(initial=True)
        self._timer = self.root.after(self.update_ms, self._tick)

    def _new_controller(self):
        cls = FolderController if self.scan_mode == "folder" else ScanController
        return cls(scan_lock=self._scan_lock)

    def _mode_description(self):
        if self.scan_mode == "folder":
            return "All regular files; logical sizes. Aggregating until complete; cancelled/error totals are partial."
        return "Folder sizes include matching files only."

    def change_mode(self):
        mode = self.mode.get()
        if mode == self.scan_mode:
            return
        self.controller.close()
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
        self.roots_list.delete(0, "end")
        self.roots_list.insert("end", folder)
        self.roots_list.selection_set(0)
        self.roots_list.see(0)
        self._update_scope_buttons()

    def remove_folders(self):
        self.roots_list.delete(0, "end")
        self._update_scope_buttons()

    def discover(self, initial=False):
        self.discover_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Checking drives…")
        settings = dict(self.settings)
        # A folder selection opts into that local drive, including USB drives.
        # Discovery never adds drives to the user's scan list automatically.
        settings["include_removable"] = True
        if not initial:
            settings["roots"] = None
        results = self._discover_results

        def work():
            try:
                result = (*configured_scope(settings), initial)
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
        self.discovery, config, initial = result
        if initial:
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
                config = FolderConfig(scope.roots, scope.excluded_paths, scope.boundary_paths)
            else:
                config = ScanConfig(scope.roots, mib_to_bytes(self.minimum.get()),
                                    scope.excluded_paths, scope.boundary_paths)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return
        snapshot = self.controller.start(config)
        self.run_id = snapshot.run_id
        self._presented_run_id = None
        self._notified_run_id = None
        if self._notification_timer is not None:
            self.root.after_cancel(self._notification_timer)
            self._notification_timer = None
        self.results.reset(snapshot.store, snapshot.run_id)
        self.cancel_button.configure(state="normal")
        self.status.set("Scanning…")
        self.current_path.set("")

    def cancel(self):
        self.controller.cancel()
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelling…")

    def _tick(self):
        self._timer = None
        self._poll_folder_open()
        snapshot = self.controller.snapshot()
        if snapshot and snapshot.run_id == self.run_id:
            progress = snapshot.progress
            state = "Scanning"
            if not snapshot.finished or self._presented_run_id != snapshot.run_id:
                self.results.poll_changes()
            if snapshot.finished:
                self.cancel_button.configure(state="disabled")
                state = "Failed" if snapshot.error else "Cancelled" if snapshot.summary.cancelled else "Completed"
                if self._presented_run_id != snapshot.run_id:
                    # finished is published only after the collector's final
                    # batch. Drain its remaining changes without rebuilding.
                    self._presented_run_id = snapshot.run_id
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
        self.controller.close()
        for timer in (self._timer, self._discover_timer, self._notification_timer):
            if timer:
                self.root.after_cancel(timer)
        for tree in self._trees.values():
            tree.destroy()
        self.root.destroy()
