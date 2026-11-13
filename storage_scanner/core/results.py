"""Thread-safe append-only result index; never builds unmatched folders."""
from dataclasses import dataclass
import os
from threading import RLock
from typing import Protocol

from storage_scanner.core.models import FileMatch
from storage_scanner.platform_fs.paths import contains_path, path_key


@dataclass(frozen=True, slots=True)
class ResultNode:
    path: str
    name: str
    is_directory: bool
    size_bytes: int = 0


class ResultStore(Protocol):
    def add_batch(self, matches: tuple[FileMatch, ...]) -> None: ...
    def children(self, parent: str | None, offset: int = 0, limit: int = 200) -> tuple[ResultNode, ...]: ...
    def child_count(self, parent: str | None) -> int: ...
    def take_changes(self, limit: int = 100) -> tuple[str | None, ...]: ...


class MemoryResultStore:
    def __init__(self, roots):
        self._roots = tuple(sorted(set(path_key(p) for p in roots), key=len, reverse=True))
        self._nodes: dict[str, ResultNode] = {}
        self._children: dict[str | None, list[str]] = {None: []}
        self._changes: set[str | None] = set()
        self._count = 0
        self._lock = RLock()

    @property
    def match_count(self):
        with self._lock:
            return self._count

    def add_batch(self, matches):
        # Release the lock regularly so a GUI page read cannot wait for a huge batch.
        for offset in range(0, len(matches), 64):
            with self._lock:
                for match in matches[offset:offset + 64]:
                    key = path_key(match.path)
                    if key in self._nodes:
                        continue
                    if key in self._roots:
                        # A file bind mount is a leaf root, with no synthetic folder.
                        self._nodes[key] = ResultNode(key, match.path, False, match.size_bytes)
                        self._children[None].append(key)
                        self._changes.add(None)
                        self._count += 1
                        continue
                    parent = os.path.dirname(key)
                    if parent not in self._nodes:
                        root = next((r for r in self._roots if contains_path(r, key)), None)
                        if root is None:
                            raise ValueError(f"Result outside configured roots: {match.path}")
                        missing = []
                        folder = parent
                        display_folder = os.path.dirname(match.path)
                        while folder not in self._nodes:
                            missing.append((folder, os.path.basename(display_folder)))
                            if folder == root:
                                break
                            folder = os.path.dirname(folder)
                            display_folder = os.path.dirname(display_folder)
                        for folder, display_name in reversed(missing):
                            ancestor = None if folder == root else os.path.dirname(folder)
                            name = folder if ancestor is None else display_name
                            self._nodes[folder] = ResultNode(folder, name, True)
                            self._children[folder] = []
                            self._children[ancestor].append(folder)
                            self._changes.add(ancestor)
                    self._nodes[key] = ResultNode(key, os.path.basename(match.path), False, match.size_bytes)
                    self._children[parent].append(key)
                    self._changes.add(parent)
                    self._count += 1

    def children(self, parent, offset=0, limit=200):
        if offset < 0 or limit < 0:
            raise ValueError("Page offset and limit must be nonnegative")
        with self._lock:
            return tuple(self._nodes[key] for key in self._children.get(parent, ())[offset:offset + limit])

    def child_count(self, parent):
        with self._lock:
            return len(self._children.get(parent, ()))

    def take_changes(self, limit=100):
        with self._lock:
            return tuple(self._changes.pop() for _ in range(min(limit, len(self._changes))))
