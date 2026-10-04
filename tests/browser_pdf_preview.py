"""Real browser PDF rendering on both hosts; no paid API or remote PDF request.

Run with Playwright installed and PAPERREF_TEST_PYTHON pointing to the Python
environment with PyMuPDF/Streamlit. Fixtures are generated in a test temp folder.
"""
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web_security import content_policy

PYTHON = os.environ.get('PAPERREF_TEST_PYTHON', str(ROOT / '.venv/bin/python'))
POLICY = content_policy((ROOT / 'static/index.html').read_text())


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        if self.path == '/static/index.html':
            self.send_header('Content-Security-Policy', POLICY)
        super().end_headers()

    def log_message(self, *args):
        pass


TRACK = """(() => {
 const worker = window.Worker, create = URL.createObjectURL, revoke = URL.revokeObjectURL;
 window.previewTest = {workers: 0, terminated: 0, urls: [], revoked: []};
 window.previewWorkers = [];
 window.Worker = class extends worker {
   constructor(...args) { super(...args); window.previewTest.workers++; window.previewWorkers.push(this); }
   terminate() { window.previewTest.terminated++; return super.terminate(); }
 };
 URL.createObjectURL = function(...args) { const url = create.apply(this, args); window.previewTest.urls.push(url); return url; };
 URL.revokeObjectURL = function(url) { window.previewTest.revoked.push(url); return revoke.call(this, url); };
})();"""


def ready(ui, number, total):
    ui.locator('#pdf-page-label').filter(has_text=f'หน้า {number} / {total}').wait_for(timeout=30000)
    ui.locator('#pdf-preview-canvas').wait_for(state='visible')
    ui.locator('#pdf-preview-viewport[aria-busy=false]').wait_for()
    # This asserts painted pixels, rather than merely the presence of a canvas.
    assert ui.locator('#pdf-preview-canvas').evaluate('''c => {
      const pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
      let ink=0; for(let i=0;i<pixels.length;i+=4) if(pixels[i+3]>0 && Math.min(pixels[i],pixels[i+1],pixels[i+2])<180) ink++;
      return ink>50 && c.width*c.height<=2405000;
    }''')


