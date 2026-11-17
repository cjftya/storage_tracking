"""Folder-only RAM index. Every direct-byte delta reaches each ancestor once."""
from dataclasses import dataclass, replace
import os
from threading import RLock

from storage_scanner.platform_fs.paths import contains_path, path_key


@dataclass(frozen=True, slots=True)
class FolderNode:
    path: str
    name: str
    size_bytes: int = 0
    direct_bytes: int = 0
    is_directory: bool = True


class FolderResultStore:
    def __init__(self, roots):
        self._roots = tuple(sorted(set(path_key(p) for p in roots), key=len, reverse=True))
        self._nodes = {}
        self._children = {None: []}
        self._parents = {}
        self._changes = set()
        self._lock = RLock()
        self.state = "scanning"
        self.finalized = False
        for root in self._roots:
            self._ensure(root, root)

    @property
    def folder_count(self):
        with self._lock:
            return len(self._nodes)

    def _ensure(self, path, root):
        missing = []
        current = path
        while current not in self._nodes:
            missing.append(current)
            if current == root:
                break
            current = os.path.dirname(current)
        for current in reversed(missing):
            parent = None if current == root else os.path.dirname(current)
            self._nodes[current] = FolderNode(current, current if parent is None else os.path.basename(current))
            self._children[current] = []
            self._parents[current] = parent
            self._children[parent].append(current)
            self._changes.add(parent)

    def add_batch(self, deltas):
        direct = {}
        roots = {}
        for delta in deltas:
            key = path_key(delta.path)
            root = next((r for r in self._roots if contains_path(r, key)), None)
            if root is None or delta.direct_bytes < 0:
                raise ValueError("Invalid directory delta")
            if not delta.direct_bytes:
                continue
            direct[key] = direct.get(key, 0) + delta.direct_bytes
            roots[key] = root
        with self._lock:
            if self.finalized:
                raise RuntimeError("Folder results are finalized")
            affected = set()
            for key, root in roots.items():
                self._ensure(key, root)
                while key is not None and key not in affected:
                    affected.add(key)
                    key = self._parents[key]
            totals = dict(direct)
            for key in sorted(affected, key=len, reverse=True):
                value = totals[key]
                node = self._nodes[key]
                self._nodes[key] = replace(node, size_bytes=node.size_bytes + value,
                                           direct_bytes=node.direct_bytes + direct.get(key, 0))
                self._changes.add(key)
                parent = self._parents[key]
                if parent is not None:
                    totals[parent] = totals.get(parent, 0) + value

    def finish(self, *, complete, should_stop=lambda: False):
        # Collector is the sole writer. Sort each child index outside the lock;
        # readers copy at most a page, and never wait on a whole-tree sort.
        for parent, keys in self._children.items():
            if should_stop():
                return
            ordered = sorted(keys, key=lambda key: (-self._nodes[key].size_bytes, key))
            with self._lock:
                self._children[parent] = ordered
                self._changes.add(parent)
        with self._lock:
            self.state = "complete" if complete else "partial"
            self.finalized = True

    def node(self, path):
        with self._lock:
            return self._nodes.get(path)

    def children_page(self, parent, offset=0, limit=200):
        if offset < 0 or limit < 0:
            raise ValueError("Page offset and limit must be nonnegative")
        with self._lock:
            keys = self._children.get(parent, ())
            return len(keys), tuple(self._nodes[key] for key in keys[offset:offset + limit])

    def children(self, parent, offset=0, limit=200):
        return self.children_page(parent, offset, limit)[1]

    def child_count(self, parent):
        with self._lock:
            return len(self._children.get(parent, ()))

    def take_changes(self, limit=100):
        with self._lock:
            return tuple(self._changes.pop() for _ in range(min(limit, len(self._changes))))
