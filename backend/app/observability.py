"""Diagnostics for the class of failure the browser can only report as "Failed to fetch".

A fetch() that never completes gives the frontend a bare TypeError with no status code,
so the evidence has to come from the server side. Three things are recorded here:

* every request with its id, duration and outcome, so a failed call in the browser
  console can be matched against what the server saw (or did not see at all);
* requests that take unusually long, which is what a proxy or browser eventually
  abandons;
* event-loop stalls, which make the server briefly unable to answer anything. This is
  the usual cause of intermittent failures during a scan, and it is invisible in normal
  request logs because the stalled requests never get as far as being handled.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

log = logging.getLogger("redactor.access")
loop_log = logging.getLogger("redactor.loop")
progress_log = logging.getLogger("redactor.progress")

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestLogMiddleware:
    """Pure ASGI middleware - it does not buffer response bodies, so downloads stream."""

    #: Endpoints the browser polls on a timer. At one call every 1.5s per open tab these
    #: swamp the log and hide everything worth reading, so they are logged only when they
    #: are slow, when they fail, or once every POLL_SAMPLE calls.
    POLL_PATTERNS = (re.compile(r"^/api/anonymize/jobs/[0-9a-f]+$"), re.compile(r"^/health$"))
    POLL_SAMPLE = 20

    def __init__(self, app: ASGIApp, slow_ms: int = 2000) -> None:
        self.app = app
        self.slow_ms = slow_ms
        self._poll_counts: dict[str, int] = defaultdict(int)

    def _is_poll(self, method: str, path: str) -> bool:
        return method == "GET" and any(p.match(path) for p in self.POLL_PATTERNS)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        rid = uuid.uuid4().hex[:8]
        token = request_id_var.set(rid)
        client = request.headers.get("x-client-id", "-")[:12]
        size = request.headers.get("content-length", "-")
        started = time.perf_counter()
        status = 0

        async def send_wrapper(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            # Includes the client hanging up mid-upload, which never reaches a handler
            # and is a common source of an unexplained failure in the browser.
            log.exception(
                "req=%s %s %s FAILED after %.0fms client=%s bytes=%s",
                rid,
                request.method,
                request.url.path,
                elapsed,
                client,
                size,
            )
            raise
        finally:
            request_id_var.reset(token)

        elapsed = (time.perf_counter() - started) * 1000
        path = request.url.path
        slow = elapsed >= self.slow_ms

        if slow or status >= 400:
            # Always worth seeing, whatever the level.
            level = logging.WARNING
            suffix = "  [SLOW]" if slow else ""
        elif self._is_poll(request.method, path):
            # Sampled: one line per POLL_SAMPLE calls, so a tail still shows the browser
            # is alive without a line every 1.5 seconds.
            self._poll_counts[path] += 1
            n = self._poll_counts[path]
            if n % self.POLL_SAMPLE:
                return
            level = logging.DEBUG
            suffix = f"  [poll x{n}]"
        else:
            level = logging.DEBUG
            suffix = ""

        log.log(
            level,
            "req=%s %s %s -> %s in %.0fms client=%s bytes=%s%s",
            rid, request.method, path, status, elapsed, client, size, suffix,
        )


async def loop_lag_monitor(threshold_ms: int, interval: float = 0.5) -> None:
    """Log whenever the event loop was blocked longer than the threshold.

    Sleeps for a known interval and measures the overshoot. Any significant excess means
    a coroutine held the loop and no request could be served during that window - the
    server was effectively offline for that long, without a single log line to show it.
    """
    while True:
        started = time.perf_counter()
        await asyncio.sleep(interval)
        lag_ms = (time.perf_counter() - started - interval) * 1000
        if lag_ms >= threshold_ms:
            loop_log.warning(
                "event loop blocked for %.0fms - requests could not be served during this "
                "window; suspect synchronous work in an async path",
                lag_ms,
            )


#: Third-party loggers that are noisy at their own default levels and say nothing we do
#: not already log ourselves. httpx in particular logs a line for every outbound call,
#: duplicating the llm[...] lines; python_multipart logs each part of every upload.
NOISY_LOGGERS = {
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "python_multipart": logging.WARNING,
    "multipart": logging.WARNING,
    "asyncio": logging.WARNING,
    "watchfiles": logging.WARNING,
}


def configure_logging(level: str) -> None:
    """Configure levels so INFO reads as a job narrative and DEBUG adds the detail.

    At INFO: job progress, LLM calls, slow or failed requests.
    At DEBUG: every request, prompt layers, raw model replies, per-document stats.
    Third-party libraries are pinned to WARNING either way - their DEBUG output is
    voluminous and would bury exactly what someone turned DEBUG on to read.
    """
    resolved = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        force=True,
    )
    for name, floor in NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(floor)


__all__ = [
    "Progress",
    "RequestLogMiddleware",
    "loop_lag_monitor",
    "configure_logging",
    "request_id_var",
]


class Progress:
    """Tracks a long job and logs a heartbeat, so a slow scan can be told from a stuck one.

    A scan can run for minutes with nothing in the log between "started" and "finished".
    That is precisely when someone asks whether it has hung. This emits a line at each
    step and, independently, a heartbeat while a step is still running - so silence in
    the log means the process is wedged, not merely busy.
    """

    def __init__(self, label: str, total: int, heartbeat_s: float = 15.0) -> None:
        self.label = label
        self.total = max(total, 0)
        self.done = 0
        self.heartbeat_s = heartbeat_s
        self.started = time.perf_counter()
        self._step_started = self.started
        self._current = "starting"
        self._task: asyncio.Task | None = None

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def _eta(self) -> str:
        if not self.done or not self.total:
            return "eta unknown"
        per_item = self.elapsed / self.done
        remaining = per_item * (self.total - self.done)
        return f"eta ~{remaining:.0f}s"

    def step(self, description: str) -> None:
        """Mark the start of a named step."""
        self._current = description
        self._step_started = time.perf_counter()
        progress_log.info(
            "%s: %s [%d/%d done, %.0fs elapsed, %s]",
            self.label, description, self.done, self.total, self.elapsed, self._eta(),
        )

    def complete(self, description: str = "") -> None:
        """Mark one unit of work finished."""
        self.done += 1
        took = time.perf_counter() - self._step_started
        progress_log.info(
            "%s: %d/%d done%s (took %.1fs, %.0fs elapsed, %s)",
            self.label, self.done, self.total,
            f" - {description}" if description else "",
            took, self.elapsed, self._eta(),
        )

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            progress_log.info(
                "%s: still working on %r - %d/%d done, %.0fs elapsed",
                self.label, self._current, self.done, self.total, self.elapsed,
            )

    async def __aenter__(self) -> "Progress":
        progress_log.info("%s: started, %d item(s)", self.label, self.total)
        self._task = asyncio.create_task(self._beat())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._task:
            self._task.cancel()
        if exc_type:
            progress_log.error(
                "%s: FAILED after %.1fs at %r (%d/%d done): %s",
                self.label, self.elapsed, self._current, self.done, self.total, exc,
            )
        else:
            progress_log.info(
                "%s: finished %d/%d in %.1fs", self.label, self.done, self.total, self.elapsed,
            )
