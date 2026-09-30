"""Explicit, streamed repository discovery without an AI key."""
import asyncio
import json
import re
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import httpx

from extractor import Reference
from open_access import OADiscovery
from resolver import _resolve_one, _set_access_category, _headers, validate_doi

router = APIRouter()


class OAPaper(BaseModel):
    reference_number: int = Field(ge=1, le=9999)
    title: str = Field(default='', max_length=1200)
    matched_title: str | None = Field(default=None, max_length=1200)
    original_text: str = Field(default='', max_length=4000)
    year: str | None = Field(default=None, max_length=10)
    doi: str | None = Field(default=None, max_length=300)


class OARequest(BaseModel):
    papers: list[OAPaper] = Field(min_length=1, max_length=1000)
    contact_email: str = Field(default='', max_length=254)


@router.post('/api/open-access/search')
async def search_open_access(request: OARequest):
    numbers = [paper.reference_number for paper in request.papers]
    if len(numbers) != len(set(numbers)):
        raise HTTPException(422, 'เลขรายการอ้างอิงต้องไม่ซ้ำกัน')
    email = request.contact_email.strip()
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HTTPException(422, 'อีเมลติดต่อสำหรับ Unpaywall ไม่ถูกต้อง')
    for paper in request.papers:
        if paper.doi:
            try:
                paper.doi = validate_doi(paper.doi)
            except ValueError:
                raise HTTPException(422, 'DOI ไม่ถูกต้องสำหรับรายการ ' + str(paper.reference_number)) from None

    async def events():
        queue = asyncio.Queue()
        async def worker():
            timeout = httpx.Timeout(8, connect=4)
            async with httpx.AsyncClient(timeout=timeout, headers=_headers(), follow_redirects=False) as client:
                discovery = OADiscovery(client, email or None)
                gate = asyncio.Semaphore(4)
                async def one(paper):
                    async with gate:
                        ref = Reference(paper.original_text, paper.matched_title or paper.title, paper.year,
                                        paper.reference_number, paper.doi)
                        result = await _resolve_one(client, asyncio.Semaphore(1), ref, discovery, all_sources=True)
                        await discovery.enrich(result, deep=True)
                        _set_access_category(result)
                        result['reference_number'] = paper.reference_number
                        await queue.put({'type': 'item', 'result': result})
                tasks = [asyncio.create_task(one(paper)) for paper in request.papers]
                try:
                    await asyncio.gather(*tasks)
                    await queue.put({'type': 'done', 'total': len(request.papers)})
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        task = asyncio.create_task(worker())
        try:
            completed = 0
            yield json.dumps({'type': 'progress', 'completed': 0, 'total': len(request.papers)}) + '\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    if event['type'] == 'item':
                        completed += 1
                        event.update(completed=completed, total=len(request.papers))
                    yield json.dumps(event, ensure_ascii=False) + '\n'
                    if event['type'] == 'done':
                        break
                except asyncio.TimeoutError:
                    if task.done():
                        # Do not expose provider response text or server credentials.
                        yield json.dumps({'type': 'error', 'detail': 'ค้นหาเพิ่มเติมไม่สำเร็จ ผลที่ค้นพบแล้วและชุดเดิมยังอยู่'}, ensure_ascii=False) + '\n'
                        break
                    yield '{"type":"heartbeat"}\n'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return StreamingResponse(events(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
