from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FolderConfig:
    roots: tuple[str, ...]
    excluded_paths: tuple[str, ...] = ()
    boundary_paths: tuple[str, ...] = ()
    batch_size: int = 512
    flush_interval: float = 0.25
    progress_interval: float = 0.2
    cancellation_check_interval: int = 128

    def __post_init__(self):
        if not 256 <= self.batch_size <= 1024:
            raise ValueError("Batch size must be between 256 and 1024")
        if self.flush_interval <= 0 or self.progress_interval <= 0:
            raise ValueError("Update intervals must be positive")
        if self.cancellation_check_interval < 1:
            raise ValueError("Cancellation interval must be positive")


@dataclass(frozen=True, slots=True)
class DirectoryDelta:
    path: str
    direct_bytes: int


@dataclass(frozen=True, slots=True)
class FolderProgress:
    files_scanned: int = 0
    directories_scanned: int = 0
    bytes_scanned: int = 0
    errors: int = 0
    current_directory: str = ""


@dataclass(frozen=True, slots=True)
class FolderSummary:
    files_scanned: int
    directories_scanned: int
    bytes_scanned: int
    errors: int
    elapsed_seconds: float
    cancelled: bool
