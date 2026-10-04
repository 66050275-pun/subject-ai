"""Academic metadata lookup with DOI-first and citation-text fallbacks."""

from __future__ import annotations

import asyncio
import html
import os
import re
import time
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import quote, quote_plus, unquote, urlsplit

import httpx

from extractor import Reference
from bibliography_parser import citation_fields, is_author_prefix, normalize_text, VENUE
from open_access import OADiscovery, location, merge_locations, openalex_locations, openalex_records, safe_url

REQUEST_TIMEOUT_SECONDS = 8.0
MAX_CONCURRENT_LOOKUPS = 8
CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper"
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def normalize_doi(value: str) -> str:
    """Accept a DOI, doi: value, or DOI URL and return its identifier."""
    doi = value.strip()
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = unquote(doi).strip().rstrip(".,;:)]}")
    return doi


def validate_doi(value: str) -> str:
    doi = normalize_doi(value)
    if not DOI_PATTERN.fullmatch(doi):
        raise ValueError("กรุณากรอก DOI เช่น 10.1016/j.jpowsour.2024.234567")
    return doi


def _headers() -> dict[str, str]:
    user_agent = "PaperRefFinder/1.1 (academic reference resolver)"
    contact = os.getenv("PAPERREF_CONTACT_EMAIL", "").strip()
    if contact:
        user_agent += f" mailto:{contact}"
    return {"User-Agent": user_agent, "Accept": "application/json"}


