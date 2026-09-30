"""Deployment adapter: preserve multipart APIs and incremental stream errors."""
import json
import unittest
from unittest.mock import patch

import streamlit_backend
from streamlit_backend import APIError, call_api
from streamlit_graph import graph_html


class StreamlitBackendTests(unittest.TestCase):
    def test_real_api_validation(self):
        providers = call_api('/api/ai/providers', method='GET')
        self.assertIn('maxplus', providers['providers'])
        with self.assertRaises(APIError) as caught:
            call_api('/api/summarize', data={'doi': '10.1234/test'})
        self.assertEqual(caught.exception.status, 400)

    def test_multipart_upload_uses_existing_endpoint(self):
        with self.assertRaises(APIError) as caught:
            call_api('/api/references', files={'file': ('paper.pdf', b'not a PDF', 'application/pdf')})
        self.assertEqual(caught.exception.status, 415)

    def test_stream_callbacks_run_before_response_finishes(self):
        ended = False; received = []
        async def app(scope, receive, send):
            nonlocal ended
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/x-ndjson')]})
            payload = (json.dumps({'type': 'progress', 'completed': 1, 'total': 2}) + '\n').encode()
            await send({'type': 'http.response.body', 'body': payload[:10], 'more_body': True})
            await send({'type': 'http.response.body', 'body': payload[10:], 'more_body': True})
            self.assertEqual(received, ['progress'])
            await send({'type': 'http.response.body', 'body': b'{"type":"result","payload":{"total_references":2}}\n'})
            ended = True
        def on_event(event):
            self.assertFalse(ended); received.append(event['type'])
        with patch.object(streamlit_backend, 'app', app):
            result = call_api('/api/ai/extract-references', on_event=on_event)
        self.assertEqual(result['total_references'], 2)
        self.assertEqual(received, ['progress', 'result'])

    def test_stream_error_is_not_treated_as_http_200_success(self):
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/x-ndjson')]})
            await send({'type': 'http.response.body', 'body': b'{"type":"error","status":429,"detail":"Rate limited"}\n'})
        with patch.object(streamlit_backend, 'app', app), self.assertRaises(APIError) as caught:
            call_api('/api/ai/extract-references')
        self.assertEqual(caught.exception.status, 429)

    def test_nonlocal_url_is_rejected(self):
        with self.assertRaises(ValueError):
            call_api('https://example.org/api/references')

    def test_non_ascii_key_error_does_not_expose_key(self):
        with self.assertRaises(APIError) as caught:
            call_api('/api/summarize', headers={'X-AI-API-Key': 'fake-คีย์-private'})
        self.assertEqual(caught.exception.status, 422)
        self.assertNotIn('private', str(caught.exception))

    def test_graph_escapes_paper_text_in_script(self):
        html = graph_html([{'reference_number': 1, 'title': '</script><script>alert(1)</script>'}], '</script>')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('\\u003c/script>', html)
