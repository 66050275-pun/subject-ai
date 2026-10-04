"""Request-scoped, code-only title recovery for current or saved references."""
import asyncio
import json

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from extractor import Reference
from resolver import _headers, _resolve_one, validate_doi

router = APIRouter()


class MetadataPaper(BaseModel):
    reference_number: int = Field(ge=1, le=9999)
    title: str = Field(default='', max_length=1200)
    matched_title: str | None = Field(default=None, max_length=1200)
    original_text: str = Field(default='', max_length=4000)
    year: str | None = Field(default=None, max_length=10)
    doi: str | None = Field(default=None, max_length=300)


class MetadataRequest(BaseModel):
    papers: list[MetadataPaper] = Field(min_length=1, max_length=1000)


@router.post('/api/metadata/search')
async def search_titles(request: MetadataRequest):
    numbers = [p.reference_number for p in request.papers]
    if len(numbers) != len(set(numbers)):
        raise HTTPException(422, 'เลขรายการอ้างอิงต้องไม่ซ้ำกัน')
    for p in request.papers:
        if p.doi:
            try:
                p.doi = validate_doi(p.doi)
            except ValueError:
                raise HTTPException(422, 'DOI ของรายการอ้างอิงไม่ถูกต้อง') from None

    async def events():
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), headers=_headers(), follow_redirects=False) as client:
            gate = asyncio.Semaphore(4)
            async def one(paper):
                ref = Reference(paper.original_text, paper.matched_title or paper.title, paper.year,
                                paper.reference_number, paper.doi)
                result = await _resolve_one(client, gate, ref)
                result['reference_number'] = paper.reference_number
                result['metadata_search'] = result.get('oa_search', {})
                return result
            tasks = [asyncio.create_task(one(p)) for p in request.papers]
            pending = set(tasks)
            completed = recovered = 0
            errors = {}
            try:
                yield json.dumps({'type': 'progress', 'completed': 0, 'total': len(tasks)}) + '\n'
                while pending:
                    finished, pending = await asyncio.wait(pending, timeout=15, return_when=asyncio.FIRST_COMPLETED)
                    if not finished:
                        yield '{"type":"heartbeat"}\n'
                    for task in finished:
                        result = task.result()
                        completed += 1
                        recovered += bool(result.get('matched_title'))
                        errors.update(result.get('metadata_search') or {})
                        yield json.dumps({'type': 'item', 'completed': completed, 'total': len(tasks), 'result': result}, ensure_ascii=False) + '\n'
                yield json.dumps({'type': 'done', 'total': len(tasks), 'recovered': recovered,
                                  'unresolved': len(tasks) - recovered, 'source_errors': errors}, ensure_ascii=False) + '\n'
            except Exception:
                # Keep internal exception details and credentials out of responses.
                yield json.dumps({'type': 'error', 'detail': 'ค้นชื่อเรื่องไม่สำเร็จ ผลที่ค้นพบแล้วและรายการเดิมยังอยู่'}, ensure_ascii=False) + '\n'
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    return StreamingResponse(events(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