def _normalize(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


async def _academic_get(client, url, **kwargs):
    """Optional academic API credentials go only to their own fixed API host."""
    host = urlsplit(url).hostname
    if host == 'api.crossref.org':
        contact = os.getenv('PAPERREF_CONTACT_EMAIL', '').strip()
        if contact:
            kwargs['params'] = {**(kwargs.get('params') or {}), 'mailto': contact}
    if host == 'api.openalex.org':
        key = os.getenv('OPENALEX_API_KEY', '').strip()
        if key:
            kwargs['params'] = {**(kwargs.get('params') or {}), 'api_key': key}
    elif host == 'api.semanticscholar.org':
        key = os.getenv('SEMANTIC_SCHOLAR_API_KEY', '').strip()
        if key:
            kwargs['headers'] = {**(kwargs.get('headers') or {}), 'x-api-key': key}
    # One request-scoped cache/gate; no user metadata survives the client lifetime.
    state = getattr(client, '_paperref_academic', None)
    if state is None:
        state = {'cache': {}, 'blocked': {}, 'gates': {}, 'next_request': {}}
        client._paperref_academic = state
    key = (url, tuple(sorted((str(k), str(v)) for k, v in (kwargs.get('params') or {}).items())))
    if key not in state['cache']:
        async def fetch():
            gate = state['gates'].setdefault(host, asyncio.Semaphore(1))
            async with gate:
                if host in state['blocked']:
                    return state['blocked'][host]
                interval = 1.5 if host == 'api.semanticscholar.org' else 1.0
                wait = state['next_request'].get(host, 0) - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                state['next_request'][host] = time.monotonic() + interval
                response = await client.get(url, **kwargs)
                if response.status_code in (401, 403, 429):
                    state['blocked'][host] = response
                return response
        if len(state['cache']) >= 512:
            for old_key, task in list(state['cache'].items()):
                if task.done():
                    del state['cache'][old_key]
                    break
        state['cache'][key] = asyncio.create_task(fetch())
    return await state['cache'][key]


def _usable_title(value: str | None) -> bool:
    if not value:
        return False
    if is_author_prefix(value) and (re.search(r'\b[A-Z]\.\s*|[,;]|\bet al\b', value)):
        return False
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", value)
    return len(words) >= 3 and not re.search(
        r"(?:https?://|10\.\d{4,9}/|\b(?:vol(?:ume)?|pp?|pages?)\.?\s*\d|\b\d{2,4}\s*[-–—]\s*\d{2,4}\b)",
        value,
        re.IGNORECASE,
    )


def _title_similarity(reference_title: str, candidate_title: str) -> float:
    reference = _normalize(reference_title)
    candidate = _normalize(candidate_title)
    if len(reference) < 8 or len(candidate) < 8:
        return 0.0
    return SequenceMatcher(None, reference, candidate).ratio()


def _title_matches(reference_title: str, candidate_title: str) -> bool:
    return _title_similarity(reference_title, candidate_title) >= 0.88


def _safe_http_url(value: str | None) -> str | None:
    return safe_url(value)


def _first_author_family(citation: str) -> str:
    citation = normalize_text(citation)
    first = re.sub(r"^\s*\[\d+\]\s*", "", citation).split(",", 1)[0].strip()
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][\w’'-]*", first)
    if len(tokens) > 1 and all(len(t) == 1 for t in tokens[1:]):
        return tokens[0].casefold()  # Surname-first Vancouver/APA.
    return tokens[-1].casefold() if tokens else ""


def _crossref_year(item: dict | None) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("published-print", "published-online", "issued", "created"):
        date = item.get(key)
        if not isinstance(date, dict):
            continue
        parts = date.get("date-parts") or []
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            return str(parts[0][0])
    return None


def _crossref_author_matches(item: dict, citation: str) -> bool:
    family = _first_author_family(citation)
    authors = item.get("author") or []
    return bool(family and any(
        isinstance(author, dict) and _first_author_family(str(author.get("family") or '')) == family for author in authors
    ))


def _coordinates_match(reference: Reference, metadata: dict) -> bool:
    """Require bibliographic coordinates for references which omit the title."""
    citation = normalize_text(reference.original_text)
    volume = str(metadata.get('volume') or '').strip()
    page = str(metadata.get('page') or metadata.get('article-number') or '').strip()
    if not volume or volume == reference.year:
        return False
    # Compare the first page/article identifier, including alphanumeric pages.
    first_page = re.split(r'\s*[-–—]\s*', page)[0]
    if not first_page:
        return False
    # First page must follow this volume/date/issue; the last page is not a match.
    pattern = (r'(?<![\w\[(])' + re.escape(volume) + r'(?![\w\])])\s*'
               r'(?:\([\w\s,–-]+\)\s*){0,2}'
               r'(?:(?:18|19|20)\d{2}\s*)?[,;:]?\s*(?:pp?\.?\s*)?'
               + re.escape(first_page) + r'(?!\w)')
    return bool(re.search(pattern, citation, re.I))


def _candidate_title(value) -> bool:
    return bool(isinstance(value, str) and re.search(r'[^\W\d_]{3}', value)
                and not VENUE.match(value)
                and not (is_author_prefix(value) and re.search(r'\b[A-Z]\.\s*|[,;]|\bet al\b', value)))


def _journal_matches(citation: str, journal) -> bool:
    if not isinstance(journal, str) or not journal.strip():
        return True  # Some APIs omit the venue; other identity fields remain required.
    generic = {'journal', 'of', 'the', 'and', 'j', 'proceedings', 'proc'}
    words = [w.casefold() for w in re.findall(r'[^\W\d_]+', journal) if len(w) >= 3 and w.casefold() not in generic]
    printed = [w.casefold() for w in re.findall(r'[^\W\d_]+', normalize_text(citation))]
    return bool(words and all(any(w.startswith(p) or p.startswith(w) for p in printed if len(p) >= 3) for w in words))


def _clean_reference(reference: Reference) -> Reference:
    """A citation or author list is not a usable paper title."""
    printed_title, printed_year, authors = citation_fields(reference.original_text)
    title = reference.title or ''
    normalized = _normalize(title)
    names = _normalize(' '.join(authors))
    if normalized and ((normalized == _normalize(reference.original_text) and (authors or printed_year)) or normalized == names
                       or VENUE.match(title)
                       or (is_author_prefix(title) and re.search(r'\b[A-Z]\.\s*|[,;]|\bet al\b', title))):
        title = printed_title
    if not title:
        title = printed_title
    return replace(reference, title=title, year=reference.year or printed_year, authors=reference.authors or authors)


def _metadata_matches(reference: Reference, metadata: dict) -> bool:
    title = str(metadata.get('title') or '')
    year = str(metadata.get('year') or '')
    if not _candidate_title(title) or reference.year and year and reference.year != year:
        return False
    if _usable_title(reference.title):
        return _title_matches(reference.title, title)
    if len(_normalize(title)) >= 15 and _normalize(title) in _normalize(reference.original_text):
        return True
    family = _first_author_family(reference.original_text)
    def author_family(name):
        name = str(name).split(',', 1)[0]
        return _first_author_family(name)
    return bool(reference.year and reference.year == year and family
                and any(author_family(a) == family for a in metadata.get('authors') or [])
                and _coordinates_match(reference, metadata) and _journal_matches(reference.original_text, metadata.get('journal')))


def _good_crossref_match(item: dict, reference: Reference) -> bool:
    titles = item.get("title") or []
    matched_title = titles[0] if titles else ""
    if not _candidate_title(matched_title):
        return False
    if _usable_title(reference.title) and _title_similarity(reference.title, matched_title) >= 0.88:
        return not reference.year or _crossref_year(item) in (None, reference.year)

    full_citation = _normalize(reference.original_text)
    normalized_title = _normalize(matched_title)
    if len(normalized_title) >= 15 and normalized_title in full_citation:
        return not reference.year or _crossref_year(item) in (None, reference.year)
    if _usable_title(reference.title):
        return False

    try:
        score = float(item.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    same_year = bool(reference.year and _crossref_year(item) == reference.year)
    # Some bibliography formats omit article titles entirely. Crossref's
    # bibliographic score plus the year and first author can still identify it.
    return bool(matched_title and score >= 35 and same_year
                and _crossref_author_matches(item, reference.original_text) and _coordinates_match(reference, item)
                and _journal_matches(reference.original_text, (item.get('container-title') or [''])[0]))


async def _crossref_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await _academic_get(client, f"{CROSSREF_URL}/{quote(doi, safe='')}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get("message")


async def _crossref_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    if reference.doi:
        try:
            direct = await _crossref_by_doi(client, reference.doi)
            if direct:
                return direct
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
    response = await _academic_get(client,
        CROSSREF_URL,
        params={"query.bibliographic": reference.original_text, "rows": 8},
    )
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    matches = [item for item in items if _good_crossref_match(item, reference)]
    if not matches:
        return None
    if not _usable_title(reference.title):
        identities = {normalize_doi(str(item.get('DOI') or '')) or _normalize(str((item.get('title') or [''])[0])) for item in matches}
        if len(identities) > 1:
            return None  # Two distinct records satisfy the citation: leave unresolved.
    return max(matches, key=lambda item: float(item.get("score") or 0))


async def _openalex_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    search = reference.title if _usable_title(reference.title) else reference.original_text
    response = await _academic_get(client,
        OPENALEX_URL,
        params={"search": search, "per-page": 5},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    matches = [work for work in results if isinstance(work, dict) and _metadata_matches(reference, {
        'title': work.get('display_name'), 'year': work.get('publication_year'),
        'authors': [(a.get('author') or {}).get('display_name', '') for a in work.get('authorships') or [] if isinstance(a, dict) and isinstance(a.get('author'), dict)],
        'volume': (work.get('biblio') or {}).get('volume'), 'page': (work.get('biblio') or {}).get('first_page'),
        'journal': ((work.get('primary_location') or {}).get('source') or {}).get('display_name'),
    })]
    if not matches:
        return None
    if not _usable_title(reference.title) and len({w.get('id') or _normalize(w.get('display_name') or '') for w in matches}) > 1:
        return None
    work = max(
        matches,
        key=lambda item: _title_similarity(reference.title, item.get("display_name") or ""),
    )
    locations = [row for row in openalex_locations(work) if row]
    pdf_url = locations[0]['url'] if locations else None
    raw_doi = work.get("doi")
    doi = normalize_doi(raw_doi) if raw_doi else None
    return {
        "title": work.get("display_name"),
        "doi": doi,
        "paper_url": _safe_http_url(raw_doi) or _safe_http_url(work.get("id")),
        "record_url": _safe_http_url(work.get("id")),
        "oa_pdf_url": _safe_http_url(pdf_url),
        "pdf_locations": locations,
        "oa_records": openalex_records(work),
        "year": str(work.get("publication_year") or "") or None,
        "authors": [(entry.get('author') or {}).get('display_name', '') for entry in (work.get('authorships') or []) if isinstance(entry, dict) and isinstance(entry.get('author'), dict)][:40],
        "abstract": _openalex_abstract(work),
    }


async def _openalex_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await _academic_get(client,
        OPENALEX_URL,
        params={"filter": f"doi:https://doi.org/{doi}", "per-page": 1},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return None
    work = results[0]
    if not isinstance(work, dict):
        return None
    locations = [row for row in openalex_locations(work) if row]
    pdf_url = locations[0]['url'] if locations else None
    return {
        "title": work.get("display_name"),
        "doi": normalize_doi(work.get("doi") or doi),
        "paper_url": _safe_http_url(work.get("doi")) or _safe_http_url(work.get("id")),
        "record_url": _safe_http_url(work.get("id")),
        "oa_pdf_url": _safe_http_url(pdf_url),
        "pdf_locations": locations,
        "oa_records": openalex_records(work),
        "year": str(work.get("publication_year") or "") or None,
        "authors": [(entry.get('author') or {}).get('display_name', '') for entry in (work.get('authorships') or []) if isinstance(entry, dict) and isinstance(entry.get('author'), dict)][:40],
        "abstract": _openalex_abstract(work),
    }


async def _semantic_scholar_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await _academic_get(client,
        f"{SEMANTIC_SCHOLAR_URL}/{quote('DOI:' + doi, safe=':')}",
        params={"fields": "title,openAccessPdf,externalIds,url,paperId,year,authors,abstract"},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    paper = response.json()
    pdf = paper.get("openAccessPdf") or {}
    paper_url = _safe_http_url(paper.get("url"))
    if not paper_url and paper.get("paperId"):
        paper_url = f"https://www.semanticscholar.org/paper/{paper['paperId']}"
    return {
        "title": paper.get("title"),
        "doi": normalize_doi((paper.get("externalIds") or {}).get("DOI") or doi),
        "paper_url": paper_url or f"https://doi.org/{doi}",
        "record_url": paper_url,
        "oa_pdf_url": _safe_http_url(pdf.get("url")),
        "arxiv_id": (paper.get('externalIds') or {}).get('ArXiv'),
        "year": str(paper.get("year") or "") or None,
        "authors": [author.get('name', '') for author in (paper.get('authors') or []) if isinstance(author, dict)][:40],
        "abstract": str(paper.get('abstract') or ''),
    }


async def _semantic_scholar_lookup(
    client: httpx.AsyncClient, reference: Reference
) -> dict | None:
    response = await _academic_get(client,
        f"{SEMANTIC_SCHOLAR_URL}/search",
        params={
            "query": reference.title if _usable_title(reference.title) else reference.original_text,
            "limit": 5,
            "fields": "title,openAccessPdf,externalIds,url,paperId,year,authors,abstract,journal",
        },
    )
    response.raise_for_status()
    papers = response.json().get("data", [])
    matches = [paper for paper in papers if isinstance(paper, dict) and _metadata_matches(reference, {
        'title': paper.get('title'), 'year': paper.get('year'),
        'authors': [a.get('name', '') for a in paper.get('authors') or [] if isinstance(a, dict)],
        'volume': (paper.get('journal') or {}).get('volume'), 'page': (paper.get('journal') or {}).get('pages'),
        'journal': (paper.get('journal') or {}).get('name'),
    })]
    if not matches:
        return None
    if not _usable_title(reference.title) and len({p.get('paperId') or _normalize(p.get('title') or '') for p in matches}) > 1:
        return None
    paper = max(
        matches,
        key=lambda item: _title_similarity(reference.title, item.get("title") or ""),
    )
    pdf = paper.get("openAccessPdf") or {}
    doi = (paper.get("externalIds") or {}).get("DOI")
    paper_url = _safe_http_url(paper.get("url"))
    if not paper_url and paper.get("paperId"):
        paper_url = f"https://www.semanticscholar.org/paper/{paper['paperId']}"
    return {
        "title": paper.get("title"),
        "doi": normalize_doi(doi) if doi else None,
        "paper_url": paper_url or (f"https://doi.org/{doi}" if doi else None),
        "record_url": paper_url,
        "oa_pdf_url": _safe_http_url(pdf.get("url")),
        "arxiv_id": (paper.get('externalIds') or {}).get('ArXiv'),
        "year": str(paper.get("year") or "") or None,
        "authors": [author.get('name', '') for author in (paper.get('authors') or []) if isinstance(author, dict)][:40],
        "abstract": str(paper.get('abstract') or ''),
    }


async def _google_scholar_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    """Optional official SerpAPI integration; never scrape Scholar HTML/CAPTCHAs."""
    key = os.getenv('SERPAPI_API_KEY', '').strip()
    if not key:
        return None
    response = await _academic_get(client, 'https://serpapi.com/search.json', params={
        'engine': 'google_scholar', 'q': reference.original_text or reference.title,
        'num': 5, 'api_key': key,
    })
    response.raise_for_status()
    body = response.json()
    if body.get('error'):
        raise ValueError('Scholar search unavailable')
    matches = []
    for row in body.get('organic_results') or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get('title') or '')
        link = _safe_http_url(row.get('link'))
        # Scholar snippets alone often lack volume/pages. Verify linked DOI records.
        doi_match = re.search(r'10\.\d{4,9}/[^\s?#]+', unquote(link or ''), re.I)
        if doi_match:
            candidate = await _crossref_by_doi(client, normalize_doi(doi_match.group()))
            if candidate and _good_crossref_match({**candidate, 'score': 100}, reference):
                matches.append({'title': (candidate.get('title') or [title])[0], 'doi': candidate.get('DOI'),
                                'year': _crossref_year(candidate), 'paper_url': link, 'record_url': link})
        elif _usable_title(reference.title) and _title_matches(reference.title, title):
            summary = str((row.get('publication_info') or {}).get('summary') or '')
            if not reference.year or re.search(r'(?<!\d)' + re.escape(reference.year) + r'(?!\d)', summary):
                matches.append({'title': title, 'paper_url': link, 'record_url': link})
    identities = {normalize_doi(str(m.get('doi') or '')) or _normalize(m['title']) for m in matches}
    return matches[0] if matches and len(identities) == 1 else None


def _new_result(reference: Reference) -> dict:
    return {
        "original_text": reference.original_text,
        "title": reference.title or "",
        "authors": list(reference.authors),
        "authors_source": "pdf" if reference.authors else None,
        "year": reference.year,
        "matched_title": None,
        "title_source": "PDF" if reference.title else None,
        "title_status": "from_pdf" if reference.title else "unresolved",
        "doi": reference.doi,
        "paper_url": f"https://doi.org/{reference.doi}" if reference.doi else None,
        "oa_pdf_url": None,
        "scholar_url": None,
        "metadata_sources": [],
        "source_links": [],
        "pdf_locations": [],
    }


def _add_source(result: dict, source_name: str, metadata: dict) -> None:
    for field in ('authors', 'abstract', 'journal'):
        if metadata.get(field) and (not result.get(field) or (field == 'authors' and result.get('authors_source') == 'pdf')):
            result[field] = metadata[field]
            if field == 'authors':
                result['authors_source'] = source_name
    title = metadata.get("title")
    doi = metadata.get("doi")
    paper_url = _safe_http_url(metadata.get("paper_url"))
    record_url = _safe_http_url(metadata.get("record_url")) or paper_url
    pdf_url = _safe_http_url(metadata.get("oa_pdf_url"))
    if _candidate_title(title) and not result["matched_title"]:
        result["matched_title"] = title
        result['title_source'] = source_name
        result['title_status'] = 'verified'
    if doi and not result["doi"]:
        result["doi"] = normalize_doi(str(doi))
    if paper_url and not result["paper_url"]:
        result["paper_url"] = paper_url
    if pdf_url and not result["oa_pdf_url"]:
        result["oa_pdf_url"] = pdf_url
    merge_locations(result, metadata.get('pdf_locations') or [location(pdf_url, source_name, landing=record_url)])
    for record in metadata.get('oa_records') or []:
        if record not in result['source_links']:
            result['source_links'].append(record)
    if metadata.get("year") and not result.get("year"):
        result["year"] = str(metadata["year"])
    if metadata.get('arxiv_id'):
        result['arxiv_id'] = metadata['arxiv_id']
    if source_name not in result["metadata_sources"]:
        result["metadata_sources"].append(source_name)
    if record_url and not any(link["name"] == source_name for link in result["source_links"]):
        result["source_links"].append({"name": source_name, "url": record_url})


def _set_access_category(result: dict) -> None:
    if result.get("oa_pdf_url"):
        result["access_status"] = "pdf_available"
        result["access_label"] = "ดาวน์โหลด PDF ได้"
        result["access_detail"] = f"พบ {len(result.get('pdf_locations') or [result['oa_pdf_url']])} ลิงก์ PDF จากฐานข้อมูล · ยังไม่ได้ตรวจดาวน์โหลดจริง · ฉบับผู้เขียน/preprint อาจต่างจากฉบับตีพิมพ์"
    elif any(source in result.get("metadata_sources", []) for source in ("OpenAlex", "Semantic Scholar", "Unpaywall", "Europe PMC", "arXiv", "HAL", "Zenodo", "CORE")):
        result["access_status"] = "open_source_record"
        result["access_label"] = "พบระเบียนในฐานข้อมูลเปิด"
        result["access_detail"] = "พบระเบียน แต่ยังไม่มีลิงก์ PDF ดาวน์โหลดตรง"
    else:
        result["access_status"] = "scholar_search"
        result["access_label"] = "ค้นต่อใน Google Scholar"
        result["access_detail"] = "ยังไม่พบ PDF หรือระเบียนจากแหล่ง Open Access ที่ค้นไว้"


def _record_lookup_error(result, source, exc):
    state = f'HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else 'unavailable'
    result.setdefault('oa_search', {})[source] = state


async def _resolve_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, reference: Reference, discovery=None, all_sources=False
) -> dict:
    reference = _clean_reference(reference)
    result = _new_result(reference)
    async with semaphore:
        try:
            crossref = await _crossref_lookup(client, reference)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            _record_lookup_error(result, 'Crossref', exc)
            crossref = None
        if crossref:
            titles = crossref.get("title") or []
            doi = crossref.get("DOI") or reference.doi
            crossref_url = f"https://doi.org/{doi}" if doi else _safe_http_url(crossref.get("URL"))
            _add_source(result, "Crossref", {
                "title": titles[0] if titles else None,
                "doi": doi,
                "paper_url": crossref_url,
                "record_url": crossref_url,
                "year": _crossref_year(crossref),
                "authors": [" ".join(str(a.get(k) or '') for k in ('given', 'family')).strip() for a in (crossref.get('author') or []) if isinstance(a, dict)][:40],
                "abstract": _crossref_abstract(crossref),
                "journal": (crossref.get('container-title') or [''])[0],
            })

        effective_doi = result.get('doi') or reference.doi
        if effective_doi:
            try:
                openalex_by_doi = await _openalex_by_doi(client, effective_doi)
                if openalex_by_doi:
                    _add_source(result, "OpenAlex", openalex_by_doi)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                _record_lookup_error(result, 'OpenAlex', exc)
            if all_sources or not result["oa_pdf_url"]:
                try:
                    semantic_by_doi = await _semantic_scholar_by_doi(client, effective_doi)
                    if semantic_by_doi:
                        _add_source(result, "Semantic Scholar", semantic_by_doi)
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    _record_lookup_error(result, 'Semantic Scholar', exc)

        effective_title = result["matched_title"] or reference.title
        # DOI references have already been checked against exact DOI records in
        # OpenAlex and Semantic Scholar; avoid repeating title-search requests.
        if not effective_doi:
            lookup_reference = replace(reference, title=effective_title)
            try:
                openalex = await _openalex_lookup(client, lookup_reference)
                if openalex:
                    _add_source(result, "OpenAlex", openalex)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                _record_lookup_error(result, 'OpenAlex', exc)

            if all_sources or not result["oa_pdf_url"] or not result["paper_url"]:
                try:
                    semantic_scholar = await _semantic_scholar_lookup(client, lookup_reference)
                    if semantic_scholar:
                        _add_source(result, "Semantic Scholar", semantic_scholar)
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    _record_lookup_error(result, 'Semantic Scholar', exc)

        if not result['matched_title'] and os.getenv('SERPAPI_API_KEY', '').strip():
            try:
                scholar = await _google_scholar_lookup(client, reference)
                if scholar:
                    _add_source(result, 'Google Scholar (SerpAPI)', scholar)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                _record_lookup_error(result, 'Google Scholar (SerpAPI)', exc)

        if discovery:
            await discovery.enrich(result)

    search_text = result["matched_title"] or reference.title
    if not result['matched_title'] and not _usable_title(search_text):
        search_text = reference.original_text
    result["scholar_url"] = "https://scholar.google.com/scholar?q=" + quote_plus(search_text)
    _set_access_category(result)
    return result


async def resolve_references(references: list[Reference]) -> list[dict]:
    """Look up bibliography entries concurrently, preserving their PDF order."""
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=4.0)
    limits = httpx.Limits(max_connections=MAX_CONCURRENT_LOOKUPS * 2)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=_headers(),
        follow_redirects=True,
    ) as client:
        discovery = OADiscovery(client)
        return await asyncio.gather(
            *(_resolve_one(client, semaphore, reference, discovery) for reference in references)
        )


async def lookup_doi(value: str) -> dict:
    """Resolve a user-provided DOI into the same shape used by PDF results."""
    doi = validate_doi(value)
    reference = Reference(original_text=f"DOI: {doi}", title="", year=None, number=1, doi=doi)
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=4.0)
    async with httpx.AsyncClient(timeout=timeout, headers=_headers(), follow_redirects=True) as client:
        result = _new_result(reference)
        try:
            crossref = await _crossref_by_doi(client, doi)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            crossref = None
        if crossref:
            titles = crossref.get("title") or []
            canonical_doi = crossref.get("DOI") or doi
            crossref_url = f"https://doi.org/{canonical_doi}"
            _add_source(result, "Crossref", {
                "title": titles[0] if titles else None,
                "doi": canonical_doi,
                "paper_url": crossref_url,
                "record_url": crossref_url,
                "year": _crossref_year(crossref),
                "authors": [" ".join(str(a.get(k) or '') for k in ('given', 'family')).strip() for a in (crossref.get('author') or []) if isinstance(a, dict)][:40],
                "abstract": _crossref_abstract(crossref),
                "journal": (crossref.get('container-title') or [''])[0],
            })
        try:
            openalex_by_doi = await _openalex_by_doi(client, doi)
            if openalex_by_doi:
                _add_source(result, "OpenAlex", openalex_by_doi)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
        if not result["oa_pdf_url"]:
            try:
                semantic_by_doi = await _semantic_scholar_by_doi(client, doi)
                if semantic_by_doi:
                    _add_source(result, "Semantic Scholar", semantic_by_doi)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass
        title = result["matched_title"]
        if _usable_title(title):
            title_reference = replace(reference, title=title)
            try:
                openalex = await _openalex_lookup(client, title_reference)
                if openalex:
                    _add_source(result, "OpenAlex", openalex)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass
            try:
                semantic = await _semantic_scholar_lookup(client, title_reference)
                if semantic:
                    _add_source(result, "Semantic Scholar", semantic)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass
        await OADiscovery(client).enrich(result)
        search_text = result["matched_title"] or doi
        result["scholar_url"] = "https://scholar.google.com/scholar?q=" + quote_plus(search_text)
        _set_access_category(result)
        result["reference_number"] = 1
        result["citation_mentions"] = 0
        return result


def _crossref_abstract(item: dict | None) -> str:
    if not item:
        return ""
    abstract = item.get("abstract") or ""
    abstract = re.sub(r"<[^>]+>", " ", html.unescape(abstract))
    return re.sub(r"\s+", " ", abstract).strip()


def _openalex_abstract(work: dict | None) -> str:
    index = (work or {}).get("abstract_inverted_index") or {}
    if not index:
        return ""
    words: dict[int, str] = {}
    for word, positions in index.items():
        for position in positions:
            words[int(position)] = word
    return " ".join(words[position] for position in sorted(words))


async def fetch_doi_summary_material(value: str) -> dict:
    """Fetch title/abstract metadata only; this does not download a paper PDF."""
    doi = validate_doi(value)
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=4.0)
    async with httpx.AsyncClient(timeout=timeout, headers=_headers(), follow_redirects=True) as client:
        crossref = None
        try:
            crossref = await _crossref_by_doi(client, doi)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass

        openalex = None
        try:
            response = await _academic_get(client, OPENALEX_URL, params={
                "filter": f"doi:https://doi.org/{doi}", "per-page": 1,
            })
            response.raise_for_status()
            works = response.json().get("results", [])
            openalex = works[0] if works else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass

    # Missing records are normal (404, timeout or null API payload). Normalize
    # before reading metadata so optional enrichment cannot crash AI features.
    crossref = crossref if isinstance(crossref, dict) else {}
    openalex = openalex if isinstance(openalex, dict) else {}
    crossref_title = (crossref.get("title") or [""])[0]
    title = crossref_title or (openalex or {}).get("display_name") or ""
    author_names = [
        " ".join(part for part in (author.get("given"), author.get("family")) if part)
        for author in (crossref.get("author") or [])[:12]
        if isinstance(author, dict)
    ]
    if not any(author_names):
        author_names = [
            author["display_name"]
            for entry in (openalex.get("authorships") or [])
            if isinstance(entry, dict)
            for author in [entry.get("author")]
            if isinstance(author, dict) and author.get("display_name")
        ][:12]
    abstract = _crossref_abstract(crossref) or _openalex_abstract(openalex)
    return {
        "doi": doi,
        "title": title,
        "year": _crossref_year(crossref) or str((openalex or {}).get("publication_year") or ""),
        "authors": author_names,
        "abstract": abstract,
        "source": "Crossref/OpenAlex metadata",
    }


