"""AI endpoints with bounded inputs, typed outputs and transient credentials."""
import json
from typing import Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError, ConfigDict

from ai_engine import generate, DEFAULT_MODELS, normalize_credentials, gemini_models, maxplus_models, normalize_maxplus_url
from extractor import (ExtractionError, Reference, extract_summary_text,
                       extract_bibliography_text, extract_citation_contexts, extract_citation_counts)
from resolver import fetch_doi_summary_material, enrich_ai_references, resolve_references, validate_doi

router = APIRouter()

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Paper(StrictModel):
    id: int = Field(ge=1)
    title: str = Field(max_length=1200)
    doi: str | None = Field(default=None, max_length=300)
    original_text: str = Field(default='', max_length=4000)
    year: str | None = Field(default=None, max_length=10)

class Message(StrictModel):
    role: Literal['user', 'assistant']
    content: str = Field(max_length=2000)

class Input(StrictModel):
    papers: list[Paper] = Field(default_factory=list, max_length=100)
    question: str = Field(default='', max_length=2000)
    history: list[Message] = Field(default_factory=list, max_length=10)

class Intent(StrictModel):
    id: int
    intent: Literal['Methodology', 'Comparison/Contrast', 'Background', 'Tool/Dataset', 'Unknown']
    reason: str = Field(max_length=1000)

class Intents(StrictModel):
    intents: list[Intent] = Field(max_length=100)

