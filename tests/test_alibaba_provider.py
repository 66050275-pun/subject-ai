"""Official regional Alibaba adapter contracts; no paid or live API calls."""
import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import httpx

import ai_engine
import ai_features
from ai_cluster_batches import parse_themes
from main import app
import streamlit_transport

Client = httpx.AsyncClient
KEY = 'fake-alibaba-test-key'
CHINA = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
REGIONS = [
    'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
    CHINA,
    'https://dashscope-us.aliyuncs.com/compatible-mode/v1',
    'https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1',
]


class AlibabaValidationTests(unittest.TestCase):
    def test_provider_defaults_and_trimmed_key(self):
        self.assertEqual(ai_engine.normalize_credentials('alibaba', '', ' "' + KEY + '" '),
                         ('qwen-plus', KEY))

    def test_official_regional_endpoints_are_canonical(self):
        self.assertEqual(ai_engine.normalize_alibaba_url(), REGIONS[0])
        for endpoint in REGIONS:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(ai_engine.normalize_provider_base_url('alibaba', endpoint + '/'), endpoint)
                self.assertEqual(ai_engine.normalize_alibaba_url(endpoint + '/chat/completions'), endpoint)

    def test_unofficial_urls_and_endpoint_credentials_are_rejected(self):
        for endpoint in (
            'http://dashscope.aliyuncs.com/compatible-mode/v1',
            'https://dashscope.aliyuncs.com.evil.example/compatible-mode/v1',
            'https://fake:secret@dashscope.aliyuncs.com/compatible-mode/v1',
            CHINA + '?api_key=fake-secret', CHINA + '#fake-secret',
            CHINA.replace('.com/', '.com:443/'),
            'https://127.0.0.1/compatible-mode/v1',
            'https://dashscope.aliyuncs.com/api/v1', CHINA + '/models',
            'https://coding.dashscope.aliyuncs.com/v1',
            'https://[malformed/compatible-mode/v1',
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(HTTPException) as caught:
                ai_engine.normalize_alibaba_url(endpoint)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertNotIn('fake-secret', caught.exception.detail)

    def test_invalid_model_and_key_are_rejected(self):
        for model, key in [('https://model.example/qwen-plus', KEY), ('qwen plus', KEY),
                           ('qwen-plus', ''), ('qwen-plus', 'bad\nkey')]:
            with self.subTest(model=model), self.assertRaises(HTTPException):
                ai_engine.normalize_credentials('alibaba', model, key)


class AlibabaAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, *, status=200, content='Research summary', model='', structured=False,
                   finish_reason='stop', **kwargs):
        requests, options = [], []
        def respond(request):
            requests.append(request)
            if status == 200:
                return httpx.Response(status, json={'choices': [
                    {'message': {'content': content}, 'finish_reason': finish_reason}]})
            return httpx.Response(status, json={'error': {'message': 'private response ' + KEY}},
                                  headers={'Location': 'https://external.example/', 'Retry-After': '5'})
        def factory(**params):
            options.append(params)
            return Client(transport=httpx.MockTransport(respond), **params)
        with patch.object(ai_engine.httpx, 'AsyncClient', factory):
            try:
                result = await ai_engine.generate('alibaba', model, KEY, 'Paper evidence',
                                                  structured=structured, **kwargs)
                return result, requests
            finally:
                self.assertEqual(len(requests), 1)
                self.assertFalse(options[0]['follow_redirects'])

    async def test_default_generate_uses_header_and_compatible_tokens(self):
        result, requests = await self.call(max_output_tokens=1700)
        self.assertEqual(result, 'Research summary')
        request = requests[0]
        self.assertEqual(str(request.url), REGIONS[0] + '/chat/completions')
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + KEY)
        self.assertNotIn(KEY, str(request.url))
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'qwen-plus')
        self.assertEqual(body['max_tokens'], 1700)
        self.assertNotIn('max_completion_tokens', body)
        self.assertFalse(body['enable_thinking'])
        self.assertEqual(body['messages'][1]['content'], 'Paper evidence')

    async def test_each_region_preserves_its_official_destination(self):
        for endpoint in REGIONS:
            with self.subTest(endpoint=endpoint):
                _, requests = await self.call(base_url=endpoint)
                self.assertEqual(str(requests[0].url), endpoint + '/chat/completions')

    async def test_structured_qwen_result_uses_json_and_shared_decoder(self):
        result, requests = await self.call(content='{"clusters":[{"name":"Batteries","ids":[1]}]}',
                                           structured=True)
        self.assertEqual(result['clusters'][0]['ids'], [1])
        body = json.loads(requests[0].content)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertIn('JSON', body['messages'][0]['content'])

    async def test_theme_response_parser_receives_generated_content(self):
        result, _ = await self.call(content='{"themes":["Battery aging","Recycling"]}',
                                   structured=True, response_parser=parse_themes)
        self.assertEqual([theme['name'] for theme in result], ['Battery aging', 'Recycling'])

    async def test_truncated_structured_output_has_safe_diagnostic(self):
        with self.assertRaises(ai_engine.AIResponseFormatError) as caught:
            await self.call(content='{"themes":[', structured=True, finish_reason='length')
        self.assertEqual(caught.exception.reason, 'output_limit')
        self.assertNotIn(KEY, caught.exception.detail)

    async def test_qwen38_uses_effort_control_instead_of_thinking_toggle(self):
        for model in ('qwen3.8-max', 'qwen3.8-flash'):
            with self.subTest(model=model):
                _, requests = await self.call(model=model, structured=True, content='{"value":1}')
                body = json.loads(requests[0].content)
                self.assertEqual(body['reasoning_effort'], 'none')
                self.assertNotIn('enable_thinking', body)

    async def test_unknown_model_does_not_receive_assumed_capabilities(self):
        _, requests = await self.call(model='custom-thinking-model', structured=True, content='{"value":1}')
        body = json.loads(requests[0].content)
        self.assertNotIn('enable_thinking', body)
        self.assertNotIn('reasoning_effort', body)
        self.assertNotIn('response_format', body)

    async def test_errors_are_safe_and_preserve_useful_status(self):
        for upstream, application in [(400, 400), (401, 401), (403, 403), (402, 402),
                                      (404, 422), (429, 429), (503, 503)]:
            with self.subTest(status=upstream), self.assertLogs(ai_engine.logger, level='WARNING') as log:
                with self.assertRaises(HTTPException) as caught:
                    await self.call(status=upstream)
            self.assertEqual(caught.exception.status_code, application)
            self.assertIn('Alibaba Cloud Model Studio', caught.exception.detail)
            self.assertNotIn(KEY, caught.exception.detail)
            self.assertNotIn('private response', caught.exception.detail)
            self.assertNotIn(KEY, '\n'.join(log.output))

    async def test_redirect_is_rejected_without_following_location(self):
        with self.assertRaises(HTTPException) as caught:
            await self.call(status=302)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn('external.example', caught.exception.detail)

    async def test_timeout_does_not_expose_request_credentials(self):
        def respond(request):
            raise httpx.ReadTimeout('private timeout ' + KEY, request=request)
        factory = lambda **params: Client(transport=httpx.MockTransport(respond), **params)
        with patch.object(ai_engine.httpx, 'AsyncClient', factory), self.assertRaises(HTTPException) as caught:
            await ai_engine.generate('alibaba', '', KEY, 'Paper')
        self.assertEqual(caught.exception.status_code, 504)
        self.assertNotIn(KEY, caught.exception.detail)


class AlibabaEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_suggested_models_are_not_claimed_as_credential_verification(self):
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_engine.httpx, 'AsyncClient', side_effect=AssertionError('No discovery request')):
                response = await client.get('/api/ai/alibaba-models', params={'base_url': CHINA})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['models'], ['qwen-plus'])
        self.assertEqual(response.json()['base_url'], CHINA)
        self.assertFalse(response.json()['verified'])
        self.assertEqual(response.json()['source'], 'suggested')
        self.assertTrue(response.json()['warning'])
        self.assertNotIn(KEY, response.text)

    async def test_provider_registry_includes_alibaba_default(self):
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/ai/providers')
        self.assertEqual(response.json()['providers']['alibaba'], 'qwen-plus')

    async def test_summary_uses_selected_region_and_default_model(self):
        generate = AsyncMock(return_value='Thai summary')
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_features, 'source_material', AsyncMock(return_value=('Source', 'Test', None))), \
                 patch.object(ai_features, 'generate', generate):
                response = await client.post('/api/summarize', headers={'X-AI-API-Key': KEY},
                    data={'provider': 'alibaba', 'base_url': CHINA + '/chat/completions', 'doi': '10.1234/paper'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_args.args[:3], ('alibaba', 'qwen-plus', KEY))
        self.assertEqual(generate.call_args.kwargs['base_url'], CHINA)
        self.assertNotIn(KEY, response.text)

    async def test_shared_analysis_features_keep_provider_region(self):
        papers = [{'id': i, 'title': f'Battery research paper {i}'} for i in range(1, 4)]
        async def enrich(rows):
            return rows
        cases = [
            ('qa', {'papers': [], 'question': 'What is the method?'}, 'Research answer'),
            ('synthesis', {'papers': papers}, 'Research synthesis'),
            ('intents', {'papers': papers[:1]}, {'intents': [{'id': 1, 'intent': 'Unknown', 'reason': 'No context'}]}),
            ('clusters', {'papers': papers}, {'clusters': [{'name': f'Theme {i}', 'ids': [i]} for i in range(1, 4)]}),
        ]
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for feature, payload, result in cases:
                generate = AsyncMock(return_value=result)
                with self.subTest(feature=feature), \
                     patch.object(ai_features, 'source_material', AsyncMock(return_value=('Source', 'Test', None))), \
                     patch.object(ai_features, 'enrich_ai_references', enrich), \
                     patch.object(ai_features, 'generate', generate):
                    response = await client.post('/api/ai/' + feature, headers={'X-AI-API-Key': KEY},
                        data={'provider': 'alibaba', 'base_url': CHINA, 'payload': json.dumps(payload), 'doi': '10.1234/paper'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(generate.call_args.args[:3], ('alibaba', 'qwen-plus', KEY))
                self.assertEqual(generate.call_args.kwargs['base_url'], CHINA)
                self.assertNotIn(KEY, response.text)

    async def test_extraction_uses_the_same_regional_configuration(self):
        extract = AsyncMock(return_value={'results': [], 'total_references': 0})
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_features, 'read_pdf', AsyncMock(return_value=b'%PDF-test')), \
                 patch.object(ai_features, 'extract_bibliography_text', return_value='References text'), \
                 patch.object(ai_features, 'extract_bibliography_result', extract):
                response = await client.post('/api/ai/extract-references', headers={'X-AI-API-Key': KEY},
                    data={'provider': 'alibaba', 'base_url': CHINA},
                    files={'file': ('paper.pdf', b'%PDF-test', 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(extract.call_args.args[2:6], ('alibaba', 'qwen-plus', KEY, CHINA))

    async def test_workspace_comparison_normalizes_endpoint(self):
        generate = AsyncMock(return_value='Comparison')
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_features, 'generate', generate):
                response = await client.post('/api/ai/compare', headers={'X-AI-API-Key': KEY}, data={
                    'provider': 'alibaba', 'base_url': CHINA + '/',
                    'payload': json.dumps({'papers': [{'title': 'Paper A'}, {'title': 'Paper B'}]})})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_args.kwargs['base_url'], CHINA)
        self.assertEqual(generate.call_args.args[:3], ('alibaba', 'qwen-plus', KEY))

    async def test_invalid_endpoint_is_blocked_before_source_or_generation(self):
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_features, 'source_material', AsyncMock(side_effect=AssertionError('No source access'))), \
                 patch.object(ai_features, 'generate', AsyncMock(side_effect=AssertionError('No paid generation'))):
                for endpoint in ('/api/summarize', '/api/ai/qa', '/api/ai/compare'):
                    with self.subTest(endpoint=endpoint):
                        response = await client.post(endpoint, headers={'X-AI-API-Key': KEY},
                            data={'provider': 'alibaba', 'base_url': 'https://external.example/v1', 'payload': '{}'})
                        self.assertEqual(response.status_code, 422)

    async def test_streamlit_forwards_only_the_new_local_models_route(self):
        request = streamlit_transport.make_request({
            'path': '/api/ai/alibaba-models?base_url=' + CHINA,
            'method': 'GET', 'headers': {'X-AI-API-Key': KEY, 'Authorization': 'discard-this'},
        })
        self.assertEqual(request.headers['X-AI-API-Key'], KEY)
        self.assertNotIn('Authorization', request.headers)
        frames = []
        await streamlit_transport.forward(request, frames.append)
        self.assertEqual(frames[0]['status'], 200)
        body = b''.join(base64.b64decode(frame['data']) for frame in frames if frame['kind'] == 'chunk')
        self.assertFalse(json.loads(body)['verified'])
        for path in ('https://dashscope.aliyuncs.com/compatible-mode/v1', '/api/ai/alibaba-models/anything'):
            with self.assertRaises(ValueError):
                streamlit_transport.make_request({'path': path, 'method': 'GET'})


if __name__ == '__main__':
    unittest.main()
