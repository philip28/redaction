"""Job state lives in memory, uploaded bytes live on disk. No database, by design.

Each browser gets a client id (generated client-side, stored in localStorage and sent as
X-Client-Id). Jobs are scoped to that id, which is what makes the service multi-user
without a login. Everything is deleted after JOB_TTL_MINUTES.
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .entities import Entity
from .i18n import DEFAULT_LANG


@dataclass
class Document:
    id: str
    filename: str
    size: int
    source_path: Path
    output_path: Path | None = None
    output_name: str | None = None
    replacements: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "output_name": self.output_name,
            "replacements": self.replacements,
            "error": self.error,
        }


@dataclass
class JobProgress:
    """Live state of a running scan, polled by the browser.

    Stages are codes rather than sentences: the frontend renders them in the interface
    language, which may differ from the language the job was started in.
    """

    stage: str = "queued"  # queued | reading | scanning | matching | done
    document: str | None = None
    documents_done: int = 0
    documents_total: int = 0
    chunks_done: int = 0
    chunks_total: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    #: When the current stage began. The ETA is measured from here: chunk counts reset
    #: per document, so dividing whole-job elapsed by them overstates the remainder
    #: badly at every document boundary.
    stage_started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        elapsed = max(time.time() - self.started_at, 0.0)
        return {
            "stage": self.stage,
            "document": self.document,
            "documents_done": self.documents_done,
            "documents_total": self.documents_total,
            "chunks_done": self.chunks_done,
            "chunks_total": self.chunks_total,
            "elapsed_seconds": round(elapsed, 1),
            # Time since the last observable change. The browser uses this to tell a slow
            # job from a stalled one, which a percentage alone cannot express.
            "stalled_seconds": round(max(time.time() - self.updated_at, 0.0), 1),
            "eta_seconds": self._eta(max(time.time() - self.stage_started_at, 0.0)),
        }

    def _eta(self, stage_elapsed: float) -> float | None:
        """Seconds remaining for the current stage, from chunks when scanning.

        Chunks are the finer measure and the slow part; documents alone jump from 0% to
        50% on a two-file job and say nothing in between.
        """
        done, total = (
            (self.chunks_done, self.chunks_total)
            if self.chunks_total
            else (self.documents_done, self.documents_total)
        )
        if not done or not total or done >= total:
            return None
        return round(stage_elapsed / done * (total - done), 1)


@dataclass
class Job:
    id: str
    client_id: str
    kind: str  # "anonymize" | "deanonymize"
    directory: Path
    #: Language for messages produced by background work after the request has returned.
    lang: str = DEFAULT_LANG
    status: str = "pending"  # pending | analyzing | ready | redacting | complete | error
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    documents: list[Document] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    unmapped_tags: list[str] = field(default_factory=list)
    progress: JobProgress = field(default_factory=JobProgress)

    def touch(self, status: str | None = None) -> None:
        if status:
            self.status = status
        self.updated_at = time.time()

    def document(self, document_id: str) -> Document | None:
        return next((d for d in self.documents if d.id == document_id), None)

    def entity(self, entity_id: str) -> Entity | None:
        return next((e for e in self.entities if e.id == entity_id), None)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "documents": [d.to_dict() for d in self.documents],
            "entities": [e.to_dict() for e in self.entities],
            "warnings": self.warnings,
            "error": self.error,
            "unmapped_tags": self.unmapped_tags,
            "progress": self.progress.to_dict(),
        }


class JobStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.data_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, client_id: str, kind: str, lang: str = DEFAULT_LANG) -> Job:
        self.purge_expired()
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        (directory / "source").mkdir(parents=True, exist_ok=True)
        (directory / "output").mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, client_id=client_id, kind=kind, directory=directory, lang=lang)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str, client_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.client_id != client_id:
            return None
        return job

    def delete(self, job_id: str, client_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.client_id != client_id:
                return False
            self._jobs.pop(job_id, None)
        shutil.rmtree(job.directory, ignore_errors=True)
        return True

    def purge_expired(self) -> int:
        cutoff = time.time() - self.settings.job_ttl_minutes * 60
        with self._lock:
            expired = [job for job in self._jobs.values() if job.updated_at < cutoff]
            for job in expired:
                self._jobs.pop(job.id, None)
        for job in expired:
            shutil.rmtree(job.directory, ignore_errors=True)
        # Sweep directories orphaned by a restart, but never one a live job still owns:
        # a long review session leaves the files untouched while the job is very much alive.
        with self._lock:
            live = set(self._jobs)
        for path in self.root.iterdir():
            if path.is_dir() and path.name not in live and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        return len(expired)