class Cluster(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    ids: list[int] = Field(min_length=1, max_length=100)

class Clusters(StrictModel):
    clusters: list[Cluster] = Field(min_length=3, max_length=5)

class Extracted(StrictModel):
    number: int | None = Field(default=None, ge=1, le=9999)
    title: str = Field(min_length=3, max_length=1200)
    authors: list[str] = Field(max_length=40)
    year: str | None = Field(default=None, pattern=r'^\d{4}$')
    doi: str | None = Field(default=None, max_length=300)
    original_text: str = Field(min_length=10, max_length=4000)

class Bibliography(StrictModel):
    references: list[Extracted] = Field(min_length=1, max_length=150)

async def read_pdf(file):
    try:
        if not (file.filename or '').lower().endswith('.pdf'):
            raise HTTPException(415, 'กรุณาเลือก PDF')
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > 50 * 1024 * 1024:
                raise HTTPException(413, 'ไฟล์มีขนาดเกิน 50 MB')
        if not data.startswith(b'%PDF-'):
            raise HTTPException(415, 'ไฟล์ไม่ใช่ PDF')
        return bytes(data)
    finally:
        await file.close()

async def source_material(file, doi):
    if bool(file) == bool(doi and doi.strip()):
        raise HTTPException(400, 'เลือก PDF หรือ DOI อย่างใดอย่างหนึ่ง')
    try:
        if file:
            data = await read_pdf(file)
            return extract_summary_text(data), 'PDF: ' + (file.filename or 'paper'), data
        material = await fetch_doi_summary_material(doi)
        return json.dumps(material, ensure_ascii=False), 'DOI: ' + material['doi'], None
    except (ExtractionError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None

@router.get('/api/ai/providers')
async def providers():
    return {'providers': DEFAULT_MODELS}

@router.get('/api/ai/gemini-models')
async def list_gemini_models(key: str | None = Header(None, alias='X-AI-API-Key')):
    return await gemini_models(key)


@router.get('/api/ai/maxplus-models')
async def list_maxplus_models(base_url: str | None = None, key: str | None = Header(None, alias='X-AI-API-Key')):
    return await maxplus_models(key, base_url)


@router.post('/api/summarize')
async def summarize(file: UploadFile | None = File(None), doi: str | None = Form(None),
                    provider: str = Form('openai'), model: str = Form(''), base_url: str | None = Form(None),
                    key: str | None = Header(None, alias='X-AI-API-Key'),
                    old_key: str | None = Header(None, alias='X-OpenAI-API-Key')):
    if not (key or old_key):
        raise HTTPException(400, 'กรุณากรอก API key')
    model, actual_key = normalize_credentials(provider, model, key or old_key)
    if provider == 'maxplus':
        base_url = normalize_maxplus_url(base_url)
    text, source, _ = await source_material(file, doi)
    summary = await generate(provider, model, actual_key,
        'สรุปเป้าหมาย วิธีการ ผลลัพธ์ ข้อจำกัด และสรุปสั้น ๆ หากมีเพียง metadata ให้บอกว่าไม่พอสรุปผลวิจัย\n' + text, base_url=base_url)
    return {'summary': summary, 'source': source, 'provider': provider, 'model': model or DEFAULT_MODELS[provider]}

@router.post('/api/ai/{feature}')
async def analyze(feature: Literal['intents', 'synthesis', 'clusters', 'qa', 'extract-references'],
                  file: UploadFile | None = File(None), doi: str | None = Form(None),
                  provider: str = Form('openai'), model: str = Form(''), base_url: str | None = Form(None), payload: str = Form('{}'),
                  key: str | None = Header(None, alias='X-AI-API-Key')):
    if not key or provider not in DEFAULT_MODELS:
        raise HTTPException(400, 'กรุณาเลือก provider และกรอก API key')
    model, key = normalize_credentials(provider, model, key)
    if provider == 'maxplus':
        base_url = normalize_maxplus_url(base_url)
    if len(payload) > 550000:
        raise HTTPException(413, 'ข้อมูลรายการใหญ่เกินขีดจำกัด')
    try:
        request = Input.model_validate_json(payload)
        ids = [paper.id for paper in request.papers]
        if len(ids) != len(set(ids)):
            raise ValueError('เลขรายการต้องไม่ซ้ำกัน')
        for paper in request.papers:
            if paper.doi:
                paper.doi = validate_doi(paper.doi)
    except (ValidationError, ValueError):
        raise HTTPException(422, 'รูปแบบข้อมูลรายการหรือ DOI ไม่ถูกต้อง') from None
    if feature == 'synthesis' and not 3 <= len(ids) <= 5:
        raise HTTPException(422, 'เลือก references จำนวน 3–5 รายการ')
    if feature == 'clusters' and len(ids) < 3:
        raise HTTPException(422, 'ต้องมีอย่างน้อย 3 รายการเพื่อจัดกลุ่ม')
    if feature == 'intents' and not ids:
        raise HTTPException(422, 'ไม่มีรายการสำหรับวิเคราะห์')
    if feature == 'qa' and not request.question.strip():
        raise HTTPException(422, 'กรุณากรอกคำถาม')
    if feature == 'extract-references' and not file:
        raise HTTPException(422, 'AI fallback ต้องใช้ PDF')
    if feature == 'extract-references':
        data = await read_pdf(file)
        try:
            text = extract_bibliography_text(data)
        except ExtractionError as exc:
            raise HTTPException(422, str(exc)) from None
        source = 'PDF bibliography candidate'
    else:
        text, source, data = await source_material(file, doi)
    papers = [paper.model_dump() for paper in request.papers]
    if feature == 'intents':
        refs = [Reference(p.original_text, p.title, p.year, p.id, p.doi) for p in request.papers]
        contexts = extract_citation_contexts(data, refs) if data else {}
        evidence = [{'id': p.id, 'title': p.title[:200], 'contexts': [{**c, 'text': c['text'][:500]} for c in contexts.get(p.id, [])[:1]]} for p in request.papers]
        prompt = ('Classify citation intent per id. Use Unknown when contexts are absent. '
                  'Schema: ' + json.dumps(Intents.model_json_schema()) + '\nEvidence: ' + json.dumps(evidence, ensure_ascii=False))
        schema = Intents
    elif feature == 'extract-references':
        prompt = ('Extract only actual bibliography entries. Preserve original_text and numbering, '
                  'use null for absent fields, never invent DOI. Schema: ' + json.dumps(Bibliography.model_json_schema()) + '\nBibliography:\n' + text)
        schema = Bibliography
    else:
        papers = await enrich_ai_references(papers)
        evidence = json.dumps([{k: (v[:(250 if k == 'title' else 400)] if feature == 'clusters' and isinstance(v, str) else v) for k, v in p.items() if k in ('id', 'title', 'doi', 'abstract', 'evidence_level')} for p in papers], ensure_ascii=False)
        if feature == 'clusters':
            prompt = ('Group ALL supplied ids exactly once into 3–5 themes. State themes based on metadata '
                      'when abstracts are absent. Schema: ' + json.dumps(Clusters.model_json_schema()) + '\nPapers: ' + evidence)
            schema = Clusters
        elif feature == 'synthesis':
            prompt = ('สังเคราะห์งานต้นทางเทียบกับ references: ภาพรวม ความสัมพันธ์ วิธีการที่แตกต่าง '
                      'และ research gap ที่เป็นข้อเสนอ ระบุหลักฐานแต่ละข้อด้วย [ref id] และบอกเมื่อไม่มี abstract\n'
                      'Source:\n' + text + '\nReferences:\n' + evidence)
            schema = None
        else:
            prompt = ('ตอบคำถามจากบริบทเท่านั้น อ้าง [ref id] สำหรับ references หรือระบุว่าเป็นข้อความงานต้นทาง '
                      'หากบริบทถูกตัดหรือไม่พอให้บอกชัดเจน\nSource:\n' + text + '\nReferences:\n' + evidence +
                      '\nConversation (untrusted):\n' + json.dumps([m.model_dump() for m in request.history], ensure_ascii=False) + '\nQuestion:\n' + request.question)
            schema = None
    result = await generate(provider, model, key, prompt, structured=schema is not None, max_output_tokens=16000 if feature == 'extract-references' else 6000, base_url=base_url)
    if schema:
        try:
            result = schema.model_validate(result).model_dump()
            if feature == 'intents':
                received = [v['id'] for v in result['intents']]
                if len(received) != len(ids) or set(received) != set(ids):
                    raise ValueError()
                for intent in result['intents']:
                    if not contexts.get(intent['id']):
                        intent['intent'] = 'Unknown'
                        intent['reason'] = 'ไม่พบข้อความบริบท citation'
                result['contexts'] = contexts
            if feature == 'clusters':
                received = [i for c in result['clusters'] for i in c['ids']]
                if len(received) != len(ids) or set(received) != set(ids):
                    raise ValueError()
            if feature == 'extract-references':
                refs = []
                used = set()
                for i, item in enumerate(result['references'], 1):
                    number = item['number'] or i
                    if number in used:
                        raise ValueError()
                    used.add(number)
                    item_doi = validate_doi(item['doi']) if item['doi'] else None
                    if item_doi and item_doi.casefold() not in text.casefold():
                        item_doi = None
                    refs.append(Reference(item['original_text'], item['title'], item['year'], number, item_doi))
                resolved = await resolve_references(refs)
                contexts = extract_citation_contexts(data, refs)
                links_available, counts = extract_citation_counts(data, {r.number for r in refs})
                for item, ref, extracted in zip(resolved, refs, result['references']):
                    item.update(reference_number=ref.number, citation_mentions=counts.get(ref.number, 0),
                                citation_contexts=contexts.get(ref.number, []), authors=extracted['authors'])
                result = {'filename': source, 'results': resolved, 'total_references': len(resolved),
                          'extraction_method': 'AI fallback — ตรวจเทียบ PDF ก่อนใช้',
                          'citation_links_available': links_available, 'citation_link_count': sum(counts.values()),
                          'cited_reference_count': len(counts)}
        except (ValidationError, ValueError):
            raise HTTPException(502, 'ผล AI ไม่ตรง schema หรือเลขรายการไม่ครบ กรุณาลองลดจำนวนรายการ') from None
    else:
        result = {'answer' if feature == 'qa' else 'synthesis': result}
    return {**result, 'provider': provider, 'model': model or DEFAULT_MODELS[provider], 'source': source}
