from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScanConfig:
    roots: tuple[str, ...]
    min_size_bytes: int = 100 * 1024 * 1024
    excluded_paths: tuple[str, ...] = ()
    boundary_paths: tuple[str, ...] = ()
    batch_size: int = 512
    flush_interval: float = 0.25
    progress_interval: float = 0.2
    cancellation_check_interval: int = 128
    allowed_paths: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.min_size_bytes, int) or self.min_size_bytes < 0:
            raise ValueError("Minimum size must be a nonnegative integer byte count")
        if not 256 <= self.batch_size <= 1024:
            raise ValueError("Batch size must be between 256 and 1024")
        if self.flush_interval <= 0 or self.progress_interval <= 0:
            raise ValueError("Update intervals must be positive")
        if self.cancellation_check_interval < 1:
            raise ValueError("Cancellation interval must be positive")


@dataclass(frozen=True, slots=True)
class FileMatch:
    path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ScanProgress:
    files_scanned: int = 0
    directories_scanned: int = 0
    files_matched: int = 0
    errors: int = 0
    current_directory: str = ""


@dataclass(frozen=True, slots=True)
class ScanSummary:
    files_scanned: int
    directories_scanned: int
    files_matched: int
    errors: int
    elapsed_seconds: float
    cancelled: bool
