"""Community Cloud entry point: streamlit run streamlit_app.py."""
from __future__ import annotations

import csv
import io
import json
import os
import uuid

import streamlit as st
import streamlit.components.v1 as components

from ai_engine import DEFAULT_MODELS
from open_access import merge_locations, safe_url
from resolver import _set_access_category
from streamlit_backend import APIError, call_api
from streamlit_graph import graph_html

st.set_page_config(page_title='PaperRef Finder', page_icon='📚', layout='wide')

# Only owner-configured academic credentials may become shared environment
# variables. AI keys always come from the current user's password field.
for name in ('PAPERREF_CONTACT_EMAIL', 'OPENALEX_API_KEY', 'SEMANTIC_SCHOLAR_API_KEY', 'CORE_API_KEY'):
    try:
        value = st.secrets[name]
        if isinstance(value, str) and value:
            os.environ.setdefault(name, value)
    except (KeyError, FileNotFoundError):
        pass

LABELS = {'code': '⌘ สกัดด้วยโค้ด', 'ai': '✦ สกัดด้วย AI', 'doi': '↗ จาก DOI'}
PROVIDERS = {'openai': 'OpenAI', 'gemini': 'Google Gemini', 'claude': 'Anthropic Claude', 'maxplus': 'MaxPlus AI'}
VERSIONS = {'publishedVersion': 'ฉบับตีพิมพ์', 'acceptedVersion': 'ฉบับผู้เขียนที่รับตีพิมพ์',
            'submittedVersion': 'preprint', 'unknown': 'ไม่ระบุฉบับ'}
PALETTE = ['#7c3aed', '#0284c7', '#059669', '#ea580c', '#db2777']
state = st.session_state
for name, default in {'datasets': {}, 'source_id': None, 'source': None, 'generation': 0,
                      'ai_provider': 'openai', 'ai_model': DEFAULT_MODELS['openai'],
                      'ai_key': '', 'model_choices': [], 'summary': ''}.items():
    if name not in state:
        state[name] = default


def provider_changed():
    state.ai_key = ''
    state.ai_model = DEFAULT_MODELS[state.ai_provider]
    state.model_choices = []


def clear_key():
    state.ai_key = ''


def clear_session():
    state.datasets = {}; state.source = None; state.source_id = None
    state.generation += 1; state.summary = ''; state.ai_key = ''
    state.doi_input = ''; state.pending_kind = None


def source_args():
    if not state.source:
        raise APIError(400, 'เลือก PDF หรือกรอก DOI ก่อน')
    if state.source['kind'] == 'pdf':
        return {}, {'file': (state.source['name'], state.source['bytes'], 'application/pdf')}
    return {'doi': state.source['doi']}, None


def ai_call(feature, papers=None, extra=None, on_event=None):
    if not state.ai_key.strip():
        raise APIError(400, 'กรอก API key ของคุณในแถบตั้งค่า AI ก่อน')
    form, files = source_args()
    form.update(provider=state.ai_provider, model=state.ai_model)
    if state.ai_provider == 'maxplus':
        form['base_url'] = state.get('maxplus_url', 'https://api.maxplus-ai.cc/v1')
    if feature != 'summary':
        form['payload'] = json.dumps({'papers': [{'id': p['reference_number'],
            'title': str(p.get('matched_title') or p.get('title') or p.get('original_text') or '')[:1200],
            'doi': p.get('doi'), 'year': str(p['year']) if p.get('year') else None,
            'original_text': str(p.get('original_text') or '')[:4000]} for p in (papers or [])], **(extra or {})})
    if feature == 'extract-references':
        if state.source['kind'] != 'pdf':
            raise APIError(400, 'AI สกัดบรรณานุกรมต้องใช้ PDF')
        form['stream'] = 'true'
    return call_api('/api/summarize' if feature == 'summary' else '/api/ai/' + feature,
                    data=form, files=files, headers={'X-AI-API-Key': state.ai_key}, on_event=on_event)


def save_payload(kind, payload):
    for item in payload.get('results', []):
        item['extraction_source'] = kind
    state.datasets[kind] = {'payload': payload, 'id': uuid.uuid4().hex,
                            'themes': {}, 'history': [], 'analysis': '', 'oa_note': ''}
    state.pending_kind = kind
    st.rerun()


