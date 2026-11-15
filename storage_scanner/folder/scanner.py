"""Iterative directory aggregation. Emits direct-byte deltas, never file results."""
import os
import stat
import time
from collections.abc import Callable

from storage_scanner.platform_fs.filters import PathFilter
from storage_scanner.folder.models import DirectoryDelta, FolderConfig, FolderProgress, FolderSummary
from storage_scanner.platform_fs.paths import directory_is_link, path_key, validate_scan_roots, entry_path_key


def scan(config: FolderConfig, on_batch: Callable[[tuple[DirectoryDelta, ...]], None],
         on_progress: Callable[[FolderProgress], None] = lambda p: None,
         should_cancel: Callable[[], bool] = lambda: False) -> FolderSummary:
    started = time.perf_counter()
    last_flush = last_progress = started
    files = directories = total_bytes = errors = 0
    current = ""
    batch = []
    cancelled = False
    excluded = PathFilter(config.excluded_paths, allowed_paths=config.allowed_paths)
    roots = tuple(dict.fromkeys(path_key(p) for p in config.roots))
    boundaries = set(path_key(p) for p in config.boundary_paths) | set(roots)
    boundary_names = {}
    for boundary in boundaries:
        boundary_names.setdefault(os.path.dirname(boundary), set()).add(os.path.basename(boundary))

    def publish(now, force=False):
        nonlocal last_flush, last_progress
        if batch and (force or len(batch) >= config.batch_size or
                      now - last_flush >= config.flush_interval):
            on_batch(tuple(batch))
            batch.clear()
            last_flush = now
        if force or now - last_progress >= config.progress_interval:
            on_progress(FolderProgress(files, directories, total_bytes, errors, current))
            last_progress = now

    inspected = validate_scan_roots(roots, excluded, directories_only=True)
    for root, info in inspected:
        if should_cancel():
            cancelled = True
            break
        stack = [root]
        while stack:
            if should_cancel():
                cancelled = True
                break
            current = stack.pop()
            direct_bytes = 0
            mounted_children = boundary_names.get(entry_path_key(current), ())
            try:
                with os.scandir(current) as entries:
                    directories += 1
                    for index, entry in enumerate(entries, 1):
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if not directory_is_link(entry):
                                    child = entry.path
                                    child_key = entry_path_key(child)
                                    if child_key not in boundaries and not excluded.excludes_key(child_key):
                                        stack.append(child)
                            elif entry.is_file(follow_symlinks=False) and not (
                                    mounted_children and entry_path_key(entry.name) in mounted_children):
                                info = entry.stat(follow_symlinks=False)
                                if stat.S_ISREG(info.st_mode):
                                    files += 1
                                    if not excluded.excludes_file_entry(entry.path):
                                        total_bytes += info.st_size
                                        direct_bytes += info.st_size
                                else:
                                    errors += 1  # Entry changed type after enumeration.
                        except OSError:
                            errors += 1
                        if index % config.cancellation_check_interval == 0:
                            if direct_bytes:
                                batch.append(DirectoryDelta(current, direct_bytes))
                                direct_bytes = 0
                            publish(time.perf_counter())
                            if should_cancel():
                                cancelled = True
                                break
            except OSError:
                errors += 1
            if direct_bytes:
                batch.append(DirectoryDelta(current, direct_bytes))
            publish(time.perf_counter())
            if cancelled:
                break
        if cancelled:
            break
    publish(time.perf_counter(), force=True)
    return FolderSummary(files, directories, total_bytes, errors,
                       time.perf_counter() - started, cancelled)
