"""Single-worker iterative traversal. No queues, GUI objects, or file reads."""
import os
import stat
import time
from collections.abc import Callable

from storage_scanner.core.filters import PathFilter
from storage_scanner.core.models import FileMatch, ScanConfig, ScanProgress, ScanSummary
from storage_scanner.platform_fs.paths import directory_is_link, path_key, root_metadata, entry_path_key


def scan(config: ScanConfig, on_batch: Callable[[tuple[FileMatch, ...]], None],
         on_progress: Callable[[ScanProgress], None] = lambda p: None,
         should_cancel: Callable[[], bool] = lambda: False) -> ScanSummary:
    started = time.perf_counter()
    last_flush = last_progress = started
    files = directories = matched = errors = 0
    current = ""
    batch = []
    cancelled = False
    excluded = PathFilter(config.excluded_paths)
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
            on_progress(ScanProgress(files, directories, matched, errors, current))
            last_progress = now

    for root in roots:
        if should_cancel():
            cancelled = True
            break
        if excluded.excludes(root):
            continue
        try:
            info = root_metadata(root)
            if info is None:
                continue
        except OSError:
            errors += 1
            continue
        if stat.S_ISREG(info.st_mode):
            # Linux bind mounts can expose a single file, not just a directory.
            current = os.path.dirname(root)
            files += 1
            if info.st_size >= config.min_size_bytes:
                matched += 1
                batch.append(FileMatch(root, info.st_size))
            publish(time.perf_counter())
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        stack = [root]
        while stack:
            if should_cancel():
                cancelled = True
                break
            current = stack.pop()
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
                                files += 1
                                if info.st_size >= config.min_size_bytes and not excluded.excludes_file_entry(entry.path):
                                    matched += 1
                                    batch.append(FileMatch(entry.path, info.st_size))
                                    if len(batch) >= config.batch_size:
                                        publish(time.perf_counter())
                        except OSError:
                            errors += 1
                        if index % config.cancellation_check_interval == 0:
                            publish(time.perf_counter())
                            if should_cancel():
                                cancelled = True
                                break
            except OSError:
                errors += 1
            publish(time.perf_counter())
            if cancelled:
                break
        if cancelled:
            break
    publish(time.perf_counter(), force=True)
    return ScanSummary(files, directories, matched, errors,
                       time.perf_counter() - started, cancelled)
