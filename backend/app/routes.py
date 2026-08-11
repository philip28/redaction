from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import ooxml, pipeline
from .config import Settings, get_settings
from .i18n import LANGS, normalise_lang, t
from .store import Job, JobStore

router = APIRouter(prefix="/api")

#: Above this size an upload is written on a worker thread rather than the event loop.
_OFFLOAD_WRITE_BYTES = 4 * 1024 * 1024

MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
}


def get_store() -> JobStore:
    from .main import store

    return store


def client_id(x_client_id: str | None = Header(default=None)) -> str:
    if not x_client_id or len(x_client_id) < 8:
        raise HTTPException(400, t("missing_client_id"))
    return x_client_id[:64]


def lang(accept_language: str | None = Header(default=None)) -> str:
    """Interface language for messages, taken from the header the frontend sends."""
    return normalise_lang(accept_language)


def _job_or_404(
    store: JobStore, job_id: str, cid: str, kind: str | None = None, lc: str = "ru"
) -> Job:
    job = store.get(job_id, cid)
    if job is None or (kind and job.kind != kind):
        raise HTTPException(404, t("job_gone", lc))
    return job


async def _read_uploads(
    files: list[UploadFile], job: Job, settings: Settings, require_office: bool = True
) -> None:
    lc = job.lang
    if not files:
        raise HTTPException(400, t("no_files", lc))
    if len(files) > settings.max_files_per_job:
        raise HTTPException(400, t("too_many_files", lc, max=settings.max_files_per_job))
    for upload in files:
        name = Path(upload.filename or "document").name
        if require_office and not ooxml.is_supported(name):
            raise HTTPException(400, t("unsupported_format", lc, name=name))
        data = await upload.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(400, t("file_too_large", lc, name=name, mb=settings.max_upload_mb))
        if not data:
            raise HTTPException(400, t("file_empty", lc, name=name))
        # Large payloads are written on a worker thread so the loop keeps serving; small
        # ones are written inline, because a thread hop costs far more than the write.
        if len(data) > _OFFLOAD_WRITE_BYTES:
            await asyncio.to_thread(pipeline.register_upload, job, name, data)
        else:
            pipeline.register_upload(job, name, data)


def _download(path: Path, filename: str, lc: str = "ru") -> FileResponse:
    if not path.exists():
        raise HTTPException(404, t("file_not_ready", lc))
    media = MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=filename)


# --------------------------------------------------------------------------- meta


@router.get("/config")
def read_config(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "categories": settings.categories,
        "detectors": settings.detectors,
        "inflect_defaults": sorted(settings.inflect_defaults),
        "max_upload_mb": settings.max_upload_mb,
        "max_files_per_job": settings.max_files_per_job,
        "job_ttl_minutes": settings.job_ttl_minutes,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model if settings.llm_configured else None,
        "tag_prefix": settings.tag_prefix,
        "tag_suffix": settings.tag_suffix,
        "supported_extensions": sorted(ooxml.SUPPORTED_EXTENSIONS),
        "languages": list(LANGS),
        "default_language": settings.default_language,
    }


# --------------------------------------------------------------------------- anonymize