def _crossref_reference_to_reference(item: dict, index: int) -> Reference:
    title = str(item.get("article-title") or item.get("volume-title") or "").strip()
    author = str(item.get("author") or "").strip()
    year = str(item.get("year") or "").strip() or None
    doi_value = str(item.get("DOI") or "").strip()
    doi = normalize_doi(doi_value) if doi_value else None
    unstructured = str(item.get("unstructured") or "").strip()
    pieces = [part for part in (
        author,
        f"({year})" if year else "",
        title,
        str(item.get("journal-title") or "").strip(),
        str(item.get("volume") or "").strip(),
        str(item.get("first-page") or "").strip(),
        f"DOI: {doi}" if doi else "",
    ) if part]
    original = unstructured or ". ".join(pieces) or f"Crossref reference {index}"
    return Reference(original_text=original, title=title, year=year, number=index, doi=doi)


def _openalex_work_to_reference(work: dict, index: int) -> Reference:
    title = str(work.get("display_name") or "").strip()
    year = str(work.get("publication_year") or "").strip() or None
    raw_doi = work.get("doi")
    doi = normalize_doi(str(raw_doi)) if raw_doi else None
    authors = [
        (authorship.get("author") or {}).get("display_name")
        for authorship in (work.get("authorships") or [])[:4]
    ]
    authors = [str(author) for author in authors if author]
    original = ", ".join(authors)
    if year:
        original += f" ({year})"
    if title:
        original += f". {title}"
    if doi:
        original += f". DOI: {doi}"
    if not original:
        original = str(work.get("id") or f"OpenAlex work {index}")
    return Reference(original_text=original, title=title, year=year, number=index, doi=doi)


