"""Folder engine/index/GUI and memory scaling on owned temporary fixtures."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile

from benchmarks.run_scenarios import gui_measure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gui-child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.gui_child:
        return gui_measure(args.gui_child, 0, "folder")
    if args.files < 1 or args.repeat < 1:
        parser.error("--files and --repeat must be positive")
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "files": args.files, "repeat": args.repeat, "scenarios": {}}
    with tempfile.TemporaryDirectory(prefix="folder-benchmark-", dir=Path.cwd()) as directory:
        for name, count, folders in (("few_folders_small", args.files // 4, 4),
                                     ("few_folders", args.files, 4),
                                     ("many_folders", args.files, min(args.files, 4000))):
            root = Path(directory) / name
            root.mkdir()
            for i in range(folders):
                (root / str(i)).mkdir()
            for i in range(count):
                (root / str(i % folders) / str(i)).write_bytes(b"x")
            rows = []
            for _ in range(args.repeat):
                row = {}
                for mode in ("engine", "store", "gui"):
                    command = [sys.executable, "-m", "storage_scanner.folder.benchmark", str(root)]
                    if mode == "store":
                        command.append("--store")
                    elif mode == "gui":
                        command = [sys.executable, "-m", "benchmarks.folder_scenarios", "--gui-child", str(root)]
                    result = subprocess.run(command, capture_output=True, text=True, check=True)
                    row[mode] = json.loads(result.stdout)
                # Separate allocation measurement: never mix tracemalloc timing
                # with the normal engine timings.
                result = subprocess.run([sys.executable, "-m", "storage_scanner.folder.benchmark",
                                         str(root), "--store", "--track-python-memory"],
                                        capture_output=True, text=True, check=True)
                row["memory"] = json.loads(result.stdout)
                rows.append(row)
            medians = {mode: {key: statistics.median(row[mode][key] for row in rows)
                              for key, value in rows[0][mode].items()
                              if isinstance(value, (int, float)) and not isinstance(value, bool)}
                       for mode in rows[0]}
            report["scenarios"][name] = {"samples": rows, "medians": medians}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
