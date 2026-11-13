from decimal import Decimal, InvalidOperation

from storage_scanner.platform_fs.paths import contains_path, path_key, entry_path_key


def mib_to_bytes(value: str) -> int:
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise ValueError
        numerator, denominator = number.as_integer_ratio()
        whole, fraction = divmod(numerator * (1024 * 1024), denominator)
        if fraction:
            raise ValueError
        return whole
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("Enter a nonnegative MiB value representing whole bytes") from exc


def bytes_to_mib(value: int) -> str:
    whole, remainder = divmod(value, 1024 * 1024)
    fraction = str(remainder * 5**20).zfill(20).rstrip("0")
    return str(whole) + ("." + fraction if fraction else "")


def matches_size(size_bytes: int, minimum: int) -> bool:
    return size_bytes >= minimum


class PathFilter:
    def __init__(self, excluded_paths, *, windows=None):
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
        return any(contains_path(p, candidate, windows=self.windows)
                   for p in self.excluded)

    def excludes_file_entry(self, path: str) -> bool:
        # Ancestors were checked before entering this directory; only exact
        # excluded file paths remain. No per-match normalization or prefix loop.
        return entry_path_key(path) in self._exact