def _semantic_scholar_work_to_reference(work: dict, index: int) -> Reference:
    title = str(work.get("title") or "").strip()
    year = str(work.get("year") or "").strip() or None
    ids = work.get("externalIds") or {}
    raw_doi = ids.get("DOI")
    doi = normalize_doi(str(raw_doi)) if raw_doi else None
    authors = [str(author.get("name") or "") for author in (work.get("authors") or [])[:4]]
    authors = [author for author in authors if author]
    original = ", ".join(authors)
    if year:
        original += f" ({year})"
    if title:
        original += f". {title}"
    if doi:
        original += f". DOI: {doi}"
    if not original:
        original = str(work.get("url") or f"Semantic Scholar work {index}")
    return Reference(original_text=original, title=title, year=year, number=index, doi=doi)


async def _openalex_relationships(client: httpx.AsyncClient, doi: str) -> dict:
    fields = "id,display_name,doi,publication_year,authorships,referenced_works,cited_by_count"
    response = await _academic_get(client, OPENALEX_URL, params={
        "filter": f"doi:https://doi.org/{doi}", "per-page": 1, "select": fields,
    })
    response.raise_for_status()
    works = response.json().get("results", [])
    if not works:
        return {"references": [], "citing": [], "reference_count": 0, "citation_count": 0}
    source = works[0]
    source_id = str(source.get("id") or "").rsplit("/", 1)[-1]
    reference_ids = [str(value).rsplit("/", 1)[-1] for value in (source.get("referenced_works") or [])]
    reference_ids = reference_ids[:100]
    citing_works: list[dict] = []
    citing_count = int(source.get("cited_by_count") or 0)
    if source_id:
        try:
            citing_response = await _academic_get(client, OPENALEX_URL, params={
                "filter": f"cites:{source_id}",
                "per-page": 20,
                "sort": "cited_by_count:desc",
                "select": "id,display_name,doi,publication_year,authorships",
            })
            citing_response.raise_for_status()
            citing_payload = citing_response.json()
            citing_works = citing_payload.get("results", [])
            citing_count = max(citing_count, int((citing_payload.get("meta") or {}).get("count") or 0))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass

    reference_works: list[dict] = []
    if reference_ids:
        try:
            references_response = await _academic_get(client, OPENALEX_URL, params={
                "filter": "openalex_id:" + "|".join(reference_ids),
                "per-page": min(len(reference_ids), 100),
                "select": "id,display_name,doi,publication_year,authorships",
            })
            references_response.raise_for_status()
            reference_works = references_response.json().get("results", [])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
    return {
        "references": [_openalex_work_to_reference(work, index) for index, work in enumerate(reference_works, 1)],
        "citing": [_openalex_work_to_reference(work, index) for index, work in enumerate(citing_works, 1)],
        "reference_count": len(source.get("referenced_works") or []),
        "citation_count": citing_count,
    }