@router.post("/anonymize/jobs")
async def create_anonymize_job(
    files: list[UploadFile] = File(...),
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    job = store.create(cid, "anonymize", lc)
    try:
        await _read_uploads(files, job, settings)
    except HTTPException:
        store.delete(job.id, cid)
        raise
    job.touch("analyzing")
    asyncio.create_task(pipeline.analyze_job(job, settings))
    return job.to_dict()


@router.get("/anonymize/jobs/{job_id}")
def read_anonymize_job(
    job_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> dict:
    return _job_or_404(store, job_id, cid, "anonymize", lc).to_dict()


class EntityPatch(BaseModel):
    id: str
    selected: bool | None = None
    inflect: bool | None = None
    variants: list[str] | None = None
    category: str | None = None


class EntityPatchRequest(BaseModel):
    entities: list[EntityPatch] = Field(default_factory=list)


@router.patch("/anonymize/jobs/{job_id}/entities")
def update_entities(
    job_id: str,
    request: EntityPatchRequest,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    job = _job_or_404(store, job_id, cid, "anonymize", lc)
    needs_recount = False
    for patch in request.entities:
        entity = job.entity(patch.id)
        if entity is None:
            continue
        if patch.selected is not None:
            entity.selected = patch.selected
        if patch.category is not None:
            entity.category = patch.category.upper()
        if patch.inflect is not None and patch.inflect != entity.inflect:
            entity.inflect = patch.inflect
            needs_recount = True
        if patch.variants is not None:
            cleaned = [v.strip() for v in patch.variants if v.strip()]
            if cleaned != entity.variants:
                entity.variants = cleaned
                needs_recount = True
    if needs_recount:
        pipeline.recount_job(job, settings)
    job.touch()
    return job.to_dict()


@router.post("/anonymize/jobs/{job_id}/redact")
def redact(
    job_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    job = _job_or_404(store, job_id, cid, "anonymize", lc)
    if job.status == "analyzing":
        raise HTTPException(409, t("scan_running", lc))
    pipeline.redact_job(job, settings)
    if job.status == "error":
        raise HTTPException(400, job.error or t("redaction_failed", lc))
    pipeline.build_bundle(job)
    return job.to_dict()


@router.get("/anonymize/jobs/{job_id}/bundle")
def download_bundle(
    job_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = _job_or_404(store, job_id, cid, "anonymize", lc)
    return _download(job.directory / "redacted_bundle.zip", f"redacted_{job.id[:8]}.zip", lc)


@router.get("/anonymize/jobs/{job_id}/mapping.json")
def download_mapping(
    job_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = _job_or_404(store, job_id, cid, "anonymize", lc)
    return _download(job.directory / "mapping.json", f"mapping_{job.id[:8]}.json", lc)


@router.get("/anonymize/jobs/{job_id}/documents/{document_id}")
def download_redacted_document(
    job_id: str,
    document_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = _job_or_404(store, job_id, cid, "anonymize", lc)
    document = job.document(document_id)
    if document is None or not document.output_path:
        raise HTTPException(404, t("doc_not_ready", lc))
    return _download(document.output_path, document.output_name or document.filename, lc)


# --------------------------------------------------------------------------- de-anonymize


@router.post("/deanonymize/jobs")
async def create_deanonymize_job(
    files: list[UploadFile] = File(...),
    mapping: UploadFile = File(...),
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    job = store.create(cid, "deanonymize", lc)
    try:
        raw = await mapping.read()
        key_name = mapping.filename or "mapping.json"
        # Checked here rather than inside parse_mapping so the reason reaches the user on
        # its own, instead of nested inside a generic "could not read" wrapper.
        if not key_name.lower().endswith(".json"):
            raise HTTPException(400, t("mapping_not_json", lc))
        try:
            table, prefix, suffix = pipeline.parse_mapping(raw, key_name, lc)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, t("mapping_unreadable", lc, error=exc)) from exc
        if not table:
            raise HTTPException(400, t("mapping_empty", lc))
        if prefix != settings.tag_prefix or suffix != settings.tag_suffix:
            job.warnings.append(
                t(
                    "mapping_tag_mismatch",
                    lc,
                    prefix=prefix,
                    suffix=suffix,
                    server_prefix=settings.tag_prefix,
                    server_suffix=settings.tag_suffix,
                )
            )
        await _read_uploads(files, job, settings)
        # The whole restore - unzip, parse, replace, rezip - is synchronous work. Left on
        # the event loop it stalls every other request for its full duration.
        await asyncio.to_thread(pipeline.restore_job, job, table, settings)
        if job.status == "error":
            raise HTTPException(400, job.error or t("restore_failed", lc))
        await asyncio.to_thread(pipeline.build_restored_bundle, job)
    except HTTPException:
        store.delete(job.id, cid)
        raise
    return job.to_dict()


@router.get("/deanonymize/jobs/{job_id}/bundle")
def download_restored_bundle(
    job_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = _job_or_404(store, job_id, cid, "deanonymize", lc)
    return _download(job.directory / "restored_bundle.zip", f"restored_{job.id[:8]}.zip", lc)


@router.get("/deanonymize/jobs/{job_id}/documents/{document_id}")
def download_restored_document(
    job_id: str,
    document_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = _job_or_404(store, job_id, cid, "deanonymize", lc)
    document = job.document(document_id)
    if document is None or not document.output_path:
        raise HTTPException(404, t("doc_not_ready", lc))
    return _download(document.output_path, document.output_name or document.filename, lc)


# --------------------------------------------------------------------------- cleanup


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    cid: str = Depends(client_id),
    lc: str = Depends(lang),
    store: JobStore = Depends(get_store),
) -> dict:
    if not store.delete(job_id, cid):
        raise HTTPException(404, t("job_gone", lc))
    return {"deleted": job_id}
