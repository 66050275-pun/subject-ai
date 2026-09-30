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
    similarity = SequenceMatcher(None, _normalize(reference.title), _normalize(matched_title)).ratio()
    # Crossref scores are relative rather than probabilities. Require a modest
    # absolute score or title overlap so empty/garbled scans do not get a false hit.
    return score >= 45 or (len(_normalize(reference.title)) > 8 and similarity >= 0.42)


async def _crossref_lookup(client: httpx.AsyncClient, reference: Reference) -> dict | None:
    response = await client.get(
        CROSSREF_URL,
        params={"query.bibliographic": reference.original_text, "rows": 1},
    )
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    if not items or not _good_crossref_match(items[0], reference):
        return None
    return items[0]


async def _openalex_pdf(client: httpx.AsyncClient, query: str) -> tuple[str | None, str | None]:
    response = await client.get(OPENALEX_URL, params={"search": query, "per-page": 1})
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return None, None
    work = results[0]
    title = work.get("display_name")
    locations = [work.get("best_oa_location") or {}, *(work.get("locations") or [])]
    pdf_url = next((location.get("pdf_url") for location in locations if location.get("pdf_url")), None)
    return title, pdf_url


async def _semantic_scholar_pdf(
    client: httpx.AsyncClient, query: str
) -> tuple[str | None, str | None]:
    response = await client.get(
        SEMANTIC_SCHOLAR_URL,
        params={"query": query, "limit": 1, "fields": "title,openAccessPdf"},
    )
    response.raise_for_status()
    papers = response.json().get("data", [])
    if not papers:
        return None, None
    paper = papers[0]
    pdf = paper.get("openAccessPdf") or {}
    return paper.get("title"), pdf.get("url")


async def _resolve_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, reference: Reference
) -> dict[str, str | None]:
    scholar_url = "https://scholar.google.com/scholar?q=" + quote_plus(reference.title or reference.original_text)
    result: dict[str, str | None] = {
        "original_text": reference.original_text,
        "title": reference.title,
        "year": reference.year,
        "matched_title": None,
        "doi": None,
        "paper_url": None,
        "oa_pdf_url": None,
        "scholar_url": scholar_url,
    }

    async with semaphore:
        try:
            crossref = await _crossref_lookup(client, reference)
        except httpx.TimeoutException:
            # The Scholar URL is always present. Avoid spending more time on
            # secondary APIs after the primary per-reference timeout expires.
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            crossref = None

        if crossref:
            titles = crossref.get("title") or []
            result["matched_title"] = titles[0] if titles else None
            result["doi"] = crossref.get("DOI")
            result["paper_url"] = _safe_http_url(crossref.get("URL")) or (
                f"https://doi.org/{result['doi']}" if result["doi"] else None
            )

        if not result["oa_pdf_url"]:
            query = result["matched_title"] or reference.title or reference.original_text
            try:
                oa_title, pdf_url = await _openalex_pdf(client, query)
                result["oa_pdf_url"] = _safe_http_url(pdf_url)
                if oa_title and not result["matched_title"]:
                    result["matched_title"] = oa_title
            except httpx.TimeoutException:
                return result
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass

        if not result["oa_pdf_url"]:
            query = result["matched_title"] or reference.title or reference.original_text
            try:
                s2_title, pdf_url = await _semantic_scholar_pdf(client, query)
                result["oa_pdf_url"] = _safe_http_url(pdf_url)
                if s2_title and not result["matched_title"]:
                    result["matched_title"] = s2_title
            except httpx.TimeoutException:
                return result
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass

    return result


async def resolve_references(references: list[Reference]) -> list[dict[str, str | None]]:
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
