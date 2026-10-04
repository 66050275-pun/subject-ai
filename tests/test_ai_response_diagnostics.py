"""Planning parses explicit theme lists without losing provider output diagnostics."""
import json
import unittest
from unittest.mock import patch

import httpx

import ai_engine
from ai_cluster_batches import parse_themes


class AIResponseDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, body, provider='maxplus', **kwargs):
        original = httpx.AsyncClient
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=body)
        with patch.object(ai_engine.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
            try:
                return await ai_engine.generate(provider, 'test', 'fake-key', 'Themes', structured=True, **kwargs)
            finally:
                self.assertEqual(len(requests), 1)

    async def test_custom_planning_parser_reaches_raw_text(self):
        result = await self.request({'choices': [{'message': {'content': '1. Battery recycling\n2. Degradation detection'}, 'finish_reason': 'stop'}]}, response_parser=parse_themes)
        self.assertEqual([r['name'] for r in result], ['Battery recycling', 'Degradation detection'])

    async def test_complete_result_at_output_limit_remains_valid(self):
        result = await self.request({'choices': [{'message': {'content': '{"themes":["Battery aging"]}'}, 'finish_reason': 'length'}]}, response_parser=parse_themes)
        self.assertEqual(result, [{'id': 1, 'name': 'Battery aging'}])

    async def test_truncated_output_has_safe_reason_across_providers(self):
        for provider, body in [
            ('maxplus', {'choices': [{'message': {'content': '{"themes":['}, 'finish_reason': 'length'}]}),
            ('gemini', {'candidates': [{'content': {'parts': [{'text': '{"themes":['}]}, 'finishReason': 'MAX_TOKENS'}]}),
            ('claude', {'content': [{'type': 'text', 'text': '{"themes":['}], 'stop_reason': 'max_tokens'}),
        ]:
            with self.subTest(provider=provider), self.assertRaises(ai_engine.AIResponseFormatError) as caught:
                await self.request(body, provider, response_parser=parse_themes)
            self.assertEqual(caught.exception.reason, 'output_limit')
            self.assertNotIn('fake-key', str(caught.exception.detail))

    async def test_empty_content_is_not_taken_from_reasoning(self):
        with self.assertRaises(ai_engine.AIResponseFormatError) as caught:
            await self.request({'choices': [{'message': {'content': '', 'reasoning_content': '{"themes":["Guess"]}'}, 'finish_reason': 'stop'}]}, response_parser=parse_themes)
        self.assertEqual(caught.exception.reason, 'empty_response')

    def test_closed_reasoning_block_is_excluded_from_final_json(self):
        raw = '<think>{"themes":["Example reasoning"]}</think>\n{"themes":["Actual theme"]}'
        self.assertEqual(parse_themes(raw), [{'id': 1, 'name': 'Actual theme'}])
        for parser in (parse_themes, ai_engine.decode_structured_response):
            with self.assertRaises(ValueError):
                parser('<think>{"themes":["Reasoning only"]}')