async def _semantic_scholar_relationships(client: httpx.AsyncClient, doi: str) -> dict:
    paper_id = quote("DOI:" + doi, safe=":")
    paper_url = f"{SEMANTIC_SCHOLAR_URL}/{paper_id}"
    fields = "title,year,externalIds,url,authors,referenceCount,citationCount"
    paper = None
    try:
        response = await _academic_get(client, paper_url, params={"fields": fields})
        if response.status_code != 404:
            response.raise_for_status()
            paper = response.json()
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        pass

    list_fields = "title,year,externalIds,url,authors"
    async def get_list(endpoint: str, item_key: str) -> tuple[list[dict], int]:
        try:
            response = await _academic_get(client,
                f"{paper_url}/{endpoint}",
                params={"offset": 0, "limit": 100 if endpoint == "references" else 20, "fields": list_fields},
            )
            if response.status_code == 404:
                return [], 0
            response.raise_for_status()
            payload = response.json()
            items = [row.get(item_key) or {} for row in payload.get("data", [])]
            return [item for item in items if item], int(payload.get("total") or len(items))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return [], 0

    references_raw, reference_total = await get_list("references", "citedPaper")
    citing_raw, citation_total = await get_list("citations", "citingPaper")
    references = [
        _semantic_scholar_work_to_reference(item, index)
        for index, item in enumerate(references_raw, 1)
    ]
    citing = [
        _semantic_scholar_work_to_reference(item, index)
        for index, item in enumerate(citing_raw, 1)
    ]
    return {
        "references": references,
        "citing": citing,
        "reference_count": max(reference_total, int((paper or {}).get("referenceCount") or 0)),
        "citation_count": max(citation_total, int((paper or {}).get("citationCount") or 0)),
    }


