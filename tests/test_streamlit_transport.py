"""Shared frontend identity, real ASGI upload, streaming, replay and cancellation."""
import asyncio
import base64
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from streamlit_frontend import frontend_html
import streamlit_transport as transport


def command(request_id='request-one', path='/api/ai/providers', method='GET', **extra):
    return {'id': request_id, 'path': path, 'method': method, **extra}


class TransportTests(unittest.TestCase):
    def test_shared_template_and_assets_are_exact(self):
        root = Path(__file__).resolve().parents[1]
        page = frontend_html()
        expected = (root / 'static/index.html').read_text()
        for name in ('utilities.css', 'app.css'):
            expected = expected.replace('<link rel="stylesheet" href="/static/' + name + '">', '<style>' + (root / 'static' / name).read_text() + '</style>')
        for name in ('progress.js', 'settings.js', 'workspace.js', 'history.js', 'graph.js'):
            expected = expected.replace('<script src="/static/' + name + '"></script>', '<script>' + (root / 'static' / name).read_text() + '</script>')
        expected = expected.replace('class="brand" href="/"', 'class="brand" href="#paper-input"')
        import re
        page = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*><meta name="referrer" content="no-referrer">', '', page, count=1)
        self.assertEqual(page, expected)

    def test_only_supported_local_api_routes(self):
        for path in ('https://example.org/api/references', '/api/unknown', '/health', '//example.org/api/references'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                transport.make_request(command(path=path))

    def test_real_multipart_upload_and_header_handling(self):
        message = command(path='/api/references', method='POST', body={'kind': 'form', 'entries': [
            {'name': 'file', 'file': True, 'filename': 'paper.pdf', 'mime': 'application/pdf',
             'data': base64.b64encode(b'not a PDF').decode()}]})
        request = transport.make_request(message)
        frames = []
        asyncio.run(transport.forward(request, frames.append))
        self.assertEqual(frames[0]['status'], 415)
        body = b''.join(base64.b64decode(e['data']) for e in frames if e['kind'] == 'chunk')
        self.assertIn('PDF', json.loads(body)['detail'])

    def test_real_json_and_unicode_body(self):
        request = transport.make_request(command(path='/api/open-access/search', method='POST',
            headers={'Content-Type': 'application/json'}, body={'kind': 'text', 'text': json.dumps({'papers': []})}))
        frames = []; asyncio.run(transport.forward(request, frames.append))
        self.assertEqual(frames[0]['status'], 422)

    def test_title_search_route_is_forwarded_without_ai_credentials(self):
        request = transport.make_request(command(path='/api/metadata/search', method='POST',
            headers={'Content-Type': 'application/json'}, body={'kind': 'text', 'text': json.dumps({'papers': []})}))
        frames = []; asyncio.run(transport.forward(request, frames.append))
        self.assertEqual(frames[0]['status'], 422)  # API validation, not transport rejection.

    def test_requests_are_not_repeated_on_streamlit_rerun(self):
        session = transport.BrowserSession(); ended = threading.Event(); calls = []
        async def app(scope, receive, send):
            calls.append(scope['path'])
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'{}'})
            ended.set()
        with patch.object(transport, 'app', app):
            session.submit(command()); session.submit(command())
            self.assertTrue(ended.wait(3))
        self.assertEqual(calls, ['/api/ai/providers'])

    def test_response_frames_are_replayed_until_acknowledged(self):
        session = transport.BrowserSession()
        session.accept({'type': 'boot', 'frame': 'one', 'nonce': 'boot'})
        session.emit('request-one', {'kind': 'start', 'status': 200})
        first = session.snapshot(); self.assertEqual(first, session.snapshot())
        session.accept({'type': 'control', 'frame': 'other', 'nonce': 'wrong', 'ack': 999})
        self.assertEqual(first, session.snapshot())
        session.accept({'type': 'control', 'frame': 'one', 'nonce': 'ack', 'ack': first['events'][0]['seq']})
        self.assertEqual(session.snapshot()['events'], [])

    def test_sessions_do_not_share_requests_or_results(self):
        first, second = transport.BrowserSession(), transport.BrowserSession()
        first.emit('one', {'kind': 'chunk', 'data': 'private-test'})
        self.assertEqual(second.snapshot()['events'], [])
        self.assertIsNot(first.lock, second.lock)

    def test_stream_is_forwarded_before_request_finishes(self):
        active = False; frames = []
        async def app(scope, receive, send):
            nonlocal active
            active = True
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/x-ndjson')]})
            await send({'type': 'http.response.body', 'body': 'ก้าวหน้า\n'.encode(), 'more_body': True})
            self.assertEqual(len(frames), 2)
            self.assertEqual(base64.b64decode(frames[1]['data']).decode(), 'ก้าวหน้า\n')
            await send({'type': 'http.response.body', 'body': b'finished\n'})
            active = False
        def emit(event):
            self.assertTrue(active); frames.append(event)
        with patch.object(transport, 'app', app):
            asyncio.run(transport.forward(transport.make_request(command()), emit))
        self.assertEqual(frames[-1]['kind'], 'end')

    def test_cancellation_stops_running_asgi_request(self):
        started = threading.Event(); cancelled = threading.Event()
        async def app(scope, receive, send):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        session = transport.BrowserSession()
        session.accept({'type': 'boot', 'frame': 'one', 'nonce': 'boot'})
        with patch.object(transport, 'app', app):
            session.submit(command())
            self.assertTrue(started.wait(3))
            session.accept({'type': 'control', 'frame': 'one', 'nonce': 'cancel', 'cancel': ['request-one']})
            self.assertTrue(cancelled.wait(3))

    def test_bad_key_error_does_not_expose_key(self):
        session = transport.BrowserSession()
        session.submit(command(headers={'X-AI-API-Key': 'fake-คีย์-private'}))
        # Thread completion barrier for this local validation failure.
        for _ in range(100):
            with session.lock:
                if not session.jobs: break
            threading.Event().wait(.005)
        events = session.snapshot()['events']
        self.assertEqual(events[-1]['kind'], 'error')
        self.assertNotIn('private', str(events))
