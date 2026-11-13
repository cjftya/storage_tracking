"""Bounded incremental Treeview rendering, with explicit per-folder paging."""
from collections import deque
import time
from tkinter import ttk

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

    def __init__(self, parent):
        super().__init__(parent)
        self.tree = ttk.Treeview(self, columns=("size", "bytes"), selectmode="browse")
        self.tree.heading("#0", text="Path / file")
        self.tree.heading("size", text="Size")
        self.tree.heading("bytes", text="Bytes")
        self.tree.column("#0", width=580, minwidth=200)
        self.tree.column("size", width=120, anchor="e", stretch=False)
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
        self.reset(None)

    def reset(self, store: ResultStore | None):
        self.store = store
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._items = {None: ""}
        self._paths = {}
        self._offsets = {None: 0}
        self._limits = {None: self.PAGE_SIZE}
        self._pending = deque([None])
        self._queued = {None}
        self._more_items = {}

    def _enqueue(self, path):
        if path not in self._queued:
            self._pending.append(path)
            self._queued.add(path)

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
        for path in self.store.take_changes():
            if path in self._items:
                self._enqueue(path)
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
                    values = ("", "") if node.is_directory else (display_size(node.size_bytes), f"{node.size_bytes:,}")
                    item = self.tree.insert(parent, "end", text=node.name, values=values)
                    self._items[node.path] = item
                    self._paths[item] = node.path
                    if node.is_directory:
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
