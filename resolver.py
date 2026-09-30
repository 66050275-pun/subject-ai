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
        if _usable_title(effective_title):
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
