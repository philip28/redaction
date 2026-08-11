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
import time
import uuid
from contextvars import ContextVar

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

log = logging.getLogger("redactor.access")
loop_log = logging.getLogger("redactor.loop")

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestLogMiddleware:
    """Pure ASGI middleware - it does not buffer response bodies, so downloads stream."""

    def __init__(self, app: ASGIApp, slow_ms: int = 2000) -> None:
        self.app = app
        self.slow_ms = slow_ms

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
        level = logging.WARNING if elapsed >= self.slow_ms else logging.INFO
        log.log(
            level,
            "req=%s %s %s -> %s in %.0fms client=%s bytes=%s%s",
            rid,
            request.method,
            request.url.path,
            status,
            elapsed,
            client,
            size,
            "  [SLOW]" if elapsed >= self.slow_ms else "",
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


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        force=True,
    )
    # Uvicorn logs the same requests in less detail; ours supersede them.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


__all__ = [
    "RequestLogMiddleware",
    "loop_lag_monitor",
    "configure_logging",
    "request_id_var",
]
