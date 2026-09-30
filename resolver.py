"""Concurrent academic metadata lookup with graceful fallbacks."""

from __future__ import annotations

import asyncio
import os
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from urllib.parse import urlsplit

import httpx

from extractor import Reference

REQUEST_TIMEOUT_SECONDS = 7.0
MAX_CONCURRENT_LOOKUPS = 8
CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def _headers() -> dict[str, str]:
    user_agent = "PaperRefFinder/1.0 (academic reference resolver)"
    contact = os.getenv("PAPERREF_CONTACT_EMAIL", "").strip()
    if contact:
        user_agent += f" mailto:{contact}"
    return {"User-Agent": user_agent, "Accept": "application/json"}


def _normalize(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


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


def _good_crossref_match(item: dict, reference: Reference) -> bool:
    try:
        score = float(item.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    titles = item.get("title") or []
    matched_title = titles[0] if titles else ""
    similarity = _title_similarity(reference.title, matched_title)
    # Crossref scores are relative rather than probabilities. Keep title
    # similarity as the main check and only accept a high Crossref score when
    # there is also some meaningful title overlap.
    return similarity >= 0.52 or (score >= 70 and similarity >= 0.35)


async def _crossref_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    response = await client.get(
        CROSSREF_URL,
        params={"query.bibliographic": reference.original_text, "rows": 5},
    )
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    matches = [item for item in items if _good_crossref_match(item, reference)]
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: _title_similarity(
            reference.title, (item.get("title") or [""])[0]
        ),
    )


async def _openalex_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    response = await client.get(
        OPENALEX_URL,
        params={"search": reference.title or reference.original_text, "per-page": 5},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    matches = [
        work for work in results
        if _title_matches(reference.title, work.get("display_name") or "")
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
    doi = raw_doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/") if raw_doi else None
    return {
        "title": work.get("display_name"),
        "doi": doi,
        "paper_url": _safe_http_url(raw_doi) or _safe_http_url(work.get("id")),
        "record_url": _safe_http_url(work.get("id")),
        "oa_pdf_url": _safe_http_url(pdf_url),
    }


async def _semantic_scholar_lookup(
    client: httpx.AsyncClient, reference: Reference
) -> dict | None:
    response = await client.get(
        SEMANTIC_SCHOLAR_URL,
        params={
            "query": reference.title or reference.original_text,
            "limit": 5,
            "fields": "title,openAccessPdf,externalIds,url,paperId",
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
    external_ids = paper.get("externalIds") or {}
    doi = external_ids.get("DOI")
    paper_url = _safe_http_url(paper.get("url"))
    if not paper_url and paper.get("paperId"):
        paper_url = f"https://www.semanticscholar.org/paper/{paper['paperId']}"
    return {
        "title": paper.get("title"),
        "doi": doi,
        "paper_url": paper_url or (f"https://doi.org/{doi}" if doi else None),
        "record_url": paper_url,
        "oa_pdf_url": _safe_http_url(pdf.get("url")),
    }


async def _resolve_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, reference: Reference
) -> dict:
    scholar_url = "https://scholar.google.com/scholar?q=" + quote_plus(reference.title or reference.original_text)
    result: dict = {
        "original_text": reference.original_text,
        "title": reference.title,
        "year": reference.year,
        "matched_title": None,
        "doi": None,
        "paper_url": None,
        "oa_pdf_url": None,
        "scholar_url": scholar_url,
        "metadata_sources": [],
        "source_links": [],
    }

    def add_source(source_name: str, metadata: dict) -> None:
        title = metadata.get("title")
        doi = metadata.get("doi")
        paper_url = _safe_http_url(metadata.get("paper_url"))
        record_url = _safe_http_url(metadata.get("record_url")) or paper_url
        pdf_url = _safe_http_url(metadata.get("oa_pdf_url"))

        if title and not result["matched_title"]:
            result["matched_title"] = title
        if doi and not result["doi"]:
            result["doi"] = doi
        if paper_url and not result["paper_url"]:
            result["paper_url"] = paper_url
        if pdf_url and not result["oa_pdf_url"]:
            result["oa_pdf_url"] = pdf_url
        if source_name not in result["metadata_sources"]:
            result["metadata_sources"].append(source_name)
        if record_url and not any(link["name"] == source_name for link in result["source_links"]):
            result["source_links"].append({"name": source_name, "url": record_url})

    async with semaphore:
        try:
            crossref = await _crossref_lookup(client, reference)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            crossref = None

        if crossref:
            titles = crossref.get("title") or []
            doi = crossref.get("DOI")
            crossref_url = f"https://doi.org/{doi}" if doi else _safe_http_url(crossref.get("URL"))
            add_source("Crossref", {
                "title": titles[0] if titles else None,
                "doi": doi,
                "paper_url": crossref_url,
                "record_url": crossref_url,
            })

        try:
            openalex = await _openalex_lookup(client, reference)
            if openalex:
                add_source("OpenAlex", openalex)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass

        if not result["oa_pdf_url"] or not result["paper_url"]:
            try:
                semantic_scholar = await _semantic_scholar_lookup(client, reference)
                if semantic_scholar:
                    add_source("Semantic Scholar", semantic_scholar)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass

    return result


async def resolve_references(references: list[Reference]) -> list[dict]:
    """Look up all references with bounded concurrency and per-request timeouts."""
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
