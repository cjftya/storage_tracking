"""Repeat baseline/current scenarios sequentially, in fresh processes."""
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
    parser.add_argument("--baseline-ref", default="2b67874e30c32eb669c1ce3c8cbb0e42310d03c6")
    parser.add_argument("--entries", type=int, default=3000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    baseline_ref = subprocess.check_output(
        ["git", "rev-parse", args.baseline_ref], cwd=project, text=True).strip()
    archive = subprocess.check_output(["git", "archive", baseline_ref], cwd=project)
    samples = {"baseline": [], "current": []}
    # Extract only the tracked baseline into an owned temporary directory. Each
    # scenario creates/scans/removes its own fixture on the same local volume.
    with tempfile.TemporaryDirectory(prefix="comparison-", dir=project) as directory:
        baseline = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            handle.extractall(baseline, filter="data")
        for _ in range(args.repeat):
            for version, source in (("baseline", baseline), ("current", project)):
                child = subprocess.run(
                    [sys.executable, "-m", "benchmarks.run_scenarios", "--entries", str(args.entries),
                     "--gui", "--fixture-parent", str(project)],
                    cwd=source, capture_output=True, text=True, check=True)
                samples[version].append(json.loads(child.stdout))
    medians = {}
    for version, rows in samples.items():
        medians[version] = {scenario: {mode: {
            key: statistics.median(sample["scenarios"][scenario][mode][key] for sample in rows)
            for key, value in rows[0]["scenarios"][scenario][mode].items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        } for mode in rows[0]["scenarios"][scenario]} for scenario in rows[0]["scenarios"]}
    report = dict(baseline_ref=baseline_ref, entries=args.entries, repeat=args.repeat,
                  samples=samples, medians=medians)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(medians, indent=2))


if __name__ == "__main__":
    main()
