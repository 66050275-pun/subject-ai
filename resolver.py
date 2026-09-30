"""Academic metadata lookup with DOI-first and citation-text fallbacks."""

from __future__ import annotations

import asyncio
import html
import os
import re
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import quote, quote_plus, unquote, urlsplit

import httpx

from extractor import Reference

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


def _usable_title(value: str | None) -> bool:
    if not value:
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
    return _title_similarity(reference_title, candidate_title) >= 0.52


def _safe_http_url(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _first_author_family(citation: str) -> str:
    first = re.sub(r"^\s*\[\d+\]\s*", "", citation).split(",", 1)[0].strip()
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][\w’'-]*", first)
    return tokens[-1].casefold() if tokens else ""


def _crossref_year(item: dict) -> str | None:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            return str(parts[0][0])
    return None


def _crossref_author_matches(item: dict, citation: str) -> bool:
    family = _first_author_family(citation)
    authors = item.get("author") or []
    return bool(family and any(
        str(author.get("family") or "").casefold() == family for author in authors
    ))


def _good_crossref_match(item: dict, reference: Reference) -> bool:
    titles = item.get("title") or []
    matched_title = titles[0] if titles else ""
    if _usable_title(reference.title) and _title_similarity(reference.title, matched_title) >= 0.45:
        return True

    full_citation = _normalize(reference.original_text)
    normalized_title = _normalize(matched_title)
    if len(normalized_title) >= 15 and normalized_title in full_citation:
        return True

    try:
        score = float(item.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    same_year = not reference.year or _crossref_year(item) == reference.year
    # Some bibliography formats omit article titles entirely. Crossref's
    # bibliographic score plus the year and first author can still identify it.
    return score >= 35 and same_year and _crossref_author_matches(item, reference.original_text)


async def _crossref_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await client.get(f"{CROSSREF_URL}/{quote(doi, safe='')}")
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
    response = await client.get(
        CROSSREF_URL,
        params={"query.bibliographic": reference.original_text, "rows": 8},
    )
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    matches = [item for item in items if _good_crossref_match(item, reference)]
    if not matches:
        return None
    return max(matches, key=lambda item: float(item.get("score") or 0))


async def _openalex_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    search = reference.title if _usable_title(reference.title) else reference.original_text
    response = await client.get(
        OPENALEX_URL,
        params={"search": search, "per-page": 5},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    matches = [
        work for work in results
        if _usable_title(reference.title)
        and _title_matches(reference.title, work.get("display_name") or "")
    ]
    if not matches:
        return None
    work = max(
        matches,
        key=lambda item: _title_similarity(reference.title, item.get("display_name") or ""),
    )
    locations = [work.get("best_oa_location") or {}, *(work.get("locations") or [])]
    pdf_url = next((location.get("pdf_url") for location in locations if location.get("pdf_url")), None)
    raw_doi = work.get("doi")
    doi = normalize_doi(raw_doi) if raw_doi else None
    return {
        "title": work.get("display_name"),
        "doi": doi,
        "paper_url": _safe_http_url(raw_doi) or _safe_http_url(work.get("id")),
        "record_url": _safe_http_url(work.get("id")),
        "oa_pdf_url": _safe_http_url(pdf_url),
        "year": str(work.get("publication_year") or "") or None,
    }


async def _openalex_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await client.get(
        OPENALEX_URL,
        params={"filter": f"doi:https://doi.org/{doi}", "per-page": 1},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return None
    work = results[0]
    locations = [work.get("best_oa_location") or {}, *(work.get("locations") or [])]
    pdf_url = next((location.get("pdf_url") for location in locations if location.get("pdf_url")), None)
    return {
        "title": work.get("display_name"),
        "doi": normalize_doi(work.get("doi") or doi),
        "paper_url": _safe_http_url(work.get("doi")) or _safe_http_url(work.get("id")),
        "record_url": _safe_http_url(work.get("id")),
        "oa_pdf_url": _safe_http_url(pdf_url),
        "year": str(work.get("publication_year") or "") or None,
    }


async def _semantic_scholar_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await client.get(
        f"{SEMANTIC_SCHOLAR_URL}/{quote('DOI:' + doi, safe=':')}",
        params={"fields": "title,openAccessPdf,externalIds,url,paperId,year"},
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
        "year": str(paper.get("year") or "") or None,
    }


async def _semantic_scholar_lookup(
    client: httpx.AsyncClient, reference: Reference
) -> dict | None:
    if not _usable_title(reference.title):
        return None
    response = await client.get(
        f"{SEMANTIC_SCHOLAR_URL}/search",
        params={
            "query": reference.title,
            "limit": 5,
            "fields": "title,openAccessPdf,externalIds,url,paperId,year",
        },
    )
    response.raise_for_status()
    papers = response.json().get("data", [])
    matches = [paper for paper in papers if _title_matches(reference.title, paper.get("title") or "")]
    if not matches:
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
        "year": str(paper.get("year") or "") or None,
    }


def _new_result(reference: Reference) -> dict:
    return {
        "original_text": reference.original_text,
        "title": reference.title if _usable_title(reference.title) else "",
        "year": reference.year,
        "matched_title": None,
        "doi": reference.doi,
        "paper_url": f"https://doi.org/{reference.doi}" if reference.doi else None,
        "oa_pdf_url": None,
        "scholar_url": None,
        "metadata_sources": [],
        "source_links": [],
    }


def _add_source(result: dict, source_name: str, metadata: dict) -> None:
    title = metadata.get("title")
    doi = metadata.get("doi")
    paper_url = _safe_http_url(metadata.get("paper_url"))
    record_url = _safe_http_url(metadata.get("record_url")) or paper_url
    pdf_url = _safe_http_url(metadata.get("oa_pdf_url"))
    if title and not result["matched_title"]:
        result["matched_title"] = title
    if doi and not result["doi"]:
        result["doi"] = normalize_doi(str(doi))
    if paper_url and not result["paper_url"]:
        result["paper_url"] = paper_url
    if pdf_url and not result["oa_pdf_url"]:
        result["oa_pdf_url"] = pdf_url
    if metadata.get("year") and not result.get("year"):
        result["year"] = str(metadata["year"])
    if source_name not in result["metadata_sources"]:
        result["metadata_sources"].append(source_name)
    if record_url and not any(link["name"] == source_name for link in result["source_links"]):
        result["source_links"].append({"name": source_name, "url": record_url})


def _set_access_category(result: dict) -> None:
    if result.get("oa_pdf_url"):
        result["access_status"] = "pdf_available"
        result["access_label"] = "ดาวน์โหลด PDF ได้"
        result["access_detail"] = "พบลิงก์ PDF ที่เปิดให้อ่านได้จากแหล่งข้อมูล"
    elif any(source in result.get("metadata_sources", []) for source in ("OpenAlex", "Semantic Scholar")):
        result["access_status"] = "open_source_record"
        result["access_label"] = "พบระเบียนในฐานข้อมูลเปิด"
        result["access_detail"] = "พบระเบียน แต่ยังไม่มีลิงก์ PDF ดาวน์โหลดตรง"
    else:
        result["access_status"] = "scholar_search"
        result["access_label"] = "ค้นต่อใน Google Scholar"
        result["access_detail"] = "ยังไม่พบ PDF หรือระเบียนจากแหล่ง Open Access ที่ค้นไว้"


async def _resolve_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, reference: Reference
) -> dict:
    result = _new_result(reference)
    async with semaphore:
        try:
            crossref = await _crossref_lookup(client, reference)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
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
            })

        if reference.doi:
            try:
                openalex_by_doi = await _openalex_by_doi(client, reference.doi)
                if openalex_by_doi:
                    _add_source(result, "OpenAlex", openalex_by_doi)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass
            if not result["oa_pdf_url"]:
                try:
                    semantic_by_doi = await _semantic_scholar_by_doi(client, reference.doi)
                    if semantic_by_doi:
                        _add_source(result, "Semantic Scholar", semantic_by_doi)
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                    pass

        effective_title = result["matched_title"] or reference.title
        # DOI references have already been checked against exact DOI records in
        # OpenAlex and Semantic Scholar; avoid repeating title-search requests.
        if not reference.doi and _usable_title(effective_title):
            lookup_reference = replace(reference, title=effective_title)
            try:
                openalex = await _openalex_lookup(client, lookup_reference)
                if openalex:
                    _add_source(result, "OpenAlex", openalex)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass

            if not result["oa_pdf_url"] or not result["paper_url"]:
                try:
                    semantic_scholar = await _semantic_scholar_lookup(client, lookup_reference)
                    if semantic_scholar:
                        _add_source(result, "Semantic Scholar", semantic_scholar)
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                    pass

    search_text = result["matched_title"] or reference.title
    if not _usable_title(search_text):
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
        return await asyncio.gather(
            *(_resolve_one(client, semaphore, reference) for reference in references)
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
            response = await client.get(OPENALEX_URL, params={
                "filter": f"doi:https://doi.org/{doi}", "per-page": 1,
            })
            response.raise_for_status()
            works = response.json().get("results", [])
            openalex = works[0] if works else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass

    crossref_title = ((crossref or {}).get("title") or [""])[0]
    title = crossref_title or (openalex or {}).get("display_name") or ""
    author_names = [
        " ".join(part for part in (author.get("given"), author.get("family")) if part)
        for author in (crossref or {}).get("author", [])[:12]
    ]
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
    response = await client.get(OPENALEX_URL, params={
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
            citing_response = await client.get(OPENALEX_URL, params={
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
            references_response = await client.get(OPENALEX_URL, params={
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
        response = await client.get(paper_url, params={"fields": fields})
        if response.status_code != 404:
            response.raise_for_status()
            paper = response.json()
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        pass

    list_fields = "title,year,externalIds,url,authors"
    async def get_list(endpoint: str, item_key: str) -> tuple[list[dict], int]:
        try:
            response = await client.get(
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
    """Fetch abstracts with bounded concurrency; missing metadata stays explicit."""
    semaphore = asyncio.Semaphore(6)
    async def enrich(item):
        result = dict(item)
        result['abstract'] = ''
        if item.get('doi'):
            try:
                async with semaphore:
                    material = await fetch_doi_summary_material(item['doi'])
                result['abstract'] = (material.get('abstract') or '')[:2500]
                result['title'] = material.get('title') or result.get('title')
                result['authors'] = material.get('authors') or []
            except (ValueError, httpx.HTTPError):
                pass
        result['evidence_level'] = 'abstract' if result['abstract'] else 'title/metadata only'
        return result
    return await asyncio.gather(*(enrich(item) for item in items))