def _merge_references(*groups: list[Reference], limit: int) -> tuple[list[Reference], int]:
    merged: list[Reference] = []
    key_to_index: dict[str, int] = {}
    total_seen = 0
    for group in groups:
        total_seen = max(total_seen, len(group))
        for reference in group:
            keys: list[str] = []
            if reference.doi:
                keys.append("doi:" + normalize_doi(reference.doi).casefold())
            if reference.title:
                keys.append("title:" + _normalize(reference.title))
            if not keys:
                keys.append("text:" + _normalize(reference.original_text))
            existing_index = next((key_to_index[key] for key in keys if key in key_to_index), None)
            if existing_index is not None:
                old = merged[existing_index]
                merged[existing_index] = Reference(
                    original_text=old.original_text if len(old.original_text) >= len(reference.original_text) else reference.original_text,
                    title=old.title or reference.title,
                    year=old.year or reference.year,
                    number=old.number,
                    doi=old.doi or reference.doi,
                )
                for key in keys:
                    key_to_index.setdefault(key, existing_index)
                continue
            index = len(merged)
            merged.append(reference)
            for key in keys:
                key_to_index[key] = index
            if len(merged) >= limit:
                return merged, total_seen
    return merged, total_seen


async def lookup_doi_relationships(value: str) -> dict:
    """Fetch the DOI paper's bibliography and a bounded list of citing works."""
    doi = validate_doi(value)
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=4.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=_headers(),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=12),
    ) as client:
        async def safe_crossref() -> dict:
            try:
                item = await _crossref_by_doi(client, doi)
                raw_refs = (item or {}).get("reference") or []
                refs = [_crossref_reference_to_reference(raw, index) for index, raw in enumerate(raw_refs[:100], 1)]
                return {"references": refs, "reference_count": len(raw_refs), "citation_count": 0}
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                return {"references": [], "reference_count": 0, "citation_count": 0}

        crossref_data, openalex_data, semantic_data = await asyncio.gather(
            safe_crossref(),
            _openalex_relationships(client, doi),
            _semantic_scholar_relationships(client, doi),
            return_exceptions=True,
        )
    if isinstance(openalex_data, BaseException):
        openalex_data = {"references": [], "citing": [], "reference_count": 0, "citation_count": 0}
    if isinstance(semantic_data, BaseException):
        semantic_data = {"references": [], "citing": [], "reference_count": 0, "citation_count": 0}

    references, _ = _merge_references(
        crossref_data.get("references", []),
        openalex_data.get("references", []),
        semantic_data.get("references", []),
        limit=100,
    )
    citing, _ = _merge_references(
        openalex_data.get("citing", []),
        semantic_data.get("citing", []),
        limit=20,
    )
    reference_count = max(
        len(references),
        int(crossref_data.get("reference_count") or 0),
        int(openalex_data.get("reference_count") or 0),
        int(semantic_data.get("reference_count") or 0),
    )
    citation_count = max(
        len(citing),
        int(openalex_data.get("citation_count") or 0),
        int(semantic_data.get("citation_count") or 0),
    )
    resolved_references, resolved_citing = await asyncio.gather(
        resolve_references(references),
        resolve_references(citing),
    )
    for index, item in enumerate(resolved_references, 1):
        item["reference_number"] = index
        item["citation_mentions"] = 0
    for index, item in enumerate(resolved_citing, 1):
        item["reference_number"] = index
        item["citation_mentions"] = 0
    return {
        "references": resolved_references,
        "citing_papers": resolved_citing,
        "reference_count": reference_count,
        "references_truncated": reference_count > len(resolved_references),
        "citation_count": citation_count,
        "citations_truncated": citation_count > len(resolved_citing),
        "relationship_sources": [
            name for name, count in (
                ("Crossref", crossref_data.get("reference_count", 0)),
                ("OpenAlex", openalex_data.get("reference_count", 0)),
                ("Semantic Scholar", semantic_data.get("reference_count", 0)),
            ) if count
        ],
    }


