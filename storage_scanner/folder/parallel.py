"""Fixed directory workers, local DFS stacks and a bounded result channel.

Callbacks run only on the coordinating scan thread. No file-level tasks exist.
"""
from collections import deque
from queue import Queue, Empty
from threading import Condition, Event, Thread
import time

from storage_scanner.folder.models import FolderProgress, FolderSummary


class _Directories:
    def __init__(self, roots, stop):
        self.pending = deque(roots)
        self.stop = stop
        self.condition = Condition()
        self.active = 0
        self.waiting = 0

    def share(self, path):
        with self.condition:
            if self.waiting > len(self.pending) and not self.stop.is_set():
                self.pending.append(path)
                self.condition.notify()
                return True
        return False

    def roots(self):
        # A yielded subtree remains active until the local DFS has exhausted it.
        while True:
            with self.condition:
                self.waiting += 1
                try:
                    while not self.pending and self.active and not self.stop.is_set():
                        self.condition.wait(.05)
                    if self.stop.is_set() or not self.pending:
                        return
                    path = self.pending.popleft()
                    self.active += 1
                finally:
                    self.waiting -= 1
            try:
                yield path
            finally:
                with self.condition:
                    self.active -= 1
                    self.condition.notify_all()


def scan_parallel(config, roots, on_batch, on_progress, should_cancel):
    from storage_scanner.folder.scanner import _scan_serial
    started = time.perf_counter()
    stop = Event()
    if should_cancel():
        stop.set()
    work = _Directories(roots, stop)
    messages = Queue(maxsize=config.workers * 2)
    latest = [FolderProgress() for _ in range(config.workers)]
    summaries = []
    failure = None
    current = ""

    def worker(index):
        tasks = work.roots()
        try:
            summary = _scan_serial(
                config, lambda batch: messages.put(("batch", index, batch)),
                lambda value: messages.put(("progress", index, value)),
                stop.is_set, tasks, work.share)
            messages.put(("summary", index, summary))
        except BaseException as exc:
            stop.set()
            messages.put(("error", index, exc))
        finally:
            tasks.close()
            messages.put(("done", index, None))

    threads = [Thread(target=worker, args=(i,), name=f"folder-{i}", daemon=True)
               for i in range(config.workers)]
    for thread in threads:
        thread.start()
    done = 0
    last_progress = started

    def progress():
        totals = [sum(getattr(p, field) for p in latest) for field in
                  ("files_scanned", "directories_scanned", "bytes_scanned", "errors")]
        on_progress(FolderProgress(*totals, current))

    while done < len(threads):
        try:
            if failure is None and should_cancel():
                stop.set()
            try:
                kind, index, value = messages.get(timeout=.05)
            except Empty:
                continue
            if kind == "done":
                done += 1
            elif kind == "error":
                if failure is None:
                    failure = value
                stop.set()
            elif kind == "summary":
                summaries.append(value)
            elif kind == "progress":
                latest[index] = value
                current = value.current_directory
            elif kind == "batch" and failure is None:
                on_batch(value)
            now = time.perf_counter()
            if failure is None and now - last_progress >= config.progress_interval:
                progress()
                last_progress = now
        except BaseException as exc:
            # Continue draining so a worker cannot be stranded in a bounded put.
            if failure is None:
                failure = exc
            stop.set()
    for thread in threads:
        thread.join()
    if failure is not None:
        raise failure
    progress()
    totals = [sum(getattr(s, field) for s in summaries) for field in
              ("files_scanned", "directories_scanned", "bytes_scanned", "errors")]
    return FolderSummary(*totals, time.perf_counter() - started,
                         stop.is_set() or any(s.cancelled for s in summaries))
