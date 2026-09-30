"""Rate-limited gateways must not receive parallel bibliography calls or retries."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

import ai_features
from ai_engine import raise_provider_error
from main import app


class ExtractionRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_maxplus_serial_batches_keep_all_references(self):
        source = '\n'.join(f'[{i}] A. Author, Battery research topic {i}, 2024.'
                           for i in range(1, 65))
        active = maximum = 0

        async def generate(*args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.001)
                excerpt = args[3].split('Bibliography excerpt:\n')[1]
                import re
                return {'references': [{'number': int(n), 'title': f'Battery research topic {n}',
                        'authors': [], 'year': '2024', 'doi': None,
                        'original_text': f'A. Author, Battery research topic {n}, 2024.'}
                        for n in re.findall(r'\[(\d+)\]', excerpt)]}
            finally:
                active -= 1

        async def resolve(refs):
            return [{'title': ref.title} for ref in refs]

        with patch.object(ai_features, 'generate', generate), \
             patch.object(ai_features, 'resolve_references', resolve), \
             patch.object(ai_features, 'extract_citation_contexts', return_value={}), \
             patch.object(ai_features, 'extract_citation_counts', return_value=(False, {})):
            result = await ai_features.extract_bibliography_result(
                b'fake', source, 'maxplus', 'test', 'fake-key', None)
        self.assertEqual(maximum, 1)
        self.assertEqual([r['reference_number'] for r in result['results']], list(range(1, 65)))

    async def test_stream_reports_429_and_does_not_retry(self):
        calls = 0

        async def limited(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise_provider_error(httpx.Response(429, headers={'Retry-After': '30'},
                                 json={'error': {'message': 'private provider content'}}), 'maxplus')

        with patch.object(ai_features, 'generate', limited), \
             patch.object(ai_features, 'extract_bibliography_text', return_value='[1] One reference title\n' * 400):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/ai/extract-references',
                    data={'provider': 'maxplus', 'model': 'test', 'stream': 'true'},
                    files={'file': ('paper.pdf', b'%PDF-1.4 fake', 'application/pdf')},
                    headers={'X-AI-API-Key': 'fake-key'})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(calls, 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events[-1]['status'], 429)
        self.assertIn('30', events[-1]['detail'])
        self.assertNotIn('private provider content', response.text)
        self.assertFalse(any(event['type'] == 'result' for event in events))

    def test_untrusted_retry_after_not_displayed(self):
        with self.assertRaises(HTTPException) as caught:
            raise_provider_error(httpx.Response(429, headers={'Retry-After': 'secret-text'}), 'maxplus')
        self.assertEqual(caught.exception.status_code, 429)
        self.assertNotIn('secret-text', caught.exception.detail)
