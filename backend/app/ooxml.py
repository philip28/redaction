"""Format-preserving text replacement inside OOXML packages.

A .docx/.xlsx/.pptx file is a ZIP of XML parts. Word, Excel and PowerPoint split a
single visible sentence across many <w:t>/<a:t>/<t> nodes (spell-check state, edit
history, formatting). A naive per-node regex therefore misses most matches:

    <w:t>Ива</w:t><w:t>н Ив</w:t><w:t>анов</w:t>   ->  "Иванов" is never found

This module groups text nodes by their logical paragraph, runs the pattern over the
joined text, and writes the result back across the original nodes. Every other byte
of the package (images, charts, pivot tables, styles, numbering) is copied through
untouched, so the redacted file opens exactly like the original.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from bisect import bisect_right
from collections.abc import Callable, Iterator
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

#: Nodes that carry visible characters.
LEAF_TAGS = {f"{{{W}}}t", f"{{{A}}}t", f"{{{X}}}t"}

#: Nodes that define a "logical paragraph" - the unit we join before matching.
#: w:p  - Word paragraph (body, tables, headers, footers, text boxes, footnotes)
#: a:p  - DrawingML paragraph (PowerPoint shapes, charts, SmartArt, Excel drawings)
#: x:si - Excel shared string (incl. rich text split into <r><t> runs)
#: x:is - Excel inline string
#: x:text - Excel cell comment
CONTAINER_TAGS = {
    f"{{{W}}}p",
    f"{{{A}}}p",
    f"{{{X}}}si",
    f"{{{X}}}is",
    f"{{{X}}}text",
}

SUPPORTED_EXTENSIONS = {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"}

_SKIP_PART = re.compile(r"(^\[Content_Types\]\.xml$|_rels/|/theme/|calcChain\.xml$)")
_AUTHOR_ATTRS = {f"{{{W}}}author", f"{{{W}}}initials", "author", "initials"}
_META_FIELDS = {"creator", "lastModifiedBy", "lastPrinted", "description", "keywords", "category"}


class UnsupportedDocument(ValueError):
    """Carries a message key rather than prose, so the caller can localise it."""

    def __init__(self, code: str, filename: str) -> None:
        super().__init__(f"{filename}: {code}")
        self.code = code
        self.filename = filename


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def _is_content_part(name: str) -> bool:
    if not name.endswith(".xml"):
        return False
    if _SKIP_PART.search(name):
        return False
    return name.startswith(("word/", "ppt/", "xl/"))


def _parser() -> etree.XMLParser:
    return etree.XMLParser(huge_tree=True, resolve_entities=False, remove_blank_text=False)


def _paragraph_groups(root: etree._Element) -> Iterator[list[etree._Element]]:
    """Yield text nodes grouped by their nearest enclosing paragraph, in document order."""
    # The anchor element is stored alongside its leaves on purpose: lxml creates element
    # proxies lazily and reuses their memory once they are unreferenced, so id() values
    # get recycled. Holding a reference keeps every key unique for the whole walk.
    groups: dict[int, tuple[etree._Element, list[etree._Element]]] = {}
    order: list[int] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag not in LEAF_TAGS:
            continue
        anchor: etree._Element = el
        parent = el.getparent()
        while parent is not None:
            if isinstance(parent.tag, str) and parent.tag in CONTAINER_TAGS:
                anchor = parent
                break
            parent = parent.getparent()
        key = id(anchor)
        if key not in groups:
            groups[key] = (anchor, [])
            order.append(key)
        groups[key][1].append(el)
    for key in order:
        yield groups[key][1]


def _replace_in_group(
    leaves: list[etree._Element],
    pattern: re.Pattern[str],
    repl: Callable[[re.Match[str]], str],
) -> int:
    """Apply `pattern` across the joined text of one paragraph and write it back.

    The replacement lands entirely in the node where the match starts, so it keeps
    that run's formatting; nodes fully covered by the match are emptied.
    """
    live = [leaf for leaf in leaves if leaf.text]
    if not live:
        return 0

    texts: list[str] = [leaf.text for leaf in live]  # type: ignore[misc]
    joined = "".join(texts)
    matches = [m for m in pattern.finditer(joined) if m.end() > m.start()]
    if not matches:
        return 0

    starts: list[int] = []
    cursor = 0
    for text in texts:
        starts.append(cursor)
        cursor += len(text)

    def locate(offset: int) -> tuple[int, int]:
        idx = max(bisect_right(starts, offset) - 1, 0)
        return idx, offset - starts[idx]

    out = list(texts)
    # Reverse order keeps every not-yet-applied offset valid: edits only ever touch
    # positions at or after the current match start.
    for match in reversed(matches):
        replacement = repl(match)
        start_i, start_o = locate(match.start())
        end_i, end_o = locate(match.end())
        if start_i == end_i:
            out[start_i] = out[start_i][:start_o] + replacement + out[start_i][end_o:]
        else:
            out[start_i] = out[start_i][:start_o] + replacement
            for middle in range(start_i + 1, end_i):
                out[middle] = ""
            out[end_i] = out[end_i][end_o:]

    for leaf, new_text in zip(live, out):
        if new_text != leaf.text:
            leaf.text = new_text
            leaf.set(XML_SPACE, "preserve")
    return len(matches)


def _scrub_authors(root: etree._Element) -> None:
    """Comment/revision author names live in attributes, not text nodes."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr in _AUTHOR_ATTRS:
                el.set(attr, "REDACTED")


