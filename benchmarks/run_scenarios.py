"""Compare traversal, indexing, and optional Tk presentation in fresh processes."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from storage_scanner.core.filters import bytes_to_mib


def gui_measure(path, minimum):
    import tkinter as tk
    from storage_scanner.gui.app import ScannerApp
    from storage_scanner.platform_fs.metrics import peak_memory_bytes

    root = tk.Tk()
    settings = {"roots": (path,), "min_size_bytes": minimum, "extra_exclusions": (),
                "include_removable": False, "use_default_exclusions": True, "update_ms": 700}
    app = ScannerApp(root, settings)
    output = {}
    started = None
    deadline = time.perf_counter() + 120
    last_heartbeat = time.perf_counter()
    max_heartbeat_gap = 0.0
    refresh_seconds = 0.0
    refresh_count = 0
    actual_refresh = app.results.refresh

    def measured_refresh():
        nonlocal refresh_seconds, refresh_count
        tick = time.perf_counter()
        result = actual_refresh()
        refresh_seconds += time.perf_counter() - tick
        refresh_count += 1
        return result

    app.results.refresh = measured_refresh

    def poll():
        nonlocal started, last_heartbeat, max_heartbeat_gap
        now = time.perf_counter()
        max_heartbeat_gap = max(max_heartbeat_gap, now - last_heartbeat)
        last_heartbeat = now
        if now > deadline:
            output["error"] = "GUI benchmark timed out"
            app.close()
            return
        if app.discovery is not None and started is None:
            started = time.perf_counter()
            app.start()
        snapshot = app.controller.snapshot()
        if snapshot:
            # Expand only the matched root. Descendants retain normal lazy loading.
            for path, item in tuple(app.results._items.items()):
                if path is not None and path in snapshot.store._roots:
                    if not app.results.tree.item(item, "open"):
                        app.results.tree.item(item, open=True)
                        app.results.tree.focus(item)
                        app.results._open()
            if snapshot.finished and not app.results._pending and refresh_count:
                output.update(asdict(snapshot.summary) if snapshot.summary else {})
                output.update(gui_wall_seconds=time.perf_counter() - started,
                              tree_refresh_seconds=refresh_seconds,
                              tree_refresh_count=refresh_count,
                              max_heartbeat_gap_seconds=max_heartbeat_gap,
                              stored_matches=snapshot.store.match_count,
                              rendered_nodes=len(app.results._paths),
                              peak_process_memory_bytes=peak_memory_bytes(), error=snapshot.error)
                app.close()
                return
        root.after(10, poll)

    root.after(10, poll)
    root.mainloop()
    print(json.dumps(output, indent=2))
    return 1 if output.get("error") else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=3000)
    parser.add_argument("--gui", action="store_true", help="Open and automatically close a Tk window per scenario")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture-parent", type=Path,
                        help="Parent for the owned temporary fixture; choose a selected local volume")
    parser.add_argument("--gui-child", nargs=2, metavar=("ROOT", "MIN_BYTES"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.gui_child:
        return gui_measure(args.gui_child[0], int(args.gui_child[1]))
    if args.entries < 1:
        parser.error("--entries must be positive")
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "entries": args.entries, "storage_medium": "unverified", "scenarios": {}}
    # Only this owned temporary fixture is created and removed.
    with tempfile.TemporaryDirectory(prefix="storage-scanner-benchmark-", dir=args.fixture_parent) as directory:
        for name in ("empty_directories", "many_files_no_matches", "many_matches"):
            folder = Path(directory) / name
            folder.mkdir()
            for index in range(args.entries):
                if name == "empty_directories":
                    (folder / str(index)).mkdir()
                else:
                    with (folder / f"{index}.bin").open("wb") as handle:
                        handle.truncate(1)
            minimum = 2 if name == "many_files_no_matches" else 1
            modes = {}
            for mode in ("engine", "store"):
                command = [sys.executable, "-m", "storage_scanner.core.benchmark", str(folder),
                           "--min-size-mib", bytes_to_mib(minimum)]
                if mode == "store":
                    command.append("--store")
                child = subprocess.run(command, check=True, capture_output=True, text=True)
                modes[mode] = json.loads(child.stdout)
            if args.gui:
                child = subprocess.run([sys.executable, "-m", "benchmarks.run_scenarios",
                                        "--gui-child", str(folder), str(minimum)],
                                       check=True, capture_output=True, text=True)
                modes["gui"] = json.loads(child.stdout)
            report["scenarios"][name] = modes
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
