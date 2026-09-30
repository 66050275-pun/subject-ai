"""PaperRef Finder API and static web application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from extractor import ExtractionError, extract_references
from resolver import resolve_references

BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024

app = FastAPI(
    title="PaperRef Finder",
    description="Extract research references from PDFs and find their paper links.",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


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

    results = await resolve_references(references)
    return {
        "filename": filename,
        "total_references": len(results),
        "direct_links": sum(bool(item["paper_url"]) for item in results),
        "results": results,
    }
