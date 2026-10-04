"""PDF text extraction and reference-list parsing."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf as fitz
from bibliography_parser import citation_fields, author_year_span, extract_doi


class ExtractionError(ValueError):
    """A PDF could not be read or did not contain a usable reference list."""


@dataclass(frozen=True)
class Reference:
    original_text: str
    title: str
    year: str | None
    number: int | None = None
    doi: str | None = None
    authors: tuple[str, ...] = ()


_HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.)]?\s+)?"
    r"(?:references|bibliography|works\s+cited|literature\s+cited|reference\s+list|"
    r"references\s+and\s+notes|références|referencias|literaturverzeichnis|เอกสารอ้างอิง|บรรณานุกรม)\s*:?\s*$",
    re.IGNORECASE,
)
_END_HEADING_RE = re.compile(
    r"^(?:appendix(?:\s+.*)?|author\s+biograph(?:y|ies)|biographical\s+notes|acknowledg(?:e)?ments|author\s+contributions|"
    r"supplementary\s+materials?)\s*:?\s*$",
    re.IGNORECASE,
)
_NUMBERED_RE = re.compile(r"^\s*(?:\[(\d{1,3})\]|\(?\d{1,3}[.)])\s+\S")
_NUMBERED_ENTRY_RE = re.compile(
    r"^\s*(?:\[(?P<bracket>\d{1,3})\]|\(?(?P<plain>\d{1,3})[.)])\s*(?P<text>\S.*)$"
)
_BRACKETED_ENTRY_RE = re.compile(r"^\s*\[(?P<number>\d{1,3})\]\s*(?P<text>\S.*)$")
_BIB_DEST_RE = re.compile(r"(?:bib|reference)(\d+)$", re.IGNORECASE)
_QUOTED_TITLE_RE = re.compile(r"[“\"‘]([^”\"’]{8,}?)[”\"’]")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s<>\]\[{}\"']+", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)((?:18|19|20)\d{2})([a-z])?(?!\d)")
_AUTHOR_YEAR_RE = re.compile(
    r"^(?:(?:van|von|de|del|da)\s+)?[A-ZÀ-ÖØ-Þ][^\n“”\"]{0,140}?(?:\(\s*)?(?:18|19|20)\d{2}[a-z]?\)?(?=[.,;:\s]|$)"
)


def _page_reading_order(page) -> str:
    """Read two-column text by geometric lines, retaining full-width bands."""
    lines = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(span.get('text', '') for span in line.get('spans', []))
            if text.strip():
                lines.append((fitz.Rect(line['bbox']), text))
    # Text coordinates are unrotated even when the displayed page is rotated.
    bounds = page.rect * page.derotation_matrix
    mid = bounds.width / 2
    left = [v for v in lines if v[0].x1 < mid + 12]
    right = [v for v in lines if v[0].x0 > mid - 12 and v not in left]
    if min(len(left), len(right)) < 5 or (len(left) + len(right)) < len(lines) * .65:
        return page.get_text('text', sort=True)
    overlap = min(max(v[0].y1 for v in left), max(v[0].y1 for v in right)) - max(min(v[0].y0 for v in left), min(v[0].y0 for v in right))
    if overlap < 50:
        return page.get_text('text', sort=True)
    wide = sorted([v for v in lines if v not in left and v not in right], key=lambda v: v[0].y0)
    result = []; remaining = left + right
    for boundary in wide + [(fitz.Rect(0, bounds.height + 1, 0, bounds.height + 1), '')]:
        before = [v for v in remaining if v[0].y0 < boundary[0].y0]
        for column in (left, right):
            result.extend(v[1] for v in sorted([v for v in before if v in column], key=lambda v: (v[0].y0, v[0].x0)))
        remaining = [v for v in remaining if v not in before]
        if boundary[1]:result.append(boundary[1])
    return '\n'.join(result)


def _extract_text(pdf_bytes: bytes, *, sort: bool = True) -> list[str]:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, ValueError, RuntimeError) as exc:
        raise ExtractionError("เปิดไฟล์ PDF ไม่ได้ หรือไฟล์เสียหาย") from exc

    try:
        if document.needs_pass:
            raise ExtractionError('PDF นี้มีรหัสผ่าน กรุณาปลดล็อกไฟล์ก่อนอัปโหลด')
        if document.page_count == 0:
            raise ExtractionError("ไฟล์ PDF ไม่มีหน้าเอกสาร")
        page_text = [_page_reading_order(page) if sort else page.get_text("text", sort=False) for page in document]
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


_BIO_START_RE = re.compile(r"^[A-Z][A-Za-z’'–-]+(?:\s+[A-Z][A-Za-z’'–-]+){1,4}\s+(?:received\s+(?:the|his|her)|is\s+currently)\b")
_LABEL_ONLY_RE = re.compile(r'(?:\[\d{1,4}\]|\(\d{1,3}\)|\d{1,3}[.)])')


def _clean_bibliography_lines(lines, repeated):
    kept = []
    pending_label = False
    last_number = 0
    for index, line in enumerate(lines):
        clean = ' '.join(line.split())
        if _END_HEADING_RE.fullmatch(clean) or _BIO_START_RE.match(clean) or re.match(r'^\*?\s*corresponding\s+author\s*:', clean, re.I):
            break
        if re.fullmatch(r'[A-Z]\.', clean):
            following = next((v.strip() for v in lines[index + 1:] if v.strip()), '')
            if re.fullmatch(r'[A-Z][A-Z -]{5,}', following) and 'PROOF' in following:
                break
        # Some journals label appendices only "A. TITLE", without "Appendix".
        # Require a following prose introduction so author initials remain safe.
        if re.fullmatch(r'[A-Z]\.\s+[^,;]{10,}', clean) and not _YEAR_RE.search(clean):
            following = next((v.strip() for v in lines[index + 1:] if v.strip()), '')
            if re.match(r'(?:This\s+appendix|In\s+this\s+appendix|Here\s+we)\b', following, re.I) or re.search(r'\bPROOF\b', clean):
                break
        if not clean:
            if kept and not pending_label:kept.append('')
            continue
        if clean.casefold() in repeated or _HEADING_RE.fullmatch(clean) or (re.fullmatch(r'\d{1,4}', clean) and not _YEAR_RE.fullmatch(clean)):
            continue
        if re.match(r'^\d{1,4}\s{2,}\S', line) or re.search(r'\s{2,}\d{1,4}\s*[·.]?\s*$', line):
            continue
        detached = _LABEL_ONLY_RE.fullmatch(clean)
        if detached and not clean.startswith('['):
            # A wrapped page-range or DOI suffix such as "680." / "1." is
            # not a new label unless it continues the actual reference sequence.
            detached = int(re.sub(r'\D', '', clean)) == last_number + 1
        if detached:
            kept.append(clean + ' ')
            pending_label = True
            last_number = int(re.sub(r'\D', '', clean))
        elif pending_label and not _NUMBERED_ENTRY_RE.match(clean):
            kept[-1] += clean
            pending_label = False
        else:
            kept.append(line)
            pending_label = False
            marker = _NUMBERED_ENTRY_RE.match(clean)
            if marker:
                last_number = int(marker.group('bracket') or marker.group('plain'))
    return '\n'.join(kept)


def _reference_section(page_text: list[str]) -> str:
    repeated = _repeated_page_lines(page_text)
    candidates = []
    for page_index, text in enumerate(page_text):
        for line_index, line in enumerate(text.splitlines()):
            if _HEADING_RE.fullmatch(' '.join(line.split())):
                tail = text.splitlines()[line_index + 1:] + '\n'.join(page_text[page_index+1:]).splitlines()
                section = _clean_bibliography_lines(tail, repeated)
                numbered = _split_numbered_entries(section, repeated)
                author_year = sum(bool(_AUTHOR_YEAR_RE.match(l.strip())) for l in section.splitlines())
                candidates.append(((len(numbered), author_year, page_index), section))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    # Heading-free journal formats: require a dense numbered, dated bibliography.
    start_page = max(0, len(page_text) - max(2, len(page_text)//3))
    tail = '\n'.join(page_text[start_page:])
    for match in re.finditer(r'(?m)^\s*(?:\[1\]|1[.)])\s*\S', tail):
        section = _clean_bibliography_lines(tail[match.start():].splitlines(), repeated)
        entries = _split_numbered_entries(section, repeated)
        if len(entries) >= 3 and sum(bool(_YEAR_RE.search(entry) or _DOI_RE.search(entry)) for _, entry in entries) >= len(entries)*.6:
            return section
    raise ExtractionError('ไม่พบหัวข้อหรือรายการบรรณานุกรมที่ยืนยันได้ใน PDF นี้')


def _starts_reference(line: str) -> bool:
    return bool(_NUMBERED_RE.match(line) or _AUTHOR_YEAR_RE.match(line.strip()))


def _remove_numbering(text: str) -> str:
    return re.sub(r"^\s*(?:\[\d{1,4}\]|\(?\d{1,4}[.)])\s*", "", text).strip()


def _title_and_year(text: str) -> tuple[str, str | None]:
    title, year, _ = citation_fields(text)
    return title, year


def _split_entries(section: str) -> list[str]:
    entries: list[str] = []
    current: list[str] = []

    def finish() -> None:
        value = re.sub(r"\s+", " ", " ".join(current)).strip()
        if value:
            entries.append(value)
        current.clear()

    lines = section.splitlines()
    protected_until = -1
    for index, raw_line in enumerate(lines):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            upcoming = next((i for i in range(index + 1, len(lines)) if lines[i].strip()), None)
            if upcoming is None or author_year_span(lines, upcoming) is not None:
                finish()
            continue
        embedded_editors = current and re.search(r'\bIn\s+(?:[A-Z]\.\s*)*$', current[-1])
        span = author_year_span(lines, index) if index > protected_until and not embedded_editors else None
        marker = _NUMBERED_ENTRY_RE.match(line)
        numbered_start = marker and (marker.group('bracket') or (
            marker.group('text')[:1].isalpha() and int(marker.group('plain')) == len(entries) + (2 if current else 1)
        ))
        if current and (span is not None or numbered_start):
            finish()
        current.append(line)
        if span is not None:
            protected_until = span
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
        if numbers[0] != 1 or len(numbers) / numbers[-1] < 0.75:
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
        if match and match.group('plain') and re.match(r'(?:https?://|doi\s*:|PMID\s*:)', match.group('text'), re.I):
            # "480. https://doi.org/..." continues a wrapped page range.
            match = None
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
    short_dated = len(numbers) <= 2 and all(v[:1].isalpha() and (_YEAR_RE.search(v) or _DOI_RE.search(v)) for v in entries.values())
    if numbers[0] != 1 or (len(numbers) < 3 and not short_dated) or len(numbers) / numbers[-1] < 0.75:
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
        title, year, authors = citation_fields(entry)
        doi = extract_doi(entry)
        references.append(
            Reference(original_text=entry, title=title, year=year, number=number, doi=doi, authors=authors)
        )
    return references


def extract_references(pdf_bytes: bytes) -> list[Reference]:
    """Extract references and best-effort title/year fields from a PDF."""
    return extract_references_with_diagnostics(pdf_bytes)[0]


def extract_references_with_diagnostics(pdf_bytes: bytes) -> tuple[list[Reference], dict]:
    """Code extraction with explicit numbered coverage; never an AI request."""
    candidates, error = _bibliography_candidates(pdf_bytes)
    if not candidates:
        raise error
    selected = max(candidates, key=lambda item: item[0])
    references = _parse_references(selected[2])
    labels = numbered_bibliography_entries(selected[1])
    expected = max(labels, default=0)
    gaps = sorted(set(range(1, expected + 1)) - labels.keys())
    warnings = []
    if gaps:
        warnings.append('ยังอ่านข้อความอ้างอิงบางหมายเลขไม่ได้: ' + ', '.join(map(str, gaps)) + ' กรุณาเทียบ PDF ต้นฉบับ')
    if not labels:
        warnings.append('รายการอ้างอิงไม่มีลำดับเลขที่ยืนยันได้ จึงยังตรวจความครบอัตโนมัติไม่ได้ กรุณาเทียบต้นฉบับ')
    return references, {
        'extraction_method': 'Code extraction — ไม่ใช้ AI',
        'extraction_warnings': warnings,
        'expected_reference_count': expected or None,
        'extraction_complete': bool(labels) and not gaps and len(references) == expected,
        'parsed_title_count': sum(bool(r.title) for r in references),
        'parsed_author_count': sum(bool(r.authors) for r in references),
    }


def _bibliography_candidates(pdf_bytes):
    # Prefer native order on a tie; geometric order repairs interleaved columns.
    candidates = []
    error = ExtractionError('ไม่พบบรรณานุกรมที่อ่านได้')
    for sort in (False, True):
        pages = _extract_text(pdf_bytes, sort=sort)
        try:
            text = _reference_section(pages)
            numbered = _split_numbered_entries(text, _repeated_page_lines(pages))
            starts = sum(bool(_AUTHOR_YEAR_RE.match(v.strip())) for v in text.splitlines())
            entries = _split_entries(text)
            dated = sum(bool(_YEAR_RE.search(v)) for v in entries)
            candidates.append(((len(numbered), starts, dated - len(entries), dated), text, pages))
        except ExtractionError as exc:
            error = exc
    return candidates, error


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


def extract_bibliography_text(pdf_bytes: bytes, max_chars: int = 120000) -> str:
    """Choose the most complete bounded bibliography across reading orders."""
    candidates, _ = _bibliography_candidates(pdf_bytes)
    if candidates:
        text = max(candidates, key=lambda item:item[0])[1]
    else:
        pages = _extract_text(pdf_bytes, sort=False)
        text = '\n'.join(pages[max(0,len(pages)-max(2,len(pages)//3)):])
    if len(text) > max_chars:
        raise ExtractionError('บรรณานุกรมยาวเกินขีดจำกัด AI กรุณาแนบ PDF เฉพาะหน้าบรรณานุกรม')
    return text


def numbered_bibliography_entries(text: str) -> dict[int, str]:
    """Unambiguous source entries, for AI coverage checks and raw-text recovery."""
    return dict(_split_numbered_entries(text, set()))


def extract_citation_contexts(pdf_bytes: bytes, references: list[Reference]) -> dict[int, list[dict]]:
    """Best-effort textual markers and internal-link positions; never bibliography text."""
    pages = _extract_text(pdf_bytes)
    contexts = {ref.number or i: [] for i, ref in enumerate(references, 1)}
    body_pages = []
    for page in pages:
        lines = page.splitlines()
        heading = next((i for i, line in enumerate(lines) if _HEADING_RE.fullmatch(' '.join(line.split()))), None)
        body_pages.append(' '.join(lines[:heading] if heading is not None else lines))
        if heading is not None:
            break
    def add(number, page, text, start, end, method):
        values = contexts.get(number)
        if values is None or len(values) >= 3:
            return
        snippet = text[max(0, start - 250):min(len(text), end + 250)].strip()
        if snippet and not any(v['text'] == snippet for v in values):
            values.append({'page': page, 'text': snippet, 'method': method})
    for page_number, text in enumerate(body_pages, 1):
        for marker in re.finditer(r'\[([\d\s,;–—-]+)\]', text):
            numbers = set()
            for part in re.split(r'[,;]', marker.group(1)):
                bounds = re.findall(r'\d+', part)
                if len(bounds) == 1:
                    numbers.add(int(bounds[0]))
                elif len(bounds) == 2 and 0 <= int(bounds[1]) - int(bounds[0]) <= 100:
                    numbers.update(range(int(bounds[0]), int(bounds[1]) + 1))
            for number in numbers:
                add(number, page_number, text, marker.start(), marker.end(), 'numbered-marker')
        for i, ref in enumerate(references, 1):
            if ref.number is None and ref.year:
                author = re.search(r'[A-Za-zÀ-ÖØ-öø-ÿ]{3,}', ref.original_text)
                if author:
                    pattern = re.escape(author.group()) + r'[^.;]{0,80}?\b' + re.escape(ref.year) + r'\b'
                    for marker in re.finditer(pattern, text, re.IGNORECASE):
                        add(i, page_number, text, marker.start(), marker.end(), 'author-year-heuristic')
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        for page_index, page in enumerate(doc):
            if page_index >= len(body_pages):
                break
            for link in page.get_links():
                match = _BIB_DEST_RE.search(link.get('nameddest') or '')
                if match and link.get('from'):
                    rect = fitz.Rect(link['from'])
                    rect.x0 = 0; rect.x1 = page.rect.width
                    rect.y0 = max(0, rect.y0 - 35); rect.y1 = min(page.rect.height, rect.y1 + 35)
                    text = ' '.join(page.get_text('text', clip=rect).split())
                    if text and text in body_pages[page_index]:
                        add(int(match.group(1)), page_index + 1, text, 0, len(text), 'internal-link')
    return contexts


def split_bibliography_batches(text: str, max_chars: int = 6000, max_entries: int = 16, *, preserve_entries: bool = False) -> list[str]:
    """Pack whole entries when recognizable; overlap unstructured long fragments."""
    bracketed = list(re.finditer(r'(?m)^\s*\[\d{1,4}\]\s*\S', text))
    plain = list(re.finditer(r'(?m)^\s*\(?\d{1,3}[.)]\s+\S', text))
    markers = bracketed if len(bracketed) >= 2 or preserve_entries and bracketed else plain
    recognizable = len(markers) >= 2 or preserve_entries and bool(markers)
    if recognizable:
        starts = [0] + [m.start() for m in markers if m.start() > 0]
        units = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    else:
        units = re.split(r'\n\s*\n', text)
    chunks = []
    current = []
    size = 0
    for unit in units:
        if not unit.strip():
            continue
        if len(unit) > max_chars:
            if current:
                chunks.append('\n'.join(current)); current = []; size = 0
            if preserve_entries and recognizable:
                if len(unit) > 20000:
                    raise ExtractionError('รายการอ้างอิงหนึ่งช่วงยาวเกิน 20,000 ตัวอักษร โหมดประหยัดไม่ตัดรายการทิ้ง กรุณาแนบเฉพาะบรรณานุกรมหรือใช้โหมดปกติ')
                chunks.append(unit)
                continue
            offset = 0
            while offset < len(unit):
                end = min(offset + max_chars, len(unit))
                if end < len(unit):
                    boundary = unit.rfind('\n', offset + max_chars // 2, end)
                    if boundary > offset:
                        end = boundary
                chunks.append(unit[offset:end])
                if end == len(unit):
                    break
                offset = end - 350  # Preserve citations spanning a layout boundary.
        else:
            if current and (size + len(unit) + 1 > max_chars or len(current) >= max_entries):
                chunks.append('\n'.join(current)); current = []; size = 0
            current.append(unit); size += len(unit) + 1
    if current:
        chunks.append('\n'.join(current))
    return chunks


def extract_source_metadata(pdf_bytes: bytes) -> dict:
    """Read explicit PDF metadata in memory; unknown authors/year remain unknown."""
    try:
        with fitz.open(stream=pdf_bytes, filetype='pdf') as document:
            metadata = document.metadata or {}
            title = (metadata.get('title') or '').strip()
            if title.lower() in ('untitled', 'microsoft word'):
                title = ''
            authors = [name.strip() for name in re.split(r';', metadata.get('author') or '') if name.strip()]
            return {'title': title[:1200], 'authors': authors[:40], 'year': '', 'doi': None}
    except (ValueError, RuntimeError):
        return {'title': '', 'authors': [], 'year': '', 'doi': None}
