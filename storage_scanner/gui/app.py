import queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from storage_scanner.core.filters import mib_to_bytes, bytes_to_mib
from storage_scanner.gui.controller import ScanController
from storage_scanner.gui.tree_view import ResultTree
from storage_scanner.options import configured_scan
from storage_scanner.platform_fs.paths import path_key
from storage_scanner.platform_fs.volumes import make_config


class ScannerApp:
    def __init__(self, root, settings):
        self.root = root
        self.settings = dict(settings)
        self.update_ms = settings.get("update_ms", 700)
        if not isinstance(self.update_ms, int) or self.update_ms < 50:
            raise ValueError("gui_update_ms must be an integer >= 50")
        self.controller = ScanController()
        self.discovery = None
        self.run_id = None
        self._closed = False
        self._timer = None
        self._discover_timer = None
        self._discover_results = queue.Queue(maxsize=1)
        self.root.title("Storage Scanner")
        self.root.geometry("1100x800")
        self.root.minsize(760, 600)
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 10))
        ttk.Label(controls, text="Minimum size (MiB, ≥)").pack(side="left")
        self.minimum = tk.StringVar(value=bytes_to_mib(settings["min_size_bytes"]))
        ttk.Entry(controls, textvariable=self.minimum, width=16).pack(side="left", padx=8)
        self.start_button = ttk.Button(controls, text="Start / Restart", command=self.start, state="disabled")
        self.start_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        self.removable = tk.BooleanVar(value=settings["include_removable"])
        self.removable_button = ttk.Checkbutton(controls, text="Include removable", variable=self.removable)
        self.removable_button.pack(side="left", padx=8)
        self.discover_button = ttk.Button(controls, text="Discover volumes", command=self.discover)
        self.discover_button.pack(side="right")
        scope = ttk.LabelFrame(frame, text="Scan scope — one absolute path per line", padding=8)
        scope.pack(fill="x", pady=(0, 8))
        ttk.Label(scope, text="Roots").grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.roots_text = tk.Text(scope, height=3, wrap="none", undo=True)
        self.roots_text.grid(row=0, column=1, sticky="ew")
        ttk.Label(scope, text="Excluded paths").grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=(8, 0))
        self.excludes_text = tk.Text(scope, height=4, wrap="none", undo=True)
        self.excludes_text.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        scope.columnconfigure(1, weight=1)
        ttk.Label(scope, text="Local volumes only; directory links are skipped. Expand folders to load matches.").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.results = ResultTree(frame)
        self.results.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Discovering local volumes…")
        self.current_path = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, anchor="w").pack(fill="x", pady=(8, 0))
        ttk.Label(frame, textvariable=self.current_path, anchor="w", width=1).pack(fill="x")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.discover(initial=True)
        self._timer = self.root.after(self.update_ms, self._tick)

    @staticmethod
    def _lines(widget):
        return tuple(path_key(line.strip()) for line in widget.get("1.0", "end").splitlines() if line.strip())

    def discover(self, initial=False):
        self.discover_button.configure(state="disabled")
        self.removable_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Discovering local volumes…")
        settings = dict(self.settings)
        settings["include_removable"] = self.removable.get()
        if not initial:
            settings["roots"] = None

        def work():
            try:
                result = configured_scan(settings)
            except Exception as exc:
                result = exc
            self._discover_results.put(result)

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
        self.removable_button.configure(state="normal")
        if isinstance(result, Exception):
            self.status.set(f"Volume discovery failed: {result}")
            return
        self.discovery, config = result
        for widget, lines in ((self.roots_text, config.roots), (self.excludes_text, config.excluded_paths)):
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(lines))
        self.start_button.configure(state="normal")
        self.status.set(f"Ready — {len(config.roots)} local scan root(s). Review scope, then Start.")

    def start(self):
        if self.discovery is None:
            return
        try:
            roots = self._lines(self.roots_text)
            if not roots:
                raise ValueError("Add at least one scan root")
            # A root may be a subdirectory of an allowed local volume, never an
            # explicitly rejected mount (network, virtual, removable, alias).
            from storage_scanner.platform_fs.volumes import validate_roots
            validate_roots(self.discovery, roots)
            config = make_config(self.discovery, roots=roots, min_size_bytes=mib_to_bytes(self.minimum.get()),
                                 excluded_paths=self._lines(self.excludes_text))
        except ValueError as exc:
            messagebox.showerror("Invalid scan settings", str(exc), parent=self.root)
            return
        snapshot = self.controller.start(config)
        self.run_id = snapshot.run_id
        self.results.reset(snapshot.store)
        self.cancel_button.configure(state="normal")
        self.status.set("Scanning…")
        self.current_path.set("")

    def cancel(self):
        self.controller.cancel()
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelling…")

    def _tick(self):
        self._timer = None
        snapshot = self.controller.snapshot()
        if snapshot and snapshot.run_id == self.run_id:
            self.results.refresh()
            progress = snapshot.progress
            state = "Scanning"
            if snapshot.finished:
                self.cancel_button.configure(state="disabled")
                state = "Failed" if snapshot.error else "Cancelled" if snapshot.summary.cancelled else "Completed"
            self.status.set(f"{state} | scanned {progress.files_scanned:,} files / {progress.directories_scanned:,} folders"
                            f" | matches {progress.files_matched:,} | errors {progress.errors:,}"
                            f" | {snapshot.elapsed_seconds:.1f} s")
            self.current_path.set(snapshot.error or progress.current_directory)
        if not self._closed:
            self._timer = self.root.after(self.update_ms, self._tick)

    def close(self):
        self._closed = True
        self.controller.close()
        for timer in (self._timer, self._discover_timer):
            if timer:
                self.root.after_cancel(timer)
        self.root.destroy()
