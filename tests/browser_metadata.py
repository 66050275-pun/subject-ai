"""Shared UI title lookup: mocked APIs only, no provider keys or paid requests."""
import functools
import http.server
import json
from pathlib import Path
import shutil
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(
    http.server.SimpleHTTPRequestHandler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()


def paper(number, **changes):
    row = {'reference_number': number, 'title': '', 'matched_title': None,
           'original_text': f'A. Author, B. Writer, Journal of Batteries 2024 ({number}).',
           'year': '2024', 'doi': None, 'oa_pdf_url': None,
           'access_status': 'scholar_search', 'metadata_sources': [], 'source_links': [],
           'scholar_url': 'https://scholar.google.com/scholar?q=full+original+citation',
           'citation_contexts': [], 'citation_mentions': 1}
    row.update(changes)
    return row


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which('chromium'),
                                            headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('https://**/*', lambda route: route.abort())
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        state = {'calls': 0, 'mode': 'success', 'held': None, 'title': 'Verified battery model'}
        existing = paper(2, title='Printed study title', matched_title='Existing resolved study',
                         title_status='verified', title_source='Crossref',
                         oa_pdf_url='https://repository.example/old.pdf',
                         pdf_locations=[{'url': 'https://repository.example/old.pdf', 'source': 'Repository'}],
                         paper_url='https://publisher.example/old', access_status='pdf_available',
                         metadata_sources=['Crossref'], source_links=[{'name': 'Crossref', 'url': 'https://doi.org/10.1000/old'}],
                         intent='Methodology', citation_contexts=[{'page': 2, 'method': 'link', 'text': 'Existing citation context'}])

        def api(route):
            path = route.request.url.split('/api/')[-1]
            if path == 'metadata/search':
                state['calls'] += 1
                assert 'x-ai-api-key' not in route.request.headers
                body = json.loads(route.request.post_data)
                assert body['papers'][0]['original_text'].startswith('A. Author')
                assert len(body['papers']) == 3
                if state['mode'] == 'held':
                    state['held'] = route
                    return
                if state['mode'] == 'failure':
                    route.fulfill(status=503, content_type='application/json', body='{"detail":"Metadata source unavailable"}')
                    return
                first = paper(1, matched_title=state['title'], title_source='Crossref', title_status='verified',
                              doi='10.1000/new', authors=['A. Author', 'B. Writer'], authors_source='Crossref',
                              paper_url='https://publisher.example/new', metadata_sources=['Crossref'],
                              source_links=[{'name': 'Crossref', 'url': 'https://doi.org/10.1000/new'}],
                              scholar_url='https://scholar.google.com/scholar?q=Verified+battery+model')
                events = [
                    {'type': 'progress', 'completed': 0, 'total': 3},
                    {'type': 'item', 'completed': 1, 'total': 3, 'result': first},
                    {'type': 'item', 'completed': 2, 'total': 3, 'result': paper(2, title_status='unresolved', metadata_search={'OpenAlex': 'HTTP 429'})},
                    {'type': 'item', 'completed': 3, 'total': 3, 'result': paper(3, title_status='unresolved')},
                ]
                if state['mode'] != 'incomplete':
                    events.append({'type': 'done', 'total': 3, 'recovered': 1, 'unresolved': 1, 'source_errors': {'OpenAlex': 'HTTP 429'}})
                route.fulfill(status=200, content_type='application/x-ndjson',
                              body='\n'.join(json.dumps(event) for event in events) + '\n')
                return
            if path == 'references':
                payload = {'filename': 'Metadata regression', 'results': [paper(1, title='A. Author and B. Writer'), existing, paper(3)],
                           'total_references': 3, 'citation_links_available': True, 'citation_link_count': 3}
            elif path == 'ai/intents':
                payload = {'intents': [{'id': 3, 'intent': 'Background', 'reason': 'Mock result'}],
                           'contexts': {}, 'resolved_papers': [dict(paper(3, matched_title='Recovered context dataset', title_status='verified', title_source='OpenAlex'), id=3)]}
            else:
                payload = {'providers': {'openai': 'Test'}}
            route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))

        page.route('**/api/**', api)
        page.goto(f'http://127.0.0.1:{server.server_port}/static/index.html')
        page.locator('#file-input').set_input_files({'name': 'test.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4 fixture'})
        page.locator('#analyze-button').click()
        page.locator('#results-list .reference-card').first.wait_for()
        assert page.locator('#openai-key').input_value() == ''
        page.locator('#metadata-search-button').click()
        page.locator('#metadata-status').filter(has_text='ค้นครบ 3').wait_for()
        assert 'เติมชื่อจากฐานข้อมูล 1' in page.locator('#metadata-status').inner_text()
        assert 'ยังไม่ยืนยันชื่อ 1' in page.locator('#metadata-status').inner_text()
        assert 'OpenAlex: HTTP 429' in page.locator('#metadata-status').inner_text()
        assert page.locator('[data-reference-number="1"] h4').inner_text() == state['title']
        assert page.locator('[data-reference-number="2"] h4').inner_text() == 'Existing resolved study'
        assert 'Existing citation context' in page.locator('[data-reference-number="2"] details').first.text_content()
        assert 'Methodology' in page.locator('[data-reference-number="2"]').inner_text()
        assert page.locator('[data-reference-number="2"] a[href="https://repository.example/old.pdf"]').count() >= 1
        assert page.locator('[data-reference-number="3"] h4').inner_text() == 'อ้างอิง #3 · รอค้นชื่อเรื่อง'
        assert page.locator('[data-graph-node=paper][aria-label*="รอค้นชื่อเรื่อง"]').count() == 1
        assert page.locator('[data-graph-node=paper][aria-label*="Verified battery model"]').count() == 1
        page.locator('.sidebar-settings').click()
        page.locator('#openai-key').fill('fake-regression-key')
        page.locator('#settings-close').click()
        page.locator('#settings-drawer').wait_for(state='hidden')
        page.locator('[data-ai=intents]').click()
        page.locator('[data-reference-number="3"] h4').filter(has_text='Recovered context dataset').wait_for()
        assert page.locator('[data-graph-node=paper][aria-label*="Recovered context dataset"]').count() == 1
        page.wait_for_timeout(600)
        page.locator('.sidebar-settings').click()
        page.locator('[data-settings-section=history]').click()
        page.locator('.history-card').wait_for()
        with page.expect_download() as download:
            page.locator('#history-export').click()
        saved = json.loads(Path(download.value.path()).read_text())
        row = saved['entries'][0]['payload']['results'][0]
        assert row['matched_title'] == state['title'] and row['title_source'] == 'Crossref'
        assert row['title_status'] == 'verified'
        assert saved['entries'][0]['payload']['results'][2]['matched_title'] == 'Recovered context dataset'
        assert 'fake-regression-key' not in json.dumps(saved)
        assert saved['entries'][0]['payload']['results'][1]['oa_pdf_url'] == 'https://repository.example/old.pdf'
        page.locator('#history-incognito').check()
        page.locator('#settings-close').click()
        page.locator('#settings-drawer').wait_for(state='hidden')
        state['title'] = 'Ephemeral new title'
        page.locator('#metadata-search-button').click()
        page.locator('[data-reference-number="1"] h4').filter(has_text='Ephemeral').wait_for()
        page.wait_for_timeout(400)
        page.reload()
        page.locator('.hero h1').wait_for()
        page.locator('.sidebar-settings').click()
        page.locator('[data-settings-section=history]').click()
        page.locator('.history-card').get_by_role('button', name='เปิดผลเดิม').click()
        page.locator('#settings-drawer').wait_for(state='hidden')
        assert page.locator('[data-reference-number="1"] h4').inner_text() == 'Verified battery model'
        assert state['calls'] == 2  # Restore is entirely offline.
        state['mode'] = 'failure'
        page.locator('#metadata-search-button').click()
        page.locator('#metadata-status').filter(has_text='Metadata source unavailable').wait_for()
        assert page.locator('[data-reference-number="1"] h4').inner_text() == 'Verified battery model'
        assert page.locator('#metadata-search-button').is_enabled()
        state['mode'] = 'incomplete'
        page.locator('#metadata-search-button').click()
        page.locator('#metadata-status').filter(has_text='จบก่อนค้นครบ').wait_for()
        assert page.locator('#metadata-search-button').is_enabled()
        state['mode'] = 'held'
        page.locator('#metadata-search-button').click()
        page.locator('#metadata-progress').wait_for(state='visible')
        assert page.locator('#oa-search-button').is_disabled()
        assert page.locator('#dataset-code').is_disabled()
        page.locator('#file-input').set_input_files({'name': 'different.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4 other'})
        page.locator('#metadata-progress').wait_for(state='hidden')
        assert page.locator('#results-section').is_hidden()
        if state['held']:
            state['held'].abort()
        assert not errors, errors
        browser.close()
    print('PASS: metadata lookup without AI key, title provenance, missing-title labels, AI enrichment, PDF/context retention, source errors, history/cache/incognito, failed/partial streams and source-change cancellation')
finally:
    server.shutdown()