def _scrub_core_properties(data: bytes) -> bytes:
    """Blank out authorship in docProps/core.xml.

    Parsed rather than regexed: producers such as openpyxl declare xmlns:dc on the
    element itself, and a text-level rewrite silently drops that declaration and
    corrupts the package.
    """
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError:
        return data
    changed = False
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = etree.QName(el).localname
        if local in _META_FIELDS and el.text:
            el.text = ""
            changed = True
    if not changed:
        return data
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _process_part(
    data: bytes,
    pattern: re.Pattern[str] | None,
    repl: Callable[[re.Match[str]], str] | None,
    scrub_metadata: bool,
) -> tuple[bytes, int]:
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError:
        return data, 0

    count = 0
    if pattern is not None and repl is not None:
        for group in _paragraph_groups(root):
            count += _replace_in_group(group, pattern, repl)
    if scrub_metadata:
        _scrub_authors(root)

    if count or scrub_metadata:
        data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return data, count


# --------------------------------------------------------------------------- public API


def extract_paragraphs(path: Path) -> list[str]:
    """Every visible paragraph of a document, in reading order (used to prompt the LLM)."""
    if not is_supported(path.name):
        raise UnsupportedDocument("unsupported_format", path.name)

    paragraphs: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not _is_content_part(name):
                    continue
                try:
                    root = etree.fromstring(archive.read(name), _parser())
                except etree.XMLSyntaxError:
                    continue
                for group in _paragraph_groups(root):
                    text = "".join(leaf.text or "" for leaf in group).strip()
                    if text:
                        paragraphs.append(text)
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocument("not_ooxml", path.name) from exc
    return paragraphs


def replace_in_document(
    src: Path,
    dst: Path,
    pattern: re.Pattern[str],
    repl: Callable[[re.Match[str]], str],
    scrub_metadata: bool = False,
) -> int:
    """Rewrite `src` into `dst` applying `pattern`. Returns the number of replacements."""
    if not is_supported(src.name):
        raise UnsupportedDocument("unsupported_format", src.name)

    total = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as source, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if _is_content_part(item.filename):
                data, count = _process_part(data, pattern, repl, scrub_metadata)
                total += count
            elif scrub_metadata and item.filename == "docProps/core.xml":
                data = _scrub_core_properties(data)
            info = zipfile.ZipInfo(item.filename, date_time=item.date_time)
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            target.writestr(info, data)
    return total


def copy_document(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
