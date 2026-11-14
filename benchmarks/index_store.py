"""Compare indexing alone using synthetic metadata, with no filesystem scan."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

from benchmarks import BASELINE_REF
from storage_scanner.core.models import FileMatch
from storage_scanner.core.results import MemoryResultStore
from storage_scanner.platform_fs.metrics import peak_memory_bytes


def measure(mode, count, baseline_ref):
    store_class = MemoryResultStore
    if mode == "baseline":
        source = subprocess.check_output(
            ["git", "show", f"{baseline_ref}:storage_scanner/core/results.py"], encoding="utf-8")
        module = types.ModuleType("baseline_results")
        sys.modules[module.__name__] = module
        exec(compile(source, "baseline:results.py", "exec"), module.__dict__)
        store_class = module.MemoryResultStore
    folder = Path.cwd() / "synthetic-index-fixture"
    matches = tuple(FileMatch(str(folder / str(i // 100) / f"{i}.bin"), i) for i in range(count))
    store = store_class((str(folder),))
    started = time.perf_counter()
    for offset in range(0, count, 512):
        store.add_batch(matches[offset:offset + 512])
    return dict(mode=mode, matches=store.match_count, index_seconds=time.perf_counter() - started,
                peak_process_memory_bytes=peak_memory_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE_REF)
    parser.add_argument("--matches", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(measure(args.child, args.matches, args.baseline_ref)))
        return
    samples = [json.loads(subprocess.check_output(
        [sys.executable, "-m", "benchmarks.index_store", "--child", mode, "--matches", str(args.matches),
         "--baseline-ref", args.baseline_ref], text=True))
        for _ in range(args.repeat) for mode in ("baseline", "current")]
    medians = {mode: {key: statistics.median(row[key] for row in samples if row["mode"] == mode)
                      for key in ("index_seconds", "peak_process_memory_bytes")}
               for mode in ("baseline", "current")}
    rendered = json.dumps(dict(samples=samples, medians=medians), indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
