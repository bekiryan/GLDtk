"""Job progress and cancel for the long requests (v3 Part 14).

A request that carries a ``job_id`` runs inside :func:`job`; deep code then calls

    report(stage, message)   # a short, user-facing progress line (also a cancel checkpoint)
    checkpoint()             # raise Cancelled if the user pressed Cancel
    sleep(seconds)           # a backoff sleep that a cancel cuts short

without passing anything down: the current job lives in a ``contextvars``
variable.  Without a job (scripts, tests, the eval harness) ``report`` and
``checkpoint`` do nothing and ``sleep`` is ``time.sleep``.

The FastAPI routes are sync and run in the threadpool, so ``GET /progress`` and
``POST /cancel`` are answered while a request runs.  Finished jobs are kept for
``KEEP_SECONDS`` so the last poll still sees the final events.

:class:`Cancelled` derives from ``BaseException`` on purpose: the pipeline has
several ``except Exception`` blocks that turn errors into warnings or "LLM
error" responses (the completeness re-prompt, the edit LLM calls, the fallback
providers), and a cancel must pass through all of them to the route.
"""

from __future__ import annotations

import collections
import contextlib
import contextvars
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterator, Optional

MAX_EVENTS = 50
KEEP_SECONDS = 600.0


class Cancelled(BaseException):
    """The job was cancelled; nothing has been written."""


@dataclass
class Job:
    id: str
    started_at: float = field(default_factory=time.monotonic)
    stage: str = "queued"
    events: Deque[Dict[str, Any]] = field(default_factory=lambda: collections.deque(maxlen=MAX_EVENTS))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finished_at: Optional[float] = None

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


class JobRegistry:
    """Thread-safe ``job_id → Job`` map; finished jobs expire after ``keep_seconds``."""

    def __init__(self, keep_seconds: float = KEEP_SECONDS) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep_seconds

    def _prune(self) -> None:
        now = time.monotonic()
        for jid in [j for j, job in self._jobs.items()
                    if job.finished_at is not None and now - job.finished_at > self._keep]:
            del self._jobs[jid]

    def start(self, job_id: str) -> Job:
        """A fresh job; a cancel that arrived before the request is kept."""
        with self._lock:
            self._prune()
            old = self._jobs.get(job_id)
            job = Job(job_id)
            if old is not None and old.cancelled:
                job.cancel_event.set()
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            self._prune()
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Set the flag; returns whether the job is running.

        An unknown id gets a finished, cancelled placeholder, so a cancel that
        overtakes its own request still stops it.
        """
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
            if job is None:
                job = Job(job_id, finished_at=time.monotonic())
                self._jobs[job_id] = job
            job.cancel_event.set()
            return job.finished_at is None

    def add_event(self, job: Job, stage: str, message: str) -> None:
        with self._lock:
            job.stage = stage
            job.events.append({"t": round(job.elapsed(), 1), "stage": stage, "message": message})

    def finish(self, job: Job) -> None:
        with self._lock:
            job.finished_at = time.monotonic()

    def progress(self, job_id: str) -> Dict[str, Any]:
        """``{stage, events, elapsed_s, done, cancelled}``; unknown ids are done."""
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
            if job is None:
                return {"stage": None, "events": [], "elapsed_s": 0.0, "done": True,
                        "cancelled": False}
            return {
                "stage": job.stage,
                "events": list(job.events),
                "elapsed_s": round(job.elapsed(), 1),
                "done": job.finished_at is not None,
                "cancelled": job.cancelled,
            }


registry = JobRegistry()

_current: contextvars.ContextVar[Optional[Job]] = contextvars.ContextVar("gldtk_job", default=None)
_prefix: contextvars.ContextVar[str] = contextvars.ContextVar("gldtk_job_prefix", default="")


def current() -> Optional[Job]:
    return _current.get()


@contextlib.contextmanager
def job(job_id: Optional[str]) -> Iterator[Optional[Job]]:
    """Run the block as job *job_id* (None: no job, reporting is a no-op)."""
    if not job_id:
        yield None
        return
    reg = registry
    j = reg.start(job_id)
    token = _current.set(j)
    try:
        yield j
    except Cancelled:
        reg.add_event(j, "cancelled", "Cancelled. Nothing was changed.")
        raise
    except BaseException:
        reg.add_event(j, "error", "Failed")
        raise
    else:
        reg.add_event(j, "done", "Finished")
    finally:
        _current.reset(token)
        reg.finish(j)


def checkpoint() -> None:
    """Raise :class:`Cancelled` if the current job was cancelled."""
    j = _current.get()
    if j is not None and j.cancelled:
        raise Cancelled()


def report(stage: str, message: str) -> None:
    """Record a progress line for the current job (a cancel checkpoint first).

    Inside :func:`prefixed` the message starts with that prefix ("Level 2/3: ").
    """
    j = _current.get()
    if j is None:
        return
    checkpoint()
    registry.add_event(j, stage, _prefix.get() + message)


@contextlib.contextmanager
def prefixed(prefix: str) -> Iterator[None]:
    """Prefix every progress line reported in the block (one level of a world, v3 Part 17)."""
    token = _prefix.set(prefix)
    try:
        yield
    finally:
        _prefix.reset(token)


def sleep(seconds: float) -> None:
    """``time.sleep`` that a cancel interrupts (then raises :class:`Cancelled`)."""
    j = _current.get()
    if j is None:
        time.sleep(seconds)
        return
    j.cancel_event.wait(seconds)
    checkpoint()


__all__ = ["Cancelled", "Job", "JobRegistry", "checkpoint", "current", "job", "prefixed",
           "registry", "report", "sleep"]
