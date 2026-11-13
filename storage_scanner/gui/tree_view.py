"""Render completed results on demand, with bounded work and folder paging."""
from collections import deque
import time
from tkinter import Menu, ttk

from storage_scanner.core.results import ResultStore


def display_size(size):
    if size < 1024:
        return f"{size:,} B"
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        size /= 1024
        if size < 1024 or unit == "PiB":
            return f"{size:,.2f} {unit}"


class ResultTree(ttk.Frame):
    PAGE_SIZE = 200
    INSERT_BUDGET = 150
    TIME_BUDGET = 0.012

    def __init__(self, parent, on_open_folder=None):
        super().__init__(parent)
        self.on_open_folder = on_open_folder
        self._render_timer = None
        self._context_path = None
        self.menu = Menu(self, tearoff=False)
        self.menu.add_command(label="열기", command=self._open_context_folder)
        self.tree = ttk.Treeview(self, columns=("size", "bytes"), selectmode="browse")
        self.tree.heading("#0", text="Path / file")
        self.tree.heading("size", text="용량 (검색 결과 합계)")
        self.tree.heading("bytes", text="Bytes")
        self.tree.column("#0", width=580, minwidth=200)
        self.tree.column("size", width=180, anchor="e", stretch=False)
        self.tree.column("bytes", width=150, anchor="e", stretch=False)
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewOpen>>", self._open)
        self.tree.bind("<Double-1>", self._more)
        self.tree.bind("<Return>", self._more)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Shift-F10>", self._show_context_menu)
        self.reset(None)

    def reset(self, store: ResultStore | None):
        if self._render_timer is not None:
            self.after_cancel(self._render_timer)
            self._render_timer = None
        self.menu.unpost()
        self._context_path = None
        self.store = store
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._items = {None: ""}
        self._paths = {}
        self._directories = set()
        self._offsets = {None: 0}
        self._limits = {None: self.PAGE_SIZE}
        self._pending = deque([None])
        self._queued = {None}
        self._more_items = {}
        if store is not None:
            self._schedule_render()

    def _schedule_render(self):
        if self._render_timer is None and self._pending:
            self._render_timer = self.after_idle(self._render_pending)

    def _render_pending(self):
        self._render_timer = None
        self.refresh()
        self._schedule_render()

    def destroy(self):
        if self._render_timer is not None:
            self.after_cancel(self._render_timer)
            self._render_timer = None
        super().destroy()

    def _show_context_menu(self, event):
        if event.type.name == "KeyPress":
            item = self.tree.focus()
            bounds = self.tree.bbox(item) if item else ()
            if not bounds:
                return
            x = self.tree.winfo_rootx() + bounds[0] + 20
            y = self.tree.winfo_rooty() + bounds[1] + bounds[3]
        else:
            item = self.tree.identify_row(event.y)
            x, y = event.x_root, event.y_root
        path = self._paths.get(item)
        if path not in self._directories or self.on_open_folder is None:
            self._context_path = None
            self.menu.unpost()
            return
        self.tree.selection_set(item)
        self.tree.focus(item)
        self._context_path = path
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()
        return "break"

    def _open_context_folder(self):
        if self._context_path in self._directories and self.on_open_folder is not None:
            self.on_open_folder(self._context_path)

    def _enqueue(self, path):
        if path not in self._queued:
            self._pending.append(path)
            self._queued.add(path)
        self._schedule_render()

    def _open(self, event=None):
        item = self.tree.focus()
        if item in self._paths:
            self._enqueue(self._paths[item])

    def _more(self, event=None):
        item = self.tree.identify_row(event.y) if event and event.type.name != "KeyPress" else self.tree.focus()
        if item in self._more_items:
            path = self._more_items.pop(item)
            self.tree.delete(item)
            self._limits[path] += self.PAGE_SIZE
            self._enqueue(path)
            return "break"

    def refresh(self):
        if self.store is None:
            return 0
        deadline = time.perf_counter() + self.TIME_BUDGET
        inserted = 0
        # The store is attached only after collection finishes. No polling for
        # changes or rewriting already displayed rows is needed.
        while self._pending and inserted < self.INSERT_BUDGET and time.perf_counter() < deadline:
            path = self._pending.popleft()
            self._queued.discard(path)
            parent = self._items[path]
            if path is not None and not self.tree.item(parent, "open"):
                continue
            offset, limit = self._offsets[path], self._limits[path]
            total = self.store.child_count(path)
            amount = min(self.INSERT_BUDGET - inserted, limit - offset, total - offset, 32)
            if amount > 0:
                dummy = parent + ":placeholder"
                if self.tree.exists(dummy):
                    self.tree.delete(dummy)
                for node in self.store.children(path, offset, amount):
                    values = (display_size(node.size_bytes), f"{node.size_bytes:,}")
                    item = self.tree.insert(parent, "end", text=node.name, values=values)
                    self._items[node.path] = item
                    self._paths[item] = node.path
                    if node.is_directory:
                        self._directories.add(node.path)
                        self._offsets[node.path] = 0
                        self._limits[node.path] = self.PAGE_SIZE
                        self.tree.insert(item, "end", iid=item + ":placeholder", text="Expand to load matches")
                    inserted += 1
                self._offsets[path] += amount
            offset = self._offsets[path]
            if offset < min(total, limit):
                self._enqueue(path)
            elif total > offset:
                more_id = parent + ":more"
                if not self.tree.exists(more_id):
                    self.tree.insert(parent, "end", iid=more_id,
                                     text="Load next 200… (double-click or Enter)")
                    self._more_items[more_id] = path
        return inserted
