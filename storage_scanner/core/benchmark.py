"""Headless baseline; use --store to separately measure result indexing cost."""
from dataclasses import asdict
import json
import platform
import signal
from threading import Event
import tracemalloc

from storage_scanner.core.results import MemoryResultStore
from storage_scanner.core.scanner import scan
from storage_scanner.options import argument_parser, configured_scan, load_settings
from storage_scanner.platform_fs.metrics import peak_memory_bytes


def main(argv=None):
    parser = argument_parser("Headless Storage Scanner benchmark")
    parser.add_argument("--store", action="store_true", help="Include in-memory result index cost")
    parser.add_argument("--track-python-memory", action="store_true", help="Enable tracemalloc (slows timing)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args)
        _, config = configured_scan(settings)
        stop = Event()
        previous = signal.signal(signal.SIGINT, lambda signum, frame: stop.set())
        store = MemoryResultStore(config.roots) if args.store else None
        if args.track_python_memory:
            tracemalloc.start()
        try:
            summary = scan(config, store.add_batch if store else lambda batch: None,
                           should_cancel=stop.is_set)
        finally:
            signal.signal(signal.SIGINT, previous)
        output = asdict(summary)
        output.update(roots=config.roots, excluded_paths=config.excluded_paths,
                      min_size_bytes=config.min_size_bytes, platform=platform.platform(),
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
