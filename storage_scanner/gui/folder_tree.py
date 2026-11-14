"""Render live append-only results with bounded work and folder paging."""
from collections import deque
import time
from tkinter import Menu, ttk

from storage_scanner.folder.results import FolderResultStore


from storage_scanner.gui.formatting import display_size, ROW_COLORS, ROW_TAGS


class FolderTree(ttk.Frame):
    PAGE_SIZE = 200
    INSERT_BUDGET = 150
    TIME_BUDGET = 0.012
    ROW_COLORS = ROW_COLORS
    ROW_TAGS = ROW_TAGS

    def __init__(self, parent, on_open_folder=None):
        super().__init__(parent)
        self.on_open_folder = on_open_folder
        self._render_timer = None
        self._cleanup_timer = None
        self._stripe_timer = None
        self._generation = 0
        self._retired = deque()
        self._paths = {}
        self._context_path = None
        self.menu = Menu(self, tearoff=False)
        self.menu.add_command(label="Open folder", command=self._open_context_folder)
        ttk.Style(self).configure("Results.Treeview", rowheight=24, foreground="#202020",
                                  background=self.ROW_COLORS[0], fieldbackground=self.ROW_COLORS[0])
        self.vertical = ttk.Scrollbar(self, orient="vertical")
        self.horizontal = ttk.Scrollbar(self, orient="horizontal")
        self.vertical.grid(row=0, column=1, sticky="ns")
        self.horizontal.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree = None
        self.reset(None)

    def _create_tree(self):
        self.tree = ttk.Treeview(self, columns=("size", "bytes"), selectmode="browse", style="Results.Treeview")
        self.tree.heading("#0", text="Name / Path")
        self.tree.heading("size", text="Logical size (aggregating)")
        self.tree.heading("bytes", text="Bytes")
        self.tree.column("#0", width=580, minwidth=200)
        self.tree.column("size", width=180, anchor="e", stretch=False)
        self.tree.column("bytes", width=150, anchor="e", stretch=False)
        self.vertical.configure(command=self.tree.yview)
        self.horizontal.configure(command=self.tree.xview)
        generation = self._generation
        self.tree.configure(yscrollcommand=lambda first, last: self._on_scroll(generation, first, last),
                            xscrollcommand=self.horizontal.set)
        for tag, color in zip(self.ROW_TAGS, self.ROW_COLORS):
            self.tree.tag_configure(tag, background=color)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewOpen>>", self._open)
        self.tree.bind("<<TreeviewClose>>", self._schedule_striping)
        self.tree.bind("<Configure>", self._schedule_striping)
        self.tree.bind("<Map>", self._schedule_striping)
        self.tree.bind("<Double-1>", self._more)
        self.tree.bind("<Return>", self._more)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Shift-F10>", self._show_context_menu)

    def reset(self, store: FolderResultStore | None, run_id=None):
        self._generation += 1
        self._sorted = False
        self._sort_paths = None
        self.run_id = run_id
        if self._render_timer is not None:
            self.after_cancel(self._render_timer)
            self._render_timer = None
        if self._stripe_timer is not None:
            self.after_cancel(self._stripe_timer)
            self._stripe_timer = None
        self.menu.unpost()
        self._context_path = None
        self.store = store
        if self.tree is not None:
            self.tree.grid_remove()
            self.tree.configure(yscrollcommand="", xscrollcommand="")
            # Parents are always inserted before descendants. Reverse insertion
            # order makes every delete a leaf (plus at most two sentinel rows).
            # Keep the old mapping alive through its iterator, without copying it.
            self._retired.append((self.tree, reversed(self._paths)))
            if self._cleanup_timer is None:
                self._cleanup_timer = self.after(1, self._cleanup_retired)
        self._create_tree()
        self._items = {None: ""}
        self._paths = {}
        self._directories = set()
        self._offsets = {None: 0}
        self._limits = {None: self.PAGE_SIZE}
        self._pending = deque([None])
        self._queued = {None}
        self._more_items = {}
        self._dirty = deque()
        self._poll_changes = False
        if store is not None:
            self._schedule_render()

    def _cleanup_retired(self):
        self._cleanup_timer = None
        deadline = time.perf_counter() + self.TIME_BUDGET
        work = 0
        while self._retired and work < self.INSERT_BUDGET and time.perf_counter() < deadline:
            tree, items = self._retired[0]
            item = next(items, None)
            if item is None:
                tree.destroy()  # Empty now; no recursive subtree destruction.
                self._retired.popleft()
            else:
                tree.delete(item)
            work += 1
        if self._retired:
            self._cleanup_timer = self.after(1, self._cleanup_retired)

    def poll_changes(self):
        """Called by the app's status timer, never by the collector thread."""
        if self.store is not None:
            self._poll_changes = True
            self._schedule_render()

    def _schedule_render(self):
        if self._render_timer is None and (self._pending or self._dirty or self._poll_changes):
            self._render_timer = self.after(1, self._render_pending, self._generation)

    def _render_pending(self, generation):
        if generation != self._generation:
            return
        self._render_timer = None
        self.refresh()
        self._schedule_render()

    def destroy(self):
        self._generation += 1
        if self._render_timer is not None:
            self.after_cancel(self._render_timer)
            self._render_timer = None
        if self._cleanup_timer is not None:
            self.after_cancel(self._cleanup_timer)
            self._cleanup_timer = None
        if self._stripe_timer is not None:
            self.after_cancel(self._stripe_timer)
            self._stripe_timer = None
        super().destroy()

    def _on_scroll(self, generation, first, last):
        if generation != self._generation:
            return
        self.vertical.set(first, last)
        self._schedule_striping()

    def _schedule_striping(self, event=None):
        if event is not None and event.widget is not self.tree:
            return
        if self._stripe_timer is None:
            self._stripe_timer = self.after_idle(self._stripe_visible_rows, self._generation)

    def _stripe_visible_rows(self, generation):
        if generation != self._generation:
            return
        self._stripe_timer = None
        if not self._paths or not self.tree.winfo_ismapped():
            return
        # Walk pixels in the viewport, jumping by actual row bounds. Never
        # traverse collapsed children or all previously loaded pages.
        y, height = 0, self.tree.winfo_height()
        parity = None
        while y < height:
            item = self.tree.identify_row(y)
            if not item:
                if parity is not None:
                    break
                y += 1  # Skip the heading.
                continue
            bounds = self.tree.bbox(item)
            if not bounds:
                break
            tags = self.tree.item(item, "tags")
            if parity is None:
                # Keep the first visible row's color stable during scrolling.
                parity = int(self.ROW_TAGS[1] in tags)
            new_tags = tuple(tag for tag in tags if tag not in self.ROW_TAGS) + (self.ROW_TAGS[parity],)
            if tags != new_tags:
                self.tree.item(item, tags=new_tags)
            parity = 1 - parity
            y = max(y + 1, bounds[1] + bounds[3] + 1)

    def _show_context_menu(self, event):
        if getattr(event, "widget", self.tree) is not self.tree:
            return
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
        if event is not None and getattr(event, "widget", self.tree) is not self.tree:
            return
        item = self.tree.focus()
        if item in self._paths:
            self._enqueue(self._paths[item])
            self._schedule_striping()

    def _more(self, event=None):
        if event is not None and getattr(event, "widget", self.tree) is not self.tree:
            return
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
        if self.store.finalized and not self._sorted:
            self._sorted = True
            self.tree.heading("size", text="Logical size" if self.store.state == "complete" else "Logical size (partial)")
            # Retain item identities, selections and expanded folders. Only
            # loaded ranges are revisited, within the normal render budget.
            self._sort_paths = iter(self._offsets)
        deadline = time.perf_counter() + self.TIME_BUDGET
        inserted = 0
        work = 0
        while self._sort_paths is not None and work < self.INSERT_BUDGET and time.perf_counter() < deadline:
            try:
                path = next(self._sort_paths)
            except StopIteration:
                self._sort_paths = None
                break
            self._offsets[path] = 0
            self._enqueue(path)
            work += 1
        if self._sort_paths is not None:
            return 0
        while work < self.INSERT_BUDGET and time.perf_counter() < deadline:
            if self._poll_changes and not self._dirty:
                self._dirty.extend(self.store.take_changes(32))
                if not self._dirty:
                    self._poll_changes = False
            if self._dirty:
                path = self._dirty.popleft()
                item = self._items.get(path)
                if path is not None and item is not None:
                    node = self.store.node(path)
                    values = (display_size(node.size_bytes), f"{node.size_bytes:,}")
                    if self.tree.item(item, "values") != values:
                        self.tree.item(item, values=values)
                if path is None or (item is not None and self.tree.item(item, "open")):
                    self._enqueue(path)
                work += 1
            if work >= self.INSERT_BUDGET or time.perf_counter() >= deadline:
                break
            if not self._pending:
                if self._dirty or self._poll_changes:
                    continue
                break
            path = self._pending.popleft()
            self._queued.discard(path)
            parent = self._items[path]
            work += 1
            if path is not None and not self.tree.item(parent, "open"):
                continue
            offset, limit = self._offsets[path], self._limits[path]
            amount = min(self.INSERT_BUDGET - work, limit - offset, 32)
            total, nodes = self.store.children_page(path, offset, max(0, amount))
            if nodes or total == 0:
                dummy = parent + ":placeholder"
                if self.tree.exists(dummy):
                    self.tree.delete(dummy)
            if nodes:
                for node in nodes:
                    if work >= self.INSERT_BUDGET or time.perf_counter() >= deadline:
                        break
                    work += 1
                    self._offsets[path] += 1
                    if node.path in self._items:
                        if self._sorted:
                            self.tree.move(self._items[node.path], parent, self._offsets[path] - 1)
                        continue
                    values = (display_size(node.size_bytes), f"{node.size_bytes:,}")
                    item = self.tree.insert(parent, self._offsets[path] - 1 if self._sorted else "end", text=node.name, values=values,
                                            tags=(self.ROW_TAGS[(self._offsets[path] - 1) % 2],))
                    self._items[node.path] = item
                    self._paths[item] = node.path
                    if node.is_directory:
                        self._directories.add(node.path)
                        self._offsets[node.path] = 0
                        self._limits[node.path] = self.PAGE_SIZE
                        self.tree.insert(item, "end", iid=item + ":placeholder", text="Expand to load")
                    inserted += 1
            offset = self._offsets[path]
            if offset < min(total, limit):
                self._enqueue(path)
            elif total > offset:
                more_id = parent + ":more"
                if not self.tree.exists(more_id):
                    self.tree.insert(parent, "end", iid=more_id,
                                     text="Load next 200…")
                    self._more_items[more_id] = path
                self.tree.move(more_id, parent, "end")
        if inserted:
            self._schedule_striping()
        return inserted
