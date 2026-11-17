"""Folder-only benchmark; use --store to include RAM aggregation cost."""
from dataclasses import asdict
import json
import platform
import signal
import time
from threading import Event
import tracemalloc

from storage_scanner.folder.results import FolderResultStore
from storage_scanner.folder.scanner import scan
from storage_scanner.folder.models import FolderConfig
from storage_scanner.options import argument_parser, configured_scope, load_settings
from storage_scanner.platform_fs.metrics import peak_memory_bytes


def main(argv=None):
    parser = argument_parser("Headless Storage Scanner benchmark")
    parser.add_argument("--store", action="store_true", help="Include in-memory result index cost")
    parser.add_argument("--track-python-memory", action="store_true", help="Enable tracemalloc (slows timing)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args)
        _, scope = configured_scope(settings)
        config = FolderConfig(scope.roots, scope.excluded_paths, scope.boundary_paths,
                              allowed_paths=scope.allowed_paths,
                              workers=settings["folder_workers"], large_fetch=settings["large_fetch"])
        stop = Event()
        previous = signal.signal(signal.SIGINT, lambda signum, frame: stop.set())
        store = FolderResultStore(config.roots) if args.store else None
        if args.track_python_memory:
            tracemalloc.start()
        index_seconds = 0.0
        def index(batch):
            nonlocal index_seconds
            if store is not None:
                started = time.perf_counter()
                store.add_batch(batch)
                index_seconds += time.perf_counter() - started
        try:
            summary = scan(config, index,
                           should_cancel=stop.is_set)
        finally:
            signal.signal(signal.SIGINT, previous)
        finalized = time.perf_counter()
        if store:
            store.finish(complete=not summary.cancelled and not summary.errors)
        finalize_seconds = time.perf_counter() - finalized
        output = asdict(summary)
        output.update(finalize_seconds=finalize_seconds, index_seconds=index_seconds,
                      stored_folders=store.folder_count if store else 0)
        output.update(roots=config.roots, excluded_paths=config.excluded_paths,
                      folder_workers=config.workers, large_fetch=config.large_fetch,
                      scan_mode="folder", platform=platform.platform(),
                      python=platform.python_version(), store_enabled=args.store,
                      files_per_second=summary.files_scanned / max(summary.elapsed_seconds, 1e-9),
                      peak_process_memory_bytes=peak_memory_bytes())
        if args.track_python_memory:
            output["peak_python_memory_bytes"] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        print(json.dumps(output, indent=2, ensure_ascii=True))
        return 130 if summary.cancelled else 0
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Benchmark: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
