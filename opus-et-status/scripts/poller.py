"""Per-source TTL cache with a background refresh thread.

Fetchers are injected callables, so this module knows nothing about SSH or
cryo-ET and is fully testable with a fake clock. Poller.snapshot() is the seam
that a future on-disk snapshot/timeline layer would hook into.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

DEFAULT_TTLS = {
    "squeue": 15.0,
    # The run's own logs/ listing plus a sacct query for exactly those job ids.
    "run_jobs": 60.0,
    "run_state": 15.0,
    # validate.sh stats the whole run tree; polling it fast would punish the
    # login node for information that changes on the order of minutes.
    "validate": 120.0,
}


@dataclass(frozen=True)
class SourceState:
    data: object = None
    fetched_at: float | None = None
    error: str | None = None
    failures: int = 0


class Poller:
    def __init__(self, fetchers, ttls, *, clock=time.monotonic, max_backoff=300.0):
        self._fetchers = dict(fetchers)
        self._ttls = dict(ttls)
        self._clock = clock
        self._max_backoff = max_backoff
        self._lock = threading.Lock()
        self._states = {name: SourceState() for name in fetchers}
        self._next_due = {name: None for name in fetchers}
        self._stop = threading.Event()
        self._thread = None

    def snapshot(self):
        with self._lock:
            return dict(self._states)

    def invalidate(self, name):
        """Mark a source as due immediately, e.g. after something on the
        cluster changed as a result of a user action."""
        if name in self._next_due:
            self._next_due[name] = None

    def _due(self, name, now):
        nxt = self._next_due[name]
        return nxt is None or now >= nxt

    def refresh_due(self, now=None):
        now = self._clock() if now is None else now
        for name, fetch in self._fetchers.items():
            if not self._due(name, now):
                continue
            ttl = self._ttls.get(name, 30.0)
            try:
                data = fetch()
            except Exception as exc:  # a dead cluster must not kill the poller
                with self._lock:
                    prev = self._states[name]
                    failures = prev.failures + 1
                    # Retain the last good data; only the error is new.
                    self._states[name] = replace(prev, error=str(exc), failures=failures)
                backoff = min(ttl * (2 ** failures), self._max_backoff)
                self._next_due[name] = now + backoff
            else:
                with self._lock:
                    self._states[name] = SourceState(
                        data=data, fetched_at=now, error=None, failures=0
                    )
                self._next_due[name] = now + ttl

    def _loop(self, interval):
        while not self._stop.is_set():
            self.refresh_due()
            self._stop.wait(interval)

    def start(self, interval=5.0):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(interval,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
