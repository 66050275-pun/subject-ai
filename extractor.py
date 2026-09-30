"""PDF text extraction and reference-list parsing."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf as fitz


class ExtractionError(ValueError):
    """A PDF could not be read or did not contain a usable reference list."""


@dataclass(frozen=True)
class Reference:
    original_text: str
    title: str
    year: str | None
    number: int | None = None
    doi: str | None = None


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
_NUMBERED_RE = re.compile(r"^\s*(?:\[(\d{1,3})\]|\(?\d{1,3}[.)])\s+\S")
_NUMBERED_ENTRY_RE = re.compile(
    r"^\s*(?:\[(?P<bracket>\d{1,3})\]|\(?(?P<plain>\d{1,3})[.)])\s+(?P<text>\S.*)$"
)
_BRACKETED_ENTRY_RE = re.compile(r"^\s*\[(?P<number>\d{1,3})\]\s+(?P<text>\S.*)$")
_BIB_DEST_RE = re.compile(r"(?:bib|reference)(\d+)$", re.IGNORECASE)
_QUOTED_TITLE_RE = re.compile(r"[“\"‘]([^”\"’]{8,}?)[”\"’]")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s<>\]\[{}\"']+", re.IGNORECASE)
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

    quoted_title = _QUOTED_TITLE_RE.search(cleaned)
    if quoted_title:
        candidate = quoted_title.group(1)
    elif year_match:
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
    # A failed parse often lands on a journal abbreviation, page range, or
    # article number. Keep those citations intact, but don't present metadata as
    # if it were a paper title.
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", candidate)
    if (not quoted_title and len(words) < 4) or re.search(
        r"(?:https?://|10\.\d{4,9}/|\b(?:vol(?:ume)?|pp?|pages?)\.?\s*\d|\b\d{2,4}\s*[-–—]\s*\d{2,4}\b)",
        candidate,
        re.IGNORECASE,
    ):
        candidate = ""
    return candidate, year


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


def _repeated_page_lines(page_text: list[str]) -> set[str]:
    """Find repeated running headers and footers to skip while parsing entries."""
    page_line_counts: Counter[str] = Counter()
    for page in page_text:
        page_line_counts.update(
            {" ".join(line.split()).casefold() for line in page.splitlines() if line.strip()}
        )
    minimum_repetitions = max(3, (len(page_text) + 3) // 4)
    return {
        line for line, count in page_line_counts.items() if count >= minimum_repetitions
    }


def _split_numbered_entries(
    section: str, repeated_lines: set[str]
) -> list[tuple[int, str]]:
    """Join wrapped numbered references, even when PDF layout inserts blanks."""
    def collect(pattern: re.Pattern[str], *, bracketed_only: bool) -> list[tuple[int, str]]:
        entries: dict[int, str] = {}
        current_number: int | None = None
        current: list[str] = []

        def finish() -> None:
            nonlocal current_number
            value = re.sub(r"\s+", " ", " ".join(current)).strip()
            if current_number is not None and len(value) >= 20:
                entries.setdefault(current_number, value)
            current_number = None
            current.clear()

        for raw_line in section.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line or line.casefold() in repeated_lines:
                continue
            match = pattern.match(line)
            if match:
                finish()
                number = match.group("number") if bracketed_only else (
                    match.group("bracket") or match.group("plain")
                )
                current_number = int(number)
                current.append(match.group("text"))
            elif current_number is not None:
                current.append(line)
        finish()

        if not entries:
            return []
        numbers = sorted(entries)
        if numbers[0] != 1 or len(numbers) < 3 or len(numbers) / numbers[-1] < 0.75:
            return []
        return [(number, entries[number]) for number in numbers]

    # Bracketed labels are unambiguous. Prefer them so a wrapped journal date
    # such as "(2023)" cannot be mistaken for a new reference number.
    bracketed = collect(_BRACKETED_ENTRY_RE, bracketed_only=True)
    if bracketed:
        return bracketed

    entries: dict[int, str] = {}
    current_number: int | None = None
    current: list[str] = []

    def finish() -> None:
        nonlocal current_number
        value = re.sub(r"\s+", " ", " ".join(current)).strip()
        if current_number is not None and len(value) >= 20:
            entries.setdefault(current_number, value)
        current_number = None
        current.clear()

    for raw_line in section.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line.casefold() in repeated_lines:
            continue
        match = _NUMBERED_ENTRY_RE.match(line)
        if match:
            finish()
            number = match.group("bracket") or match.group("plain")
            current_number = int(number)
            current.append(match.group("text"))
        elif current_number is not None:
            current.append(line)
    finish()

    if not entries:
        return []
    numbers = sorted(entries)
    # Prefer numbered parsing only when the section has a substantial,
    # mostly consecutive sequence. This avoids mistaking short numbered lists
    # in an unnumbered bibliography for reference numbers.
    if numbers[0] != 1 or len(numbers) < 3 or len(numbers) / numbers[-1] < 0.75:
        return []
    return [(number, entries[number]) for number in numbers]


def _parse_references(page_text: list[str]) -> list[Reference]:
    section = _reference_section(page_text)
    numbered_entries = _split_numbered_entries(section, _repeated_page_lines(page_text))
    if numbered_entries:
        entries = numbered_entries
    else:
        entries = list(enumerate(_split_entries(section), start=1))
    if not entries:
        raise ExtractionError("พบหัวข้อรายการอ้างอิง แต่ไม่พบรายการที่มีข้อมูลเพียงพอ")

    references: list[Reference] = []
    for number, entry in entries:
        title, year = _title_and_year(entry)
        doi_match = _DOI_RE.search(entry)
        doi = doi_match.group(0).rstrip(".,;:)") if doi_match else None
        references.append(
            Reference(original_text=entry, title=title, year=year, number=number, doi=doi)
        )
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


def extract_summary_text(pdf_bytes: bytes, *, max_chars: int = 18_000) -> str:
    """Extract bounded body text, omitting the bibliography when detectable."""
    pages = _extract_text(pdf_bytes)
    text = "\n".join(pages)
    lines = text.splitlines()
    heading_at: int | None = None
    for index, line in enumerate(lines):
        if _HEADING_RE.fullmatch(" ".join(line.split())):
            heading_at = index
    if heading_at is not None:
        text = "\n".join(lines[:heading_at])
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ExtractionError("ไม่พบข้อความเนื้อหาสำหรับสรุป")
    if len(text) > max_chars:
        marker = "\n\n[...ตัดเนื้อหาส่วนกลางเพื่อจำกัดค่าใช้จ่าย...]\n\n"
        body_size = max(0, max_chars - len(marker))
        head_size = int(body_size * 0.72)
        tail_size = body_size - head_size
        text = text[:head_size] + marker + text[-tail_size:]
    return text


def extract_citation_counts(
    pdf_bytes: bytes, valid_reference_numbers: set[int]
) -> tuple[bool, dict[int, int]]:
    """Count PDF internal links that point to numbered bibliography entries."""
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, ValueError, RuntimeError):
        return False, {}

    counts: Counter[int] = Counter()
    has_bibliography_links = False
    try:
        for page in document:
            for link in page.get_links():
                destination = link.get("nameddest") or ""
                match = _BIB_DEST_RE.search(destination)
                if not match:
                    continue
                number = int(match.group(1))
                if number in valid_reference_numbers:
                    has_bibliography_links = True
                    counts[number] += 1
    except (fitz.FileDataError, RuntimeError):
        return False, {}
    finally:
        document.close()

    return has_bibliography_links, dict(counts)
