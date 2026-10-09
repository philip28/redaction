"""Analysis (LLM + rules) -> review -> deterministic regex redaction -> mapping file."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import logging

from . import ooxml
from .config import Settings
from .entities import (
    Entity,
    build_master_pattern,
    compile_entity,
    dedup_key,
    looks_generic,
    normalise,
    merge_duplicates,
    run_detectors,
    suggest_variants,
)
from .i18n import DEFAULT_LANG, t
from .llm import LLMClient, build_chunks
from .observability import Progress
from .store import Document, Job, JobProgress

log = logging.getLogger(__name__)

MAPPING_JSON = "mapping.json"
MAPPING_VERSION = 1


# --------------------------------------------------------------------------- analysis


async def analyze_job(job: Job, settings: Settings) -> None:
    """Read every document, collect candidates, and attach them to the job."""
    job.touch("analyzing")
    client = LLMClient(settings, job.lang) if settings.llm_configured else None
    progress = Progress(f"analyze[{job.id[:8]}]", len(job.documents))
    job.progress = JobProgress(stage="queued", documents_total=len(job.documents))

    def advance(**fields) -> None:
        """Update the state the browser polls, and stamp it so a stall is detectable."""
        # Restart the ETA clock whenever the unit of work changes - a new stage OR the
        # next document within the same stage, since chunk counts reset with it.
        moved_on = (
            fields.get("stage", job.progress.stage) != job.progress.stage
            or fields.get("document", job.progress.document) != job.progress.document
        )
        if moved_on:
            job.progress.stage_started_at = time.time()
        for key, value in fields.items():
            setattr(job.progress, key, value)
        job.progress.updated_at = time.time()
    corpora: dict[str, str] = {}
    candidates: dict[str, tuple[str, str]] = {}  # dedup key -> (category, value)
    sources: dict[str, str] = {}
    aliases: dict[str, list[str]] = {}  # dedup key -> spellings the model grouped

    try:
        async with progress:
            for document in job.documents:
                def read_document(path=document.source_path):
                    """One hop per document: parsing and the detectors are both blocking."""
                    paragraphs = ooxml.extract_paragraphs(path)
                    corpus = "\n".join(paragraphs)
                    return paragraphs, corpus, run_detectors(corpus, settings.detectors)

                progress.step(f"reading {document.filename}")
                advance(stage="reading", document=document.filename, chunks_done=0, chunks_total=0)
                try:
                    # Parsing a package and running the detectors is seconds of synchronous
                    # CPU work. On the event loop it would freeze every other request -
                    # including the browser's status polling, which then fails as a dead
                    # connection rather than an error the user can read.
                    paragraphs, corpus, detected = await asyncio.to_thread(read_document)
                except ooxml.UnsupportedDocument as exc:
                    message = t(exc.code, job.lang, name=exc.filename)
                    document.error = message
                    job.warnings.append(message)
                    continue
                corpora[document.id] = corpus

                log.debug(
                    "%s: %s -> %d paragraph(s), %d chars, %d rule hit(s)",
                    job.id[:8], document.filename, len(paragraphs), len(corpus), len(detected),
                )
                for category, value in detected:
                    key = dedup_key(value)
                    if key not in candidates:
                        candidates[key] = (category, normalise(value))
                        sources[key] = "rule"

                if client is not None and corpus.strip():
                    chunks = build_chunks(paragraphs, settings.llm_max_chunk_chars)
                    progress.step(f"scanning {document.filename} ({len(chunks)} chunk(s))")
                    advance(
                        stage="scanning",
                        document=document.filename,
                        chunks_done=0,
                        chunks_total=len(chunks),
                    )
                    found, warnings = await client.scan_chunks(
                        chunks,
                        label=f"{job.id[:8]} {document.filename}",
                        on_chunk=lambda done, total: advance(
                            chunks_done=done, chunks_total=total
                        ),
                    )
                    job.warnings.extend(warnings)
                    for finding in found:
                        clean = normalise(finding.value)
                        if looks_generic(clean):
                            continue
                        key = dedup_key(clean)
                        if key not in candidates:
                            candidates[key] = (
                                finding.category if finding.category in settings.categories else "OTHER",
                                clean,
                            )
                            sources[key] = "llm"
                        # The model groups spellings of one entity itself; keep them attached
                        # so they share a tag instead of becoming separate findings.
                        for alias in finding.aliases:
                            alias_clean = normalise(alias)
                            if alias_clean and not looks_generic(alias_clean):
                                aliases.setdefault(key, []).append(alias_clean)

                progress.complete(document.filename)
                advance(documents_done=progress.done)

            if not settings.llm_configured:
                job.warnings.append(t("no_llm", job.lang))

            full_corpus = "\n".join(corpora.values())
            def build_entities() -> list[Entity]:
                built: list[Entity] = []
                for key, (category, value) in candidates.items():
                    entity = Entity(
                        id=uuid.uuid4().hex[:12],
                        category=category,
                        value=value,
                        source=sources.get(key, "llm"),
                        inflect=category.upper() in settings.inflect_defaults,
                    )
                    entity.variants = suggest_variants(value, category, full_corpus)
                    for alias in aliases.get(key, []):
                        if alias not in entity.variants and alias != entity.value:
                            entity.variants.append(alias)
                    count_occurrences(entity, corpora, settings)
                    if entity.total_occurrences:
                        built.append(entity)
                return built

            # Regex matching every candidate against every document: also blocking work.
            progress.step(f"matching {len(candidates)} candidate(s) across documents")
            entities = await asyncio.to_thread(build_entities)
            # ПАО "МегаФон" and ПАО МегаФон are one company; give them one tag.
            before = len(entities)
            entities = merge_duplicates(entities)
            if before != len(entities):
                log.info("merged %d duplicate spelling(s) into existing findings", before - len(entities))
            entities.sort(key=lambda e: (e.category, -e.total_occurrences, e.value))
            job.entities = entities
            advance(stage="done", documents_done=len(job.documents))
            job.touch("ready")
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        job.error = f"{type(exc).__name__}: {exc}"
        job.touch("error")


def count_occurrences(entity: Entity, corpora: dict[str, str], settings: Settings) -> None:
    pattern = compile_entity(entity, settings.case_sensitive)
    entity.occurrences = {}
    if pattern is None:
        return
    for document_id, corpus in corpora.items():
        count = len(pattern.findall(corpus))
        if count:
            entity.occurrences[document_id] = count


def recount_job(job: Job, settings: Settings) -> None:
    """Re-run occurrence counts after the user edits variants or inflection."""
    corpora: dict[str, str] = {}
    for document in job.documents:
        if document.error:
            continue
        try:
            corpora[document.id] = "\n".join(ooxml.extract_paragraphs(document.source_path))
        except ooxml.UnsupportedDocument:
            continue
    for entity in job.entities:
        count_occurrences(entity, corpora, settings)


# --------------------------------------------------------------------------- tagging


def assign_tags(entities: list[Entity], settings: Settings) -> None:
    counters: dict[str, int] = defaultdict(int)
    for entity in entities:
        category = re.sub(r"[^A-Z0-9]+", "_", entity.category.upper()).strip("_") or "OTHER"
        counters[category] += 1
        entity.tag = f"{settings.tag_prefix}{category}_{counters[category]:03d}{settings.tag_suffix}"


def tag_scan_pattern(settings: Settings) -> re.Pattern[str]:
    return re.compile(
        re.escape(settings.tag_prefix) + r"[A-Z0-9_]{1,64}" + re.escape(settings.tag_suffix)
    )


# --------------------------------------------------------------------------- redaction


def redact_job(job: Job, settings: Settings) -> None:
    """Replace every selected entity with its tag across every document."""
    job.touch("redacting")
    selected = [e for e in job.entities if e.selected and e.total_occurrences]
    if not selected:
        job.error = t("nothing_selected", job.lang)
        job.touch("error")
        return

    assign_tags(selected, settings)
    selected_ids = {e.id for e in selected}
    for entity in job.entities:
        if entity.id not in selected_ids:
            entity.tag = None

    pattern, lookup = build_master_pattern(selected, settings.case_sensitive)
    if pattern is None:
        job.error = t("no_pattern", job.lang)
        job.touch("error")
        return

    hits: dict[str, int] = defaultdict(int)

    def repl(match: re.Match[str]) -> str:
        group = match.lastgroup or ""
        entity = lookup.get(group)
        if entity is None or entity.tag is None:
            return match.group(0)
        hits[entity.id] += 1
        return entity.tag

    output_dir = job.directory / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for document in job.documents:
            if document.error:
                continue
            stem = Path(document.filename).stem
            suffix = Path(document.filename).suffix
            output_name = f"{stem}{settings.redacted_suffix}{suffix}"
            output_path = output_dir / f"{document.id}{suffix}"
            document.replacements = ooxml.replace_in_document(
                document.source_path,
                output_path,
                pattern,
                repl,
                scrub_metadata=settings.scrub_metadata,
            )
            document.output_path = output_path
            document.output_name = output_name

        write_mapping(job, selected, settings, hits)
        job.touch("complete")
    except Exception as exc:  # noqa: BLE001
        job.error = f"{type(exc).__name__}: {exc}"
        job.touch("error")


def write_mapping(
    job: Job, entities: list[Entity], settings: Settings, hits: dict[str, int]
) -> None:
    payload = {
        "version": MAPPING_VERSION,
        "job_id": job.id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tag_prefix": settings.tag_prefix,
        "tag_suffix": settings.tag_suffix,
        "documents": [
            {"original": d.filename, "redacted": d.output_name, "replacements": d.replacements}
            for d in job.documents
            if d.output_name
        ],
        "entities": [
            {
                "tag": entity.tag,
                "category": entity.category,
                "value": entity.value,
                "variants": entity.variants,
                "replacements": hits.get(entity.id, 0),
            }
            for entity in entities
            if entity.tag
        ],
    }
    (job.directory / MAPPING_JSON).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_bundle(job: Job) -> Path:
    """Zip the redacted documents together with the mapping files."""
    bundle = job.directory / "redacted_bundle.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for document in job.documents:
            if document.output_path and document.output_path.exists():
                archive.write(document.output_path, document.output_name)
        mapping = job.directory / MAPPING_JSON
        if mapping.exists():
            archive.write(mapping, MAPPING_JSON)
    return bundle


# --------------------------------------------------------------------------- restore


def parse_mapping(raw: bytes, filename: str, lang: str = DEFAULT_LANG) -> tuple[dict[str, str], str, str]:
    """Read mapping.json. Returns tag -> value plus the tag delimiters.

    JSON only, deliberately. A CSV key file round-tripped through Excel loses trailing
    digits on long identifiers and turns values starting with "=" into formulas, and the
    restore would then report a clean run while writing the wrong originals back.
    """
    if not filename.lower().endswith(".json"):
        raise ValueError(t("mapping_not_json", lang))
    data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    entries = data.get("entities") or []
    mapping = {
        str(item["tag"]): str(item["value"])
        for item in entries
        if item.get("tag") and item.get("value") is not None
    }
    return mapping, data.get("tag_prefix", "[["), data.get("tag_suffix", "]]")


def restore_job(job: Job, mapping: dict[str, str], settings: Settings) -> None:
    """Swap tags back for their original values."""
    job.touch("redacting")
    pattern = tag_scan_pattern(settings)
    unmapped: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        if tag in mapping:
            return mapping[tag]
        unmapped.add(tag)
        return tag

    output_dir = job.directory / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for document in job.documents:
            if document.error:
                continue
            stem = Path(document.filename).stem
            if stem.endswith(settings.redacted_suffix):
                stem = stem[: -len(settings.redacted_suffix)]
            suffix = Path(document.filename).suffix
            output_path = output_dir / f"{document.id}{suffix}"
            document.replacements = ooxml.replace_in_document(
                document.source_path, output_path, pattern, repl
            )
            document.output_path = output_path
            document.output_name = f"{stem}{settings.restored_suffix}{suffix}"

        job.unmapped_tags = sorted(unmapped)
        if unmapped:
            job.warnings.append(
                t(
                    "unmapped_tags",
                    job.lang,
                    count=len(unmapped),
                    tags=", ".join(sorted(unmapped)[:10]),
                )
            )
        job.touch("complete")
    except Exception as exc:  # noqa: BLE001
        job.error = f"{type(exc).__name__}: {exc}"
        job.touch("error")


def build_restored_bundle(job: Job) -> Path:
    bundle = job.directory / "restored_bundle.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for document in job.documents:
            if document.output_path and document.output_path.exists():
                archive.write(document.output_path, document.output_name)
    return bundle


def register_upload(job: Job, filename: str, data: bytes) -> Document:
    document = Document(
        id=uuid.uuid4().hex[:12],
        filename=Path(filename).name,
        size=len(data),
        source_path=job.directory / "source" / f"{uuid.uuid4().hex[:12]}{Path(filename).suffix}",
    )
    document.source_path.write_bytes(data)
    job.documents.append(document)
    return document