async def enrich_ai_references(items: list[dict]) -> list[dict]:
    """Recover missing titles before AI; author-only citations stay unknown."""
    semaphore = asyncio.Semaphore(6)
    async def enrich(item, client):
        result = dict(item)
        result['abstract'] = ''
        ref = _clean_reference(Reference(str(item.get('original_text') or ''), str(item.get('title') or ''),
                                        item.get('year'), item.get('id'), item.get('doi')))
        result['title'] = ref.title
        result['title_status'] = 'from_pdf' if ref.title else 'unresolved'
        result['title_source'] = 'PDF' if ref.title and _normalize(ref.title) in _normalize(ref.original_text) else None
        if item.get('doi'):
            try:
                async with semaphore:
                    material = await fetch_doi_summary_material(item['doi'])
                result['abstract'] = (material.get('abstract') or '')[:2500]
                result['title'] = material.get('title') or result.get('title')
                result['authors'] = material.get('authors') or []
                if material.get('title'):
                    result['title_status'] = 'verified'
                    result['title_source'] = 'DOI metadata'
                    result['matched_title'] = material['title']
            except (ValueError, httpx.HTTPError):
                pass
        elif not _usable_title(ref.title):
            metadata = await _resolve_one(client, semaphore, ref)
            for field in ('title', 'matched_title', 'title_status', 'title_source', 'doi', 'year', 'authors', 'abstract', 'paper_url', 'scholar_url', 'metadata_sources', 'source_links', 'oa_search'):
                if metadata.get(field) or field in ('title', 'title_status', 'title_source'):
                    result[field] = metadata.get(field)
            result['title'] = metadata.get('matched_title') or ref.title
        result['evidence_level'] = 'abstract' if result['abstract'] else ('title/metadata only' if result['title'] else 'bibliographic citation only — title unknown')
        return result
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), headers=_headers(), follow_redirects=False) as client:
        return await asyncio.gather(*(enrich(item, client) for item in items))
