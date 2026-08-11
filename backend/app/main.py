from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .observability import RequestLogMiddleware, configure_logging, loop_lag_monitor
from .store import JobStore

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger(__name__)

store = JobStore(settings)


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            removed = store.purge_expired()
            if removed:
                log.info("purged %s expired job(s)", removed)
        except Exception:  # noqa: BLE001
            log.exception("purge failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "starting: data_dir=%s ttl=%smin llm=%s model=%s lang=%s",
        settings.data_dir.resolve(),
        settings.job_ttl_minutes,
        settings.llm_base_url if settings.llm_configured else "not configured",
        settings.llm_model if settings.llm_configured else "-",
        settings.default_language,
    )
    tasks = [asyncio.create_task(_sweeper())]
    if settings.loop_lag_warn_ms > 0:
        tasks.append(asyncio.create_task(loop_lag_monitor(settings.loop_lag_warn_ms)))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Document redaction service", version="1.0.0", lifespan=lifespan)

app.add_middleware(RequestLogMiddleware, slow_ms=settings.slow_request_ms)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # So a failed call in the browser console can be quoted back against the server log.
    expose_headers=["X-Request-Id"],
)

from .routes import router  # noqa: E402  (imported after `store` exists)

app.include_router(router)


@app.get("/health")
def health() -> dict:
    """Cheap liveness probe. The frontend calls it to tell a dead server from a bad request."""
    return {"status": "ok", "jobs": len(store._jobs)}  # noqa: SLF001