def exercise(page, ui, fixture, secret, streamlit=False):
    ui.locator('.hero h1').wait_for(timeout=30000)
    ui.locator('#file-input').set_input_files(str(fixture))
    ready(ui, 1, 2)
    local_url = ui.locator('#pdf-open').get_attribute('href')
    assert local_url.startswith('blob:')
    assert ui.locator('#pdf-open').get_attribute('rel') == 'noopener noreferrer'
    ui.locator('#pdf-next').click(); ready(ui, 2, 2)
    assert ui.locator('#pdf-next').is_disabled()
    ui.locator('#pdf-zoom-in').click(); ready(ui, 2, 2)
    assert ui.locator('#pdf-zoom-label').inner_text() == '125%'
    ui.locator('#pdf-prev').click(); ready(ui, 1, 2)
    ui.locator('#pdf-preview-toggle').click()
    assert ui.locator('#pdf-preview-body').is_hidden()
    assert ui.locator('#pdf-preview-toggle').get_attribute('aria-expanded') == 'false'
    ui.locator('#pdf-preview-toggle').click(); ready(ui, 1, 2)
    for width, height, label in [(390, 844, 'phone'), (768, 1024, 'ipad'), (1440, 1000, 'desktop')]:
        page.set_viewport_size({'width': width, 'height': height})
        page.wait_for_timeout(250)
        ready(ui, 1, 2)
        assert ui.locator('body').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
        assert ui.locator('.upload-panel').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
        ui.locator('#pdf-preview').scroll_into_view_if_needed()
        if not streamlit:
            page.screenshot(path=str(ROOT.parent / f'paperref-pdf-preview-{label}.png'))
    # Selected document is only RAM; replacing it destroys its worker/local URL.
    ui.locator('#file-input').set_input_files(str(secret))
    ui.locator('#pdf-preview-status').filter(has_text='รหัสผ่าน').wait_for(timeout=30000)
    assert ui.locator('#pdf-preview-canvas').is_hidden()
    assert ui.locator('#analyze-button').is_enabled()  # Independent extraction.
    assert ui.locator('body').evaluate('(el,url) => window.previewTest.revoked.includes(url)', local_url)
    ui.locator('#file-input').set_input_files({'name': 'broken.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4 broken'})
    ui.locator('#pdf-preview-status').filter(has_text='ไฟล์ PDF อาจไม่สมบูรณ์').wait_for(timeout=30000)
    # Rapid replacement must not paint a stale page or keep an old worker.
    ui.locator('#file-input').set_input_files(str(fixture))
    ui.locator('#file-input').set_input_files({'name': 'wrong.txt', 'mimeType': 'text/plain', 'buffer': b'not PDF'})
    assert ui.locator('#pdf-preview').is_hidden()
    assert ui.locator('#pdf-open').get_attribute('href') is None
    page.wait_for_timeout(1100)
    stats = ui.locator('body').evaluate('el => window.previewTest')
    assert stats['terminated'] == stats['workers'], stats
    ui.locator('#file-input').set_input_files(str(fixture)); ready(ui, 1, 2)
    # A worker crash after startup must stop loading and retain the local fallback.
    ui.locator('body').evaluate("el => window.previewWorkers.at(-1).dispatchEvent(new Event('error', {cancelable:true}))")
    ui.locator('#pdf-preview-status').filter(has_text='ตัวอ่าน PDF เปิดไม่สำเร็จ').wait_for()
    assert ui.locator('#pdf-preview-viewport').get_attribute('aria-busy') == 'false'
    assert ui.locator('#pdf-next').is_disabled()
    assert ui.locator('#pdf-open').get_attribute('href').startswith('blob:')
    ui.locator('#file-input').set_input_files(str(fixture)); ready(ui, 1, 2)
    # History stores extraction metadata only; restore has no PDF to preview.
    ui.locator('#analyze-button').click()
    ui.locator('#results-list .reference-card').first.wait_for(timeout=30000)
    ui.locator('.sidebar-settings').click(); ui.locator('[data-settings-section=history]').click()
    ui.locator('.history-card').first.wait_for(timeout=30000)
    with page.expect_download() as download:
        ui.locator('#history-export').click()
    saved = Path(download.value.path()).read_text()
    assert '%PDF' not in saved and 'JVBER' not in saved and 'blob:' not in saved
    assert 'pdf-preview-canvas' not in saved
    ui.locator('.history-card').first.get_by_role('button', name='เปิดผลเดิม').click()
    ui.locator('#settings-drawer').wait_for(state='hidden')
    assert ui.locator('#pdf-preview').is_hidden()
    ui.locator('#file-input').set_input_files(str(fixture)); ready(ui, 1, 2)
    ui.locator('#doi-input').fill('10.1000/test'); ui.locator('#doi-button').click()
    ui.locator('#dataset-doi[aria-pressed=true]').wait_for(timeout=30000)
    assert ui.locator('#pdf-preview').is_hidden()
    assert ui.locator('#pdf-open').get_attribute('href') is None


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()
cloud = None
try:
    with tempfile.TemporaryDirectory(prefix='paperref-preview-test-') as folder:
        fixture, secret = Path(folder) / 'preview.pdf', Path(folder) / 'private.pdf'
        subprocess.run([PYTHON, '-c', '''import fitz,sys
d=fitz.open()
for i in range(2):
 p=d.new_page();p.insert_text((50,65),'PaperRef local preview page '+str(i+1),fontsize=22)
 p.draw_rect(fitz.Rect(50,100,400,220),color=(.1,.3,.2),fill=(.8,.9,.8))
d.save(sys.argv[1]);d.save(sys.argv[2],encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='local-only')
''', str(fixture), str(secret)], check=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=2)
            context.add_init_script(TRACK)
            page = context.new_page(); page.route('https://**/*', lambda route: route.abort())
            errors, calls = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def api(route):
                if route.request.method == 'POST': calls.append(route.request.url)
                kind = 'doi' if route.request.url.endswith('/doi') else 'pdf'
                payload = {'mode': kind, 'filename': 'Local preview fixture', 'total_references': 1, 'results': [
                    {'reference_number': 1, 'title': 'Battery models', 'authors': ['A. Researcher'], 'year': '2024', 'access_status': 'scholar_search'}]}
                route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))

            page.route('**/api/**', api)
            page.goto(f'http://127.0.0.1:{server.server_port}/static/index.html')
            exercise(page, page, fixture, secret)
            assert len(calls) == 2, calls  # Only explicit extraction and DOI searches.
            actual = os.environ.get('PAPERREF_PREVIEW_PDF')
            if actual:
                page.locator('#file-input').set_input_files(actual)
                page.locator('#pdf-preview-canvas').wait_for(state='visible', timeout=30000)
                assert page.locator('#pdf-page-label').inner_text().startswith('หน้า 1 / ')
                page.locator('#pdf-next').click()
                page.locator('#pdf-page-label').filter(has_text='หน้า 2 / ').wait_for(timeout=30000)
                page.screenshot(path=str(ROOT.parent / 'paperref-pdf-preview-real.png'))
            assert not errors, errors
            context.close()
            # Actual Streamlit bridge, not a reimplementation of its component.
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            with tempfile.TemporaryFile(mode='w+') as log:
                cloud = subprocess.Popen([PYTHON, str(ROOT / 'tests/browser_mock_streamlit.py'), str(port)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                url = f'http://127.0.0.1:{port}'
                for _ in range(150):
                    try:
                        urllib.request.urlopen(url + '/_stcore/health', timeout=1); break
                    except Exception:
                        time.sleep(0.1)
                shared = browser.new_context(viewport={'width': 1440, 'height': 1000})
                shared.add_init_script(TRACK)
                page = shared.new_page(); page.route('https://**/*', lambda route: route.abort())
                errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                ui = page.frame_locator('iframe').first
                exercise(page, ui, fixture, secret, streamlit=True)
                assert not errors, errors
                shared.close(); cloud.terminate(); cloud.wait(timeout=10); cloud = None
            browser.close()
    print('PASS: PDF canvas pixels, navigation/zoom, desktop/phone/iPad, CSP, invalid/password PDF, source races, worker/URL cleanup, no API preview or history PDF, and real Streamlit component')
finally:
    if cloud:
        cloud.terminate(); cloud.wait(timeout=10)
    server.shutdown(); server.server_close()
