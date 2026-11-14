"""No Tk imports. Worker and independent bounded-queue collector lifecycle."""
from dataclasses import dataclass, field
from queue import Queue, Full
from threading import Event, Lock, Thread
import time

from storage_scanner.folder.models import FolderConfig, FolderProgress, FolderSummary
from storage_scanner.folder.results import FolderResultStore
from storage_scanner.folder.scanner import scan


@dataclass
class _Run:
    run_id: int
    config: FolderConfig
    store: FolderResultStore
    queue: Queue = field(default_factory=lambda: Queue(maxsize=8))
    cancel: Event = field(default_factory=Event)
    superseded: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)
    progress: FolderProgress = field(default_factory=FolderProgress)
    summary: FolderSummary | None = None
    finished: bool = False
    error: str | None = None
    started: float = field(default_factory=time.perf_counter)


@dataclass(frozen=True)
class RunSnapshot:
    run_id: int
    store: FolderResultStore
    progress: FolderProgress
    summary: FolderSummary | None
    finished: bool
    error: str | None
    elapsed_seconds: float


class FolderController:
    def __init__(self, scan_lock=None):
        self._run = None
        self._next_id = 0
        self._scan_lock = scan_lock if scan_lock is not None else Lock()
        self._closed = False

    def start(self, config: FolderConfig) -> RunSnapshot:
        if self._closed:
            raise RuntimeError("Controller is closed")
        if self._run:
            self._run.cancel.set()
            self._run.superseded.set()
        self._next_id += 1
        run = _Run(self._next_id, config, FolderResultStore(config.roots))
        self._run = run
        Thread(target=self._collect, args=(run,), name=f"results-{run.run_id}", daemon=True).start()
        Thread(target=self._work, args=(run,), name=f"scan-{run.run_id}", daemon=True).start()
        return self.snapshot()

    def cancel(self):
        if self._run:
            self._run.cancel.set()

    def close(self):
        self._closed = True
        if self._run:
            self._run.cancel.set()
            self._run.superseded.set()

    def snapshot(self) -> RunSnapshot | None:
        run = self._run
        if run is None:
            return None
        with run.lock:
            elapsed = run.summary.elapsed_seconds if run.summary else time.perf_counter() - run.started
            return RunSnapshot(run.run_id, run.store, run.progress, run.summary,
                               run.finished, run.error, elapsed)

    def _work(self, run):
        acquired = False

        def deliver(batch):
            while not run.superseded.is_set():
                try:
                    run.queue.put(batch, timeout=0.05)
                    return
                except Full:
                    continue

        def progress(value):
            with run.lock:
                run.progress = value

        try:
            while not run.cancel.is_set():
                acquired = self._scan_lock.acquire(timeout=0.05)
                if acquired:
                    break
            if acquired:
                summary = scan(run.config, deliver, progress, run.cancel.is_set)
            else:
                summary = FolderSummary(0, 0, 0, 0, 0.0, True)
            with run.lock:
                run.summary = summary
        except Exception as exc:
            with run.lock:
                run.error = f"{type(exc).__name__}: {exc}"
        finally:
            if acquired:
                self._scan_lock.release()
            # Collector always drains, including failed or superseded runs.
            run.queue.put(None)

    @staticmethod
    def _collect(run):
        while True:
            batch = run.queue.get()
            if batch is None:
                break
            if not run.superseded.is_set():
                try:
                    run.store.add_batch(batch)
                except Exception as exc:
                    with run.lock:
                        run.error = f"Result collector: {type(exc).__name__}: {exc}"
                    run.cancel.set()
                    run.superseded.set()
        try:
            run.store.finish(complete=not run.error and run.summary is not None
                             and not run.summary.cancelled and not run.summary.errors)
        except Exception as exc:
            with run.lock:
                run.error = f"Result finalization: {type(exc).__name__}: {exc}"
        with run.lock:
            run.finished = True