def stream_progress(status, event):
    if event['type'] == 'progress':
        status.update(label=f"กำลังประมวลผล {event.get('completed', 0)}/{event.get('total', '?')} ส่วน…")
    elif event['type'] == 'error':
        status.update(label='ประมวลผลไม่สำเร็จ', state='error')


def merge_update(item, update):
    old = dict(item)
    preserved = list(item.get('pdf_locations') or [])
    if item.get('oa_pdf_url') and not preserved:
        preserved = [{'url': item['oa_pdf_url'], 'source': 'เดิม', 'version': 'unknown'}]
    item.update(update)
    item['matched_title'] = item.get('matched_title') or old.get('matched_title')
    item['metadata_sources'] = list(dict.fromkeys([*old.get('metadata_sources', []), *item.get('metadata_sources', [])]))
    item['source_links'] = [*old.get('source_links', [])]
    for link in update.get('source_links', []):
        if link not in item['source_links']:
            item['source_links'].append(link)
    merge_locations(item, preserved)
    _set_access_category(item)


def csv_bytes(items):
    fields = ['extraction_source', 'reference_number', 'title', 'matched_title', 'year', 'doi',
              'original_text', 'oa_pdf_url', 'pdf_locations', 'oa_search', 'access_status', 'scholar_url']
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(fields)
    for item in items:
        row = []
        for field in fields:
            value = item.get(field)
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or '')
            row.append("'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text)
        writer.writerow(row)
    return output.getvalue().encode('utf-8-sig')


def paper_links(item, key):
    copies = item.get('pdf_locations') or []
    if item.get('oa_pdf_url'):
        st.link_button('↓ เปิด PDF', item['oa_pdf_url'])
    if copies:
        with st.expander(f'PDF ทุกฉบับ / ลิงก์สำรอง ({len(copies)})'):
            for i, copy in enumerate(copies):
                version = VERSIONS.get(copy.get('version'), 'ไม่ระบุฉบับ')
                st.link_button(f"↓ {copy.get('source', 'Open Access')} · {version}", copy['url'])
                if copy.get('license'):
                    st.caption('License: ' + copy['license'])
                if copy.get('matched_by') == 'title':
                    st.caption('จับคู่จากชื่อเรื่อง · ตรวจเทียบฉบับก่อนใช้')
                if copy.get('landing_url'):
                    st.link_button('หน้าแหล่งต้นทาง ↗', copy['landing_url'])
    links = [*item.get('source_links', [])]
    for name, url in [('หน้าบทความ', item.get('paper_url')), ('Google Scholar', item.get('scholar_url'))]:
        if url:
            links.append({'name': name, 'url': url})
    if links:
        with st.expander('แหล่งข้อมูลและค้นต่อ'):
            seen = set()
            for link in links:
                if safe_url(link.get('url')) and link['url'] not in seen:
                    seen.add(link['url']); st.link_button(link['name'] + ' ↗', link['url'])


with st.sidebar:
    st.subheader('เชื่อมต่อ AI ของคุณ')
    st.selectbox('Provider', list(PROVIDERS), format_func=PROVIDERS.get,
                 key='ai_provider', on_change=provider_changed)
    st.text_input('ชื่อโมเดล', key='ai_model')
    st.text_input('API key', type='password', key='ai_key')
    if state.ai_provider == 'maxplus':
        st.text_input('MaxPlus Base URL', value='https://api.maxplus-ai.cc/v1', key='maxplus_url')
    if state.ai_provider in ('gemini', 'maxplus'):
        if st.button('โหลดรายการโมเดล', disabled=not bool(state.ai_key), use_container_width=True):
            try:
                path = '/api/ai/' + state.ai_provider + '-models'
                if state.ai_provider == 'maxplus':
                    from urllib.parse import urlencode
                    path += '?' + urlencode({'base_url': state.maxplus_url})
                with st.spinner('กำลังโหลดโมเดล…'):
                    state.model_choices = call_api(path, method='GET', headers={'X-AI-API-Key': state.ai_key})['models']
            except APIError as exc:
                st.error(str(exc))
        if state.model_choices:
            def choose_model():
                state.ai_model = state.model_choice
            st.selectbox('โมเดลที่พบ', state.model_choices, key='model_choice', on_change=choose_model)
    st.button('ล้าง API key', on_click=clear_key, use_container_width=True)
    st.caption('AI ใช้คีย์และโควตาของผู้ใช้เท่านั้น คีย์ส่งผ่านเซิร์ฟเวอร์เพื่อเรียกผู้ให้บริการ และอยู่ในหน่วยความจำของ session นี้ ไม่บันทึกลงไฟล์')
    st.divider()
    st.button('ล้างไฟล์และข้อมูล session', on_click=clear_session, use_container_width=True)
    st.caption('บัญชีมหาวิทยาลัย: เปิดหน้าแหล่งต้นทางแล้วเข้าสู่ระบบผ่านสถาบันในเบราว์เซอร์')

st.title('PaperRef Finder')
st.write('จากงานวิจัยหนึ่งฉบับ → สู่เปเปอร์ที่อ้างถึง · ค้น PDF และสำรวจแนวคิดบนมือถือ')
source_mode = st.radio('แหล่งงานวิจัย', ['PDF', 'DOI'], horizontal=True, key='source_mode')
if source_mode == 'PDF':
    upload = st.file_uploader('แนบ PDF ขนาดไม่เกิน 50 MB', type=['pdf'], key=f'upload-{state.generation}')
    identity = ('pdf', upload.file_id) if upload else None
    source = {'kind': 'pdf', 'name': upload.name, 'bytes': upload.getvalue()} if upload else None
else:
    doi = st.text_input('DOI หรือ DOI URL', placeholder='10.1016/…', key='doi_input').strip()
    identity = ('doi', doi) if doi else None
    source = {'kind': 'doi', 'doi': doi, 'name': doi} if doi else None
if identity != state.source_id:
    state.source_id = identity; state.source = source; state.datasets = {}; state.summary = ''
    state.pending_kind = None

left, right = st.columns(2)
with left:
    if st.button('ค้นจาก DOI' if source_mode == 'DOI' else '⌘ สกัดด้วยโค้ด',
                 disabled=not bool(state.source), use_container_width=True, type='primary'):
        try:
            form, files = source_args()
            with st.spinner('กำลังสกัดและค้นแหล่ง Open Access…'):
                payload = call_api('/api/doi' if source_mode == 'DOI' else '/api/references', data=form, files=files)
            save_payload('doi' if source_mode == 'DOI' else 'code', payload)
        except APIError as exc:
            st.error(str(exc))
with right:
    if st.button('✦ สกัดบรรณานุกรมด้วย AI', disabled=not bool(state.source) or source_mode != 'PDF', use_container_width=True):
        try:
            with st.status('กำลังสกัดด้วย AI · หลายชุดคิดค่า API ตามการใช้งาน', expanded=True) as status:
                payload = ai_call('extract-references', on_event=lambda event: stream_progress(status, event))
                status.update(label='สกัดครบแล้ว', state='complete')
            save_payload('ai', payload)
        except APIError as exc:
            st.error(str(exc))

record = None; items = []; selected = []
if state.datasets:
    st.divider(); st.subheader('คลังงานวิจัยของคุณ')
    if state.get('pending_kind'):
        state.dataset_picker = state.pending_kind; state.pending_kind = None
    if state.get('dataset_picker') not in state.datasets:
        state.dataset_picker = next(iter(state.datasets))
    kind = st.selectbox('เลือกชุดผลลัพธ์', list(state.datasets), format_func=LABELS.get, key='dataset_picker')
    record = state.datasets[kind]; payload = record['payload']; items = payload.get('results', [])
    st.caption(f"{LABELS[kind]} · {payload.get('filename', '')} · จำนวน references ไม่รวม citation ในเนื้อหา")
    for warning in payload.get('extraction_warnings', []):
        st.warning(warning)
    if payload.get('references_truncated'):
        st.info('ฐานข้อมูลแสดง references เป็นตัวอย่างสูงสุด 100 รายการ')
    count, pdfs = st.columns(2)
    count.metric('References ที่แสดง', len(items)); pdfs.metric('References ที่พบลิงก์ PDF', sum(bool(p.get('oa_pdf_url')) for p in items))
    source_paper = payload.get('source_paper')
    if source_paper:
        with st.expander('งานวิจัยต้นทาง'):
            st.write(source_paper.get('matched_title') or source_paper.get('doi')); paper_links(source_paper, 'source')

    with st.expander('ค้น PDF เพิ่มทุกแหล่ง · ไม่ใช้ AI key', expanded=False):
        email = st.text_input('อีเมลติดต่อสำหรับ Unpaywall (ไม่บังคับ)', key='oa_email')
        st.caption('อีเมลนี้ใช้ติดต่อ Unpaywall ไม่ใช่การเข้าสู่ระบบมหาวิทยาลัย CORE ใช้คีย์ที่เจ้าของแอปตั้งใน Secrets')
        if st.button('ค้น PDF เพิ่มทุกแหล่ง', key='deep-search'):
            try:
                request = {'contact_email': email, 'papers': [{k: p.get(k) for k in
                    ('reference_number', 'title', 'matched_title', 'original_text', 'doi', 'year') if p.get(k) is not None} for p in items]}
                with st.status('กำลังค้นคลังและฉบับสำรอง…', expanded=True) as status:
                    def update_oa(event):
                        if event['type'] == 'item':
                            item = next((p for p in items if p['reference_number'] == event['result']['reference_number']), None)
                            if item is not None:
                                merge_update(item, event['result'])
                            status.update(label=f"ค้นแล้ว {event['completed']}/{event['total']} รายการ")
                    call_api('/api/open-access/search', json_body=request, on_event=update_oa)
                    status.update(label='ค้นครบแล้ว', state='complete')
                issues = sorted({name + ': ' + value for p in items for name, value in p.get('oa_search', {}).items()
                                 if value not in ('found', 'not found')})
                record['oa_note'] = f"ค้นครบ {len(items)} รายการ · พบ PDF {sum(bool(p.get('oa_pdf_url')) for p in items)} references"
                if issues:
                    record['oa_note'] += ' · แหล่งที่ยังค้นไม่ครบ: ' + ' / '.join(issues)
                st.rerun()
            except APIError as exc:
                record['oa_note'] = str(exc) + ' · ผลก่อนหน้าและรายการที่ค้นเพิ่มได้ยังอยู่'
                st.error(record['oa_note'])
        if record['oa_note']:
            st.info(record['oa_note'])

    lookup = {p['reference_number']: p for p in items}
    selected_ids = st.multiselect('เลือก 3–5 references เพื่อสังเคราะห์ หรือเลือกสำหรับถาม–ตอบ', list(lookup),
        format_func=lambda n: f"[{n}] {lookup[n].get('matched_title') or lookup[n].get('title') or lookup[n].get('original_text')}",
        max_selections=5, key='select-' + record['id'])
    selected = [lookup[n] for n in selected_ids]
    with st.expander('กราฟอ้างอิง · ลาก / ซูม / Fit', expanded=True):
        st.caption('กราฟเลือกสีตามสถานะ PDF หรือธีม AI · เลือกเปเปอร์สำหรับ AI ด้วยช่องเลือกด้านบน')
        graph = graph_html(items, payload.get('filename', 'งานวิจัยต้นทาง'), record['themes'])
        if hasattr(st, 'iframe'):
            st.iframe(graph, height=620)
        else:
            components.html(graph, height=620, scrolling=True)

    query = st.text_input('ค้นในรายการ', key='search-' + record['id']).casefold()
    access = st.selectbox('การเข้าถึง', ['ทั้งหมด', 'พบ PDF', 'พบระเบียน', 'ค้นต่อใน Scholar'], key='filter-' + record['id'])
    view = st.radio('รายการที่แสดง', ['References', 'Citation / Cited by'], horizontal=True, key='view-' + record['id'])
    visible = items if view == 'References' else (payload.get('citing_papers', []) if kind == 'doi' else [p for p in items if p.get('citation_mentions')])
    statuses = {'พบ PDF': 'pdf_available', 'พบระเบียน': 'open_source_record', 'ค้นต่อใน Scholar': 'scholar_search'}
    visible = [p for p in visible if (access == 'ทั้งหมด' or p.get('access_status', 'scholar_search') == statuses[access])
               and (not query or query in ' '.join(str(p.get(k) or '') for k in ('title', 'matched_title', 'doi', 'original_text')).casefold())]
    export_name = f'paperref-{kind}-{view.split()[0].lower()}'
    one, two, three = st.columns(3)
    one.download_button('↓ JSON', json.dumps(visible, ensure_ascii=False, indent=2), export_name + '.json', 'application/json')
    two.download_button('↓ CSV', csv_bytes(visible), export_name + '.csv', 'text/csv')
    urls = list(dict.fromkeys(url for p in visible for url in ([c['url'] for c in p.get('pdf_locations', [])] or ([p['oa_pdf_url']] if p.get('oa_pdf_url') else []))))
    three.download_button('↓ ลิงก์ PDF', '\n'.join(urls), export_name + '-pdf-links.txt', 'text/plain', disabled=not bool(urls))
    st.caption(f'แสดง {len(visible)} รายการ · PDF เป็นลิงก์ที่ฐานข้อมูลระบุ ยังไม่ได้ตรวจดาวน์โหลดจริง')
    for p in visible:
        number = p['reference_number']; title = p.get('matched_title') or p.get('title') or p.get('original_text') or 'ไม่พบชื่อเรื่อง'
        with st.expander(f'[{number}] {title}', expanded=False):
            st.write(p.get('access_label', 'ค้นต่อใน Google Scholar'))
            st.caption(' · '.join(str(v) for v in (p.get('year'), p.get('doi')) if v))
            if p.get('intent'):
                st.info(p['intent'] + ' · ' + p.get('intent_reason', ''))
            for context in p.get('citation_contexts', []):
                st.caption(f"บริบท citation · หน้า {context['page']}"); st.write(context['text'])
            st.write(p.get('original_text', '')); paper_links(p, str(number))

st.divider(); st.subheader('AI Research Lab')
st.caption('ทุกคำขอ AI ใช้ API key ของคุณ · ต้องเลือกโมเดลที่ผู้ให้บริการรองรับ')
if st.button('สรุปงานวิจัยต้นทาง', disabled=not bool(state.source)):
    try:
        with st.spinner('กำลังสรุป…'):
            state.summary = ai_call('summary')['summary']
    except APIError as exc:
        st.error(str(exc))
if state.summary:
    with st.expander('สรุปงานวิจัย', expanded=True):
        st.markdown(state.summary)
if record:
    if len(items) > 100:
        st.info('Citation intent และธีมกราฟ AI รองรับสูงสุด 100 references ต่อคำขอ · สังเคราะห์และถาม–ตอบใช้รายการที่เลือกได้ตามปกติ')
    intent, synth, cluster = st.columns(3)
    action = None
    if intent.button('วิเคราะห์ citation intent', disabled=not items or len(items) > 100): action = 'intents'
    if synth.button('สังเคราะห์ 3–5 เปเปอร์', disabled=not 3 <= len(selected) <= 5): action = 'synthesis'
    if cluster.button('จัดธีมบนกราฟ', disabled=not 3 <= len(items) <= 100): action = 'clusters'
    if action:
        try:
            with st.spinner('กำลังวิเคราะห์…'):
                result = ai_call(action, selected if action == 'synthesis' else items)
            if action == 'intents':
                for intent in result['intents']:
                    item = next(p for p in items if p['reference_number'] == intent['id'])
                    item.update(intent=intent['intent'], intent_reason=intent['reason'])
                    item['citation_contexts'] = result.get('contexts', {}).get(str(intent['id']), result.get('contexts', {}).get(intent['id'], []))
                record['analysis'] = 'วิเคราะห์เจตนาแล้ว · เปิดการ์ด reference เพื่อดูบริบท'
            elif action == 'clusters':
                record['themes'] = {n: {'name': c['name'], 'color': PALETTE[i]} for i, c in enumerate(result['clusters']) for n in c['ids']}
                record['analysis'] = 'จัดธีมแล้ว: ' + ' · '.join(c['name'] for c in result['clusters'])
            else:
                record['analysis'] = result['synthesis']
            st.rerun()
        except APIError as exc:
            st.error(str(exc))
    if record['analysis']:
        st.markdown(record['analysis'])
    st.caption('ถาม–ตอบใช้งานต้นทางและ references ที่เลือก สูงสุด 5 รายการ')
    for message in record['history']:
        with st.chat_message(message['role']):
            st.markdown(message['content'])
    question = st.chat_input('ถามเกี่ยวกับวิธีการ ผลลัพธ์ หรือข้อจำกัด', max_chars=2000)
    if question:
        try:
            with st.spinner('กำลังตอบ…'):
                result = ai_call('qa', selected, {'question': question, 'history': [{'role': m['role'], 'content': m['content'][:2000]} for m in record['history'][-10:]]})
            record['history'].extend([{'role': 'user', 'content': question}, {'role': 'assistant', 'content': result['answer']}])
            record['history'] = record['history'][-20:]
            st.rerun()
        except APIError as exc:
            st.error(str(exc))
else:
    st.info('เลือก PDF หรือ DOI แล้วสกัดรายการเพื่อใช้ citation intent, synthesis, graph themes และถาม–ตอบ')
