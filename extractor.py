"""PDF text extraction and reference-list parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf as fitz


class ExtractionError(ValueError):
    """A PDF could not be read or did not contain a usable reference list."""


@dataclass(frozen=True)
class Reference:
    original_text: str
    title: str
    year: str | None


_HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.)]?\s+)?"
    r"(?:references|bibliography|works\s+cited|literature\s+cited|reference\s+list|"
    r"references\s+and\s+notes)\s*:?\s*$",
    re.IGNORECASE,
)
_END_HEADING_RE = re.compile(
    r"^(?:appendix(?:\s+[A-Z0-9]+)?|acknowledg(?:e)?ments|author\s+contributions|"
    r"supplementary\s+materials?)\s*:?\s*$",
    re.IGNORECASE,
)
_NUMBERED_RE = re.compile(r"^\s*(?:\[(\d{1,4})\]|\(?\d{1,4}[.)])\s+\S")
_YEAR_RE = re.compile(r"(?<!\d)((?:18|19|20)\d{2})([a-z])?(?!\d)")
_AUTHOR_YEAR_RE = re.compile(
    r"^[A-ZÀ-ÖØ-Þ][^\n]{0,140}?(?:\(\s*)?(?:18|19|20)\d{2}[a-z]?\)?(?=[.,;:\s]|$)"
)


def _extract_text(pdf_bytes: bytes, *, sort: bool = True) -> list[str]:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, ValueError, RuntimeError) as exc:
        raise ExtractionError("เปิดไฟล์ PDF ไม่ได้ หรือไฟล์เสียหาย") from exc

    try:
        if document.page_count == 0:
            raise ExtractionError("ไฟล์ PDF ไม่มีหน้าเอกสาร")
        page_text = [page.get_text("text", sort=sort) for page in document]
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ExtractionError("อ่านข้อความจาก PDF ไม่สำเร็จ") from exc
    finally:
        document.close()

    full_text = "\n".join(page_text)
    if len(re.sub(r"\s+", "", full_text)) < 30:
        raise ExtractionError(
            "ไม่พบข้อความใน PDF ไฟล์นี้อาจเป็น Scanned PDF ที่ไม่มี Text Layer"
        )
    return page_text


def _reference_section(page_text: list[str]) -> str:
    first_candidate_page = max(0, int(len(page_text) * 0.5))
    heading_location: tuple[int, int] | None = None

    # Reference headings are usually near the end. Search backward so an earlier
    # mention of “references” in the body does not become the section boundary.
    for page_index in range(len(page_text) - 1, first_candidate_page - 1, -1):
        lines = page_text[page_index].splitlines()
        for line_index in range(len(lines) - 1, -1, -1):
            if _HEADING_RE.fullmatch(" ".join(lines[line_index].split())):
                heading_location = (page_index, line_index)
                break
        if heading_location:
            break

    if heading_location is None:
        raise ExtractionError(
            "ไม่พบหัวข้อ References, Bibliography หรือ Literature Cited ในช่วงท้ายเอกสาร"
        )

    start_page, start_line = heading_location
    section_lines: list[str] = []
    for page_index in range(start_page, len(page_text)):
        lines = page_text[page_index].splitlines()
        lines = lines[start_line + 1 :] if page_index == start_page else lines
        kept: list[str] = []
        for line in lines:
            if _END_HEADING_RE.fullmatch(" ".join(line.split())):
                return "\n".join(section_lines)
            kept.append(line)
        section_lines.extend(kept)
    return "\n".join(section_lines)


def _starts_reference(line: str) -> bool:
    return bool(_NUMBERED_RE.match(line) or _AUTHOR_YEAR_RE.match(line.strip()))


def _remove_numbering(text: str) -> str:
    return re.sub(r"^\s*(?:\[\d{1,4}\]|\(?\d{1,4}[.)])\s*", "", text).strip()


def _title_and_year(text: str) -> tuple[str, str | None]:
    cleaned = re.sub(r"\s+", " ", _remove_numbering(text)).strip()
    year_match = _YEAR_RE.search(cleaned)
    year = year_match.group(1) if year_match else None

    if year_match:
        tail = cleaned[year_match.end() :].lstrip(" )].,;:")
        # Author-date styles put the title right after the year. In numbered
        # styles, the title generally appears before the publication year.
        if year_match.start() < 120 and tail:
            candidate = re.split(r"\.\s+(?=[A-ZÀ-ÖØ-Þ])", tail, maxsplit=1)[0]
        else:
            parts = re.split(r"(?<=[a-z0-9)])\.\s+(?=[A-ZÀ-ÖØ-Þ])", cleaned)
            candidate = parts[1] if len(parts) > 1 else cleaned
    else:
        parts = re.split(r"(?<=[a-z0-9)])\.\s+(?=[A-ZÀ-ÖØ-Þ])", cleaned)
        candidate = parts[1] if len(parts) > 1 else cleaned

    candidate = candidate.strip(" \t\r\n.,;:()[]{}")
    candidate = re.sub(r"^(?:title:\s*)", "", candidate, flags=re.IGNORECASE)
    if len(candidate) > 300:
        candidate = candidate[:300].rsplit(" ", 1)[0]
    return candidate or cleaned[:300], year


def _split_entries(section: str) -> list[str]:
    entries: list[str] = []
    current: list[str] = []

    def finish() -> None:
        value = re.sub(r"\s+", " ", " ".join(current)).strip()
        if value:
            entries.append(value)
        current.clear()

    for raw_line in section.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            finish()
            continue
        if current and _starts_reference(line):
            finish()
        current.append(line)
    finish()

    # PDFs sometimes omit paragraph spacing and numbering. Filter page headers,
    # footers, and fragments that cannot plausibly be bibliographic entries.
    return [entry for entry in entries if len(entry) >= 20]


def _parse_references(page_text: list[str]) -> list[Reference]:
    section = _reference_section(page_text)
    entries = _split_entries(section)
    if not entries:
        raise ExtractionError("พบหัวข้อรายการอ้างอิง แต่ไม่พบรายการที่มีข้อมูลเพียงพอ")

    references: list[Reference] = []
    for entry in entries:
        title, year = _title_and_year(entry)
        references.append(Reference(original_text=entry, title=title, year=year))
    return references


def extract_references(pdf_bytes: bytes) -> list[Reference]:
    """Extract references and best-effort title/year fields from a PDF."""
    pages = _extract_text(pdf_bytes)
    try:
        return _parse_references(pages)
    except ExtractionError as visual_order_error:
        # In multi-column PDFs, visual sorting can merge a section heading with
        # adjacent text from the other column. Retry the PDF's content order,
        # which often keeps headings and bibliography entries intact.
        try:
            content_order_pages = _extract_text(pdf_bytes, sort=False)
            return _parse_references(content_order_pages)
        except ExtractionError:
            raise visual_order_error
