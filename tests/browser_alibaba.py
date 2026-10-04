"""Alibaba settings and every AI action through the actual Streamlit bridge.

Run with system Python/Playwright and PAPERREF_TEST_PYTHON pointing to the
application venv. Provider responses are mocked; no credentials or paid calls.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SINGAPORE = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
BEIJING = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}'
server = subprocess.Popen([
    os.environ.get('PAPERREF_TEST_PYTHON', sys.executable),
    str(ROOT / 'tests/browser_mock_streamlit.py'), str(port)
], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

try:
    for _ in range(100):
        try:
            urllib.request.urlopen(url + '/_stcore/health', timeout=1)
            break
        except OSError:
            time.sleep(.1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('PAPERREF_TEST_CHROMIUM') or shutil.which('chromium'),
            headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('https://**/*', lambda route: route.abort())
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        frame = page.frame_locator('iframe').first
        frame.locator('.hero h1').wait_for(timeout=30000)
        frame.locator('body').evaluate('''() => {
          window.providerRequests=[];
          const original=window.fetch;
          window.fetch=(url,options={})=>{
            if(/^\\/api\\/(ai\\/|summarize)/.test(String(url))){
              const body=options.body instanceof FormData ? options.body : null;
              window.providerRequests.push({url:String(url),method:options.method||'GET',
                provider:body?.get('provider'),model:body?.get('model'),base_url:body?.get('base_url'),
                hasKey:!!new Headers(options.headers).get('X-AI-API-Key')});
            }
            return original(url,options);
          };
        }''')
        def close_settings():
            frame.locator('#settings-close').click()
            frame.locator('#settings-drawer').wait_for(state='hidden')

        frame.locator('.sidebar-settings').click()
        frame.locator('#openai-key').fill('fake-alibaba-test-key')
        frame.locator('#ai-provider').select_option('alibaba')
        assert frame.locator('#openai-key').input_value() == ''
        assert frame.locator('#summary-model').input_value() == 'qwen-plus'
        assert frame.locator('#alibaba-settings').is_visible()
        assert not frame.locator('#maxplus-settings').is_visible()
        assert frame.locator('#alibaba-region').input_value() == SINGAPORE
        assert frame.locator('#alibaba-region option').count() == 4
        frame.locator('#alibaba-check').click()
        frame.locator('#alibaba-check-result').filter(has_text='ยังไม่ได้ยืนยันคีย์').wait_for(timeout=30000)
        assert frame.locator('#alibaba-model-picker option[value=qwen-plus]').count() == 1
        catalog_requests = frame.locator('body').evaluate('() => window.providerRequests')
        assert len(catalog_requests) == 1 and not catalog_requests[0]['hasKey'], catalog_requests
        frame.locator('#openai-key').fill('fake-alibaba-test-key')
        frame.locator('#alibaba-region').select_option(BEIJING)
        assert frame.locator('#openai-key').input_value() == ''
        frame.locator('#openai-key').fill('fake-alibaba-test-key')
        frame.locator('#summary-model').fill('qwen-plus')
        close_settings()

        frame.locator('#file-input').set_input_files({
            'name': 'provider-test.pdf', 'mimeType': 'application/pdf',
            'buffer': b'%PDF-1.4 test'})
        frame.locator('[data-ai=extract-references]').click()
        frame.locator('#dataset-ai[aria-pressed=true]').wait_for(timeout=30000)
        assert frame.locator('#results-list .reference-card').count() == 3
        frame.locator('#summary-button').click()
        frame.locator('#summary-output').filter(has_text='Mock shared summary').wait_for(timeout=30000)
        frame.locator('[data-ai=intents]').click()
        expect(frame.locator('[data-ai=intents]')).to_be_enabled(timeout=30000)
        for box in frame.locator('#results-list input[type=checkbox]').all():
            box.check()
        frame.locator('[data-ai=synthesis]').click()
        frame.locator('#ai-output').filter(has_text='Mock shared research gap').wait_for(timeout=30000)
        frame.locator('[data-ai=clusters]').click()
        frame.locator('#ai-output').filter(has_text='จัดกลุ่ม').wait_for(timeout=30000)
        expect(frame.locator('[data-ai=clusters]')).to_be_enabled(timeout=30000)
        frame.locator('#qa-question').fill('What evidence is available?')
        frame.locator('#qa-form button').click()
        frame.locator('#chat-log').filter(has_text='Mock shared answer').wait_for(timeout=30000)

        # Saved research comparison has its own request builder.
        frame.locator('body').evaluate('''async () => {
          await window.PaperRefHistory.clearAll();
          for(let n=1;n<=2;n++)await window.PaperRefHistory.save({
            filename:'Distinct study '+n,source_paper:{title:'Distinct study '+n,doi:'10.1000/study'+n},
            total_references:1,results:[{reference_number:1,title:'Reference',original_text:'Reference',year:'2024'}]
          },'doi');
        }''')
        frame.locator('.sidebar-settings').click()
        frame.locator('[data-settings-section=history]').click()
        for box in frame.locator('#history-list input[type=checkbox]').all():
            box.check()
        frame.locator('#workspace-compare').click()
        frame.locator('#workspace-output').filter(has_text='Mock cross-paper comparison').wait_for(timeout=30000)
        requests = frame.locator('body').evaluate('() => window.providerRequests')
        expected = {'/api/summarize', '/api/ai/extract-references', '/api/ai/intents',
                    '/api/ai/synthesis', '/api/ai/clusters', '/api/ai/qa', '/api/ai/compare'}
        posts = [row for row in requests if row['method'] == 'POST']
        assert {row['url'] for row in posts} == expected, requests
        assert len(posts) == len(expected), requests  # No automatic paid retries.
        assert all(row['provider'] == 'alibaba' and row['model'] == 'qwen-plus'
                   and row['base_url'] == BEIJING and row['hasKey'] for row in posts), requests

        frame.locator('[data-settings-section=ai]').click()
        frame.locator('#remember-ai-preferences').check()
        page.wait_for_timeout(200)
        prefs = frame.locator('body').evaluate("() => localStorage.getItem('paperref.ai-preferences.v1')")
        assert json.loads(prefs) == {'provider': 'alibaba', 'model': 'qwen-plus'}, prefs
        assert 'fake-alibaba-test-key' not in prefs
        for width, height in [(390, 844), (768, 1024), (1440, 1000)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.wait_for_timeout(150)
            assert frame.locator('#settings-drawer').evaluate('el=>el.scrollWidth<=el.clientWidth')
        page.screenshot(path=str(Path(tempfile.gettempdir()) / 'paperref-alibaba-settings.png'))
        page.reload()
        frame = page.frame_locator('iframe').first
        frame.locator('.hero h1').wait_for(timeout=30000)
        frame.locator('.sidebar-settings').click()
        assert frame.locator('#ai-provider').input_value() == 'alibaba'
        assert frame.locator('#summary-model').input_value() == 'qwen-plus'
        assert frame.locator('#openai-key').input_value() == ''
        assert frame.locator('#alibaba-settings').is_visible()
        assert not errors, errors
        browser.close()
    print('PASS: Alibaba regions, transient keys, all seven AI actions over Streamlit, '
          'safe remembered preferences, responsive settings and reload')
finally:
    server.terminate()
    server.wait(timeout=10)
