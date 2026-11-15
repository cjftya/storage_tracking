"""Shared exclusion policy for metadata traversals."""
from storage_scanner.platform_fs.paths import contains_path, path_key, entry_path_key


class PathFilter:
    def __init__(self, excluded_paths, *, windows=None, allowed_paths=()):
        self.allowed = tuple(path_key(p, windows=windows) for p in allowed_paths)
        self.windows = windows
        self.excluded = tuple(dict.fromkeys(
            path_key(p, windows=windows) for p in excluded_paths))
        self._exact = frozenset(self.excluded)

    def excludes(self, path: str) -> bool:
        if not self.excluded:
            return False
        candidate = path_key(path, windows=self.windows)
        return self.excludes_key(candidate)

    def excludes_key(self, candidate: str) -> bool:
        if not self.allowed:
            return any(contains_path(p, candidate, windows=self.windows)
                       for p in self.excluded)
        return any(contains_path(p, candidate, windows=self.windows) and not any(
            p == "/run" and p != allowed and contains_path(p, allowed, windows=self.windows) and
            contains_path(allowed, candidate, windows=self.windows)
            for allowed in self.allowed) for p in self.excluded)

    def excludes_file_entry(self, path: str) -> bool:
        # Ancestors were checked before entering this directory; only exact
        # excluded file paths remain. No per-match normalization or prefix loop.
        return entry_path_key(path) in self._exact
