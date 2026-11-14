"""Measure real Tk calls and event delay for rendered-tree reset strategies."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk

from storage_scanner.gui.tree_view import ResultTree
from storage_scanner.platform_fs.metrics import peak_memory_bytes


def measure(mode, count, visible=False, baseline_ref="2b67874e30c32eb669c1ce3c8cbb0e42310d03c6"):
    root = tk.Tk()
    if visible:
        root.geometry("900x650")
    else:
        root.withdraw()
    view_class = ResultTree
    if mode == "baseline":
        source = subprocess.check_output(
            ["git", "show", f"{baseline_ref}:storage_scanner/gui/tree_view.py"], encoding="utf-8")
        namespace = {}
        exec(compile(source, f"{baseline_ref}:tree_view.py", "exec"), namespace)
        view_class = namespace["ResultTree"]
    view = view_class(root)
    view.pack(fill="both", expand=True)
    # All rows are actual Tk items under expanded folders (not just store rows).
    for group in range((count + 199) // 200):
        parent = view.tree.insert("", "end", open=True, text=str(group))
        view._paths[parent] = f"folder-{group}"
        for index in range(min(200, count - group * 200)):
            item = view.tree.insert(parent, "end", text=str(index))
            view._paths[item] = f"{group}/{index}"
    root.update()
    gaps = []
    last = time.perf_counter()
    timer = None

    def heartbeat():
        nonlocal last, timer
        now = time.perf_counter()
        gaps.append(now - last)
        last = now
        timer = root.after(1, heartbeat)

    heartbeat()
    calls = []
    original_call = view.tree.tk

    class MeasuredTk:
        def call(self, *args):
            tick = time.perf_counter()
            try:
                return original_call.call(*args)
            finally:
                calls.append(time.perf_counter() - tick)

        def __getattr__(self, name):
            return getattr(original_call, name)

    view.tk = view.tree.tk = MeasuredTk()
    tick = time.perf_counter()
    if mode == "delete":
        view.tree.delete(*view.tree.get_children())
    elif mode == "replace":
        view.tree.destroy()
        ttk.Treeview(view).grid(row=0, column=0)
    elif mode == "chunk":
        pending = iter(reversed(tuple(view._paths)))

        def remove():
            for _ in range(150):
                item = next(pending, None)
                if item is None:
                    return
                view.tree.delete(item)
            root.after(1, remove)

        remove()
    else:
        view.reset(None)
    reset_seconds = time.perf_counter() - tick
    while (view.tree.get_children() if mode == "chunk" else getattr(view, "_retired", ())):
        root.update()
        time.sleep(0.001)
    root.update()
    # Let a due heartbeat observe the reset call's synchronous cost.
    time.sleep(0.002)
    root.update()
    result = dict(mode=mode, file_rows=count, reset_seconds=reset_seconds,
                  cleanup_seconds=time.perf_counter() - tick,
                  max_event_gap_seconds=max(gaps), max_tk_call_seconds=max(calls, default=0),
                  peak_process_memory_bytes=peak_memory_bytes())
    root.after_cancel(timer)
    root.destroy()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=30000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--modes", nargs="+", default=["baseline", "delete", "replace", "chunk", "reset"])
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--baseline-ref", default="2b67874e30c32eb669c1ce3c8cbb0e42310d03c6")
    parser.add_argument("--visible", action="store_true", help="Include normal window drawing")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(measure(args.child, args.rows, args.visible, args.baseline_ref)))
        return
    # Fresh processes keep lifetime peak memory comparable across strategies.
    samples = [json.loads(subprocess.check_output(
        [sys.executable, "-m", "benchmarks.tree_reset", "--child", mode, "--rows", str(args.rows),
         "--baseline-ref", args.baseline_ref]
        + (["--visible"] if args.visible else []),
        text=True)) for mode in args.modes for _ in range(args.repeat)]
    report = {"samples": samples, "medians": {mode: {
        key: statistics.median(row[key] for row in samples if row["mode"] == mode)
        for key in samples[0] if key not in ("mode", "file_rows")
    } for mode in args.modes}}
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
