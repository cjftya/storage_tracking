"""Interleave baseline/current file scans against one unchanged owned fixture."""
import argparse
import io
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.files < 1 or args.repeat < 1:
        parser.error("--files and --repeat must be positive")
    project = Path.cwd()
    archive = subprocess.check_output(["git", "archive", args.baseline_ref])
    output = {"baseline_ref": args.baseline_ref, "repeat": args.repeat, "files": args.files, "scenarios": {}}
    with tempfile.TemporaryDirectory(prefix="shared-comparison-", dir=project) as directory:
        baseline = Path(directory) / "baseline"
        baseline.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            handle.extractall(baseline, filter="data")
        for scenario in ("empty_directories", "no_matches", "matches"):
            root = Path(directory) / scenario
            root.mkdir()
            for i in range(3000 if scenario == "empty_directories" else args.files):
                if scenario == "empty_directories":
                    (root / str(i)).mkdir()
                else:
                    (root / str(i)).write_bytes(b"x")
            samples = {"baseline": {"engine": [], "store": []}, "current": {"engine": [], "store": []}}
            for iteration in range(args.repeat):
                versions = (("baseline", baseline), ("current", project))
                # Alternate which version sees the freshly warmed fixture first.
                for version, source in versions if iteration % 2 == 0 else reversed(versions):
                    for mode in ("engine", "store"):
                        command = [sys.executable, "-m", "storage_scanner.core.benchmark", str(root),
                                   "--min-size-mib", "0.0000019073486328125" if scenario == "no_matches" else "0"]
                        if mode == "store":
                            command.append("--store")
                        child = subprocess.run(command, cwd=source, capture_output=True, text=True, check=True)
                        samples[version][mode].append(json.loads(child.stdout))
            medians = {version: {mode: statistics.median(s["elapsed_seconds"] for s in rows)
                                 for mode, rows in data.items()} for version, data in samples.items()}
            output["scenarios"][scenario] = {"medians_seconds": medians, "samples": samples}
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["medians_seconds"] for k, v in output["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    main()
