"""PaperRef Finder API and static web application."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web_security import SecurityHeaders
from extractor import ExtractionError, extract_citation_counts, extract_citation_contexts, extract_references
from ai_features import router as ai_router
from oa_features import router as oa_router
from resolver import (
    lookup_doi,
    lookup_doi_relationships,
    resolve_references,
)

BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024

app = FastAPI(
    title="PaperRef Finder",
    description="Extract research references from PDFs and find their paper links.",
    version="2.0.0",
)
app.add_middleware(SecurityHeaders)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(ai_router)
app.include_router(oa_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/api/references")
async def find_references(file: UploadFile = File(...)) -> dict:
    """Extract references from one PDF and resolve metadata concurrently."""
    filename = file.filename or "uploaded file"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="กรุณาอัปโหลดไฟล์ PDF (.pdf)")

    chunks: list[bytes] = []
    size = 0
    try:
        while chunk := await file.read(READ_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="ไฟล์มีขนาดเกิน 50 MB")
            chunks.append(chunk)
    finally:
        await file.close()

    pdf_bytes = b"".join(chunks)
    if not pdf_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="ไฟล์ที่เลือกไม่ใช่ PDF ที่ถูกต้อง")

    try:
        references = extract_references(pdf_bytes)
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    reference_numbers = {
        reference.number for reference in references if reference.number is not None
    }
    citation_links_available, citation_counts = extract_citation_counts(
        pdf_bytes, reference_numbers
    )
    contexts = extract_citation_contexts(pdf_bytes, references)
    results = await resolve_references(references)
    for index, (item, reference) in enumerate(zip(results, references), start=1):
        number = reference.number or index
        item["reference_number"] = number
        item["citation_mentions"] = citation_counts.get(number, 0)
        item["citation_contexts"] = contexts.get(number, [])

    return {
        "filename": filename,
        "total_references": len(results),
        "direct_links": sum(bool(item["paper_url"]) for item in results),
        "citation_links_available": citation_links_available,
        "citation_link_count": sum(citation_counts.values()),
        "cited_reference_count": len(citation_counts),
        "results": results,
    }


@app.post("/api/doi")
async def find_by_doi(doi: str = Form(...)) -> dict:
    """Resolve a DOI, its bibliography, and papers that cite it."""
    try:
        result, relationships = await asyncio.gather(
            lookup_doi(doi), lookup_doi_relationships(doi)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    title = result.get("matched_title") or result.get("doi") or "DOI"
    return {
        "mode": "doi",
        "filename": title,
        "source_paper": result,
        "total_references": relationships["reference_count"],
        "direct_links": sum(bool(item.get("paper_url")) for item in relationships["references"]),
        "citation_links_available": bool(relationships["citation_count"] or relationships["citing_papers"]),
        "citation_link_count": relationships["citation_count"],
        "cited_reference_count": len(relationships["citing_papers"]),
        "references_truncated": relationships["references_truncated"],
        "citations_truncated": relationships["citations_truncated"],
        "relationship_sources": relationships["relationship_sources"],
        "results": relationships["references"],
        "citing_papers": relationships["citing_papers"],
    }
