"""Conversion of legacy binary Office files (.doc/.xls/.ppt) via LibreOffice.

A .doc is not an older .docx. It is an OLE compound binary with no XML and no ZIP, so
the format-preserving engine in ooxml.py cannot touch it: there are no text nodes to
rewrite and no parts to copy through. The only practical route is to convert to the
modern format first, and that conversion is a re-render - LibreOffice reads the binary
and writes a new package. Text survives intact; exact layout may shift slightly, and
macros, embedded OLE objects and revision history do not survive.

That trade-off is why this is off by default and why the job carries a warning whenever
it happens: a redaction tool must not quietly hand back a document that differs from
the one it was given without saying so.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

#: Legacy extension -> the modern format it converts to.
LEGACY_FORMATS = {
    ".doc": "docx",
    ".rtf": "docx",
    ".xls": "xlsx",
    ".ppt": "pptx",
}

#: Reverse direction, for handing back the format the file arrived in.
MODERN_TO_LEGACY = {
    ".docx": "doc",
    ".xlsx": "xls",
    ".pptx": "ppt",
}


class ConversionError(RuntimeError):
    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(f"{code}: {params}")


def is_legacy(filename: str) -> bool:
    return Path(filename).suffix.lower() in LEGACY_FORMATS


def find_soffice(configured: str = "") -> str | None:
    """Locate the LibreOffice binary, preferring an explicit setting."""
    if configured:
        return configured if Path(configured).exists() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    # Default install locations, since a service account often has a bare PATH.
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def convert(
    source: Path,
    target_format: str,
    soffice: str,
    timeout: float = 180.0,
    outdir: Path | None = None,
) -> Path:
    """Convert one file, returning the path to the result.

    Each call gets a private LibreOffice profile directory. Without that, two
    conversions running at once silently collide: the second sees the first's lock file,
    decides an instance is already running, hands the job to it and exits immediately -
    leaving no output file and no error.
    """
    outdir = outdir or source.parent
    outdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        command = [
            soffice,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to",
            target_format,
            "--outdir",
            str(outdir),
            str(source),
        ]
        log.info("converting %s -> %s", source.name, target_format)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("convert_timeout", name=source.name, seconds=int(timeout)) from exc
        except OSError as exc:
            raise ConversionError("convert_unavailable", error=str(exc)) from exc

    produced = outdir / f"{source.stem}.{target_format}"
    if not produced.exists():
        # LibreOffice reports most failures on stdout with a zero exit status, so the
        # missing file is the real signal.
        detail = (result.stderr or result.stdout or "").strip()[:300]
        log.error("conversion of %s failed: rc=%s %s", source.name, result.returncode, detail)
        raise ConversionError("convert_failed", name=source.name, error=detail or "no output produced")

    log.info("converted %s -> %s (%d bytes)", source.name, produced.name, produced.stat().st_size)
    return produced


def to_modern(source: Path, soffice: str, timeout: float = 180.0) -> Path:
    """Convert a legacy file to its modern equivalent, alongside the original."""
    target = LEGACY_FORMATS[source.suffix.lower()]
    workdir = source.parent / f"converted-{uuid.uuid4().hex[:8]}"
    return convert(source, target, soffice, timeout, outdir=workdir)


def to_legacy(source: Path, legacy_suffix: str, soffice: str, timeout: float = 180.0) -> Path:
    """Convert a modern file back to the legacy format it arrived in."""
    target = MODERN_TO_LEGACY.get(source.suffix.lower())
    if not target:
        raise ConversionError("convert_failed", name=source.name, error="no legacy equivalent")
    workdir = source.parent / f"legacy-{uuid.uuid4().hex[:8]}"
    return convert(source, target, soffice, timeout, outdir=workdir)
