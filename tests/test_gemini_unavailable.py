"""Gemini temporary-capacity recovery is explicit, bounded and transport-independent."""
import asyncio
import base64
from contextlib import contextmanager, ExitStack
import json
import re
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import ai_economy
import ai_engine
import ai_features
from main import app
import streamlit_transport


KEY = 'private-gemini-unavailable-test-key'
PDF = b'%PDF-1.4 in-memory fixture'
CHUNKS = ['\n'.join(f'[{n}] A. Author, Research title {n}, 2024.'
                    for n in range(start, start + 2)) for start in (1, 3, 5)]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.real_sleep = asyncio.sleep

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await self.real_sleep(0)


@contextmanager
def fixture(generator, chunks, clock=None):
    clock = clock or Clock()
    wait_helper = ai_features._wait_gemini_retry

    def caller(*args, **kwargs):
        return ai_economy.EconomyCaller(*args, **kwargs,
            clock=clock.monotonic, sleeper=clock.sleep)

    async def retry_wait(*args, **kwargs):
        return await wait_helper(*args, **kwargs,
            clock=clock.monotonic, sleeper=clock.sleep)

    async def resolve(refs):
        return [{'title': ref.title, 'original_text': ref.original_text} for ref in refs]

    with ExitStack() as stack:
        for name, value in [('generate', generator), ('EconomyCaller', caller),
                            ('_wait_gemini_retry', retry_wait),
                            ('resolve_references', resolve)]:
            stack.enter_context(patch.object(ai_features, name, value))
        for name, value in [('split_bibliography_batches', chunks),
                            ('extract_bibliography_text', '\n'.join(chunks)),
                            ('extract_citation_contexts', {}),
                            ('extract_citation_counts', (False, {})),
                            ('extract_source_metadata', {'title': 'Source paper'})]:
            stack.enter_context(patch.object(ai_features, name, return_value=value))
        yield clock


def references(prompt):
    excerpt = prompt.split('Bibliography excerpt:\n', 1)[1].split('\nEconomy mode:', 1)[0]
    numbers = [int(n) for n in re.findall(r'(?m)^\[(\d+)\]', excerpt)]
    return {'references': [{'number': n, 'title': f'Research title {n}',
                            'authors': ['A. Author'], 'year': '2024', 'doi': None,
                            'original_text': f'A. Author, Research title {n}, 2024.'}
                           for n in numbers]}


class ProviderUnavailableTests(unittest.IsolatedAsyncioTestCase):
    def test_503_message_and_headers_are_safe_and_bounded(self):
        for value, expected in [(None, None), ('0', 0), ('45', 45), (' 60 ', 60),
                                ('300', 300), ('301', 300), ('9999999999', 300),
                                ('-1', None), ('1.5', None), ('3e2', None),
                                ('Wed, 21 Oct 2015 07:28:00 GMT', None),
                                (KEY, None), ('9' * 1000, None)]:
            with self.subTest(value=value):
                response = httpx.Response(503, headers={'Retry-After': value} if value else {},
                    json={'error': {'status': 'UNAVAILABLE', 'message': KEY + ' private body'}})
                with self.assertLogs(ai_engine.logger, level='WARNING') as captured:
                    with self.assertRaises(HTTPException) as raised:
                        ai_engine.raise_provider_error(response, 'gemini')
                error = raised.exception
                self.assertEqual(error.status_code, 503)
                self.assertIn('ชั่วคราว', error.detail)
                self.assertIn('ไม่ได้ยืนยันว่าต้องชำระเงิน', error.detail)
                self.assertEqual(error.headers, {'Retry-After': str(expected)} if expected is not None else None)
                self.assertNotIn(KEY, error.detail + str(error.headers) + '\n'.join(captured.output))
                self.assertNotIn('private body', error.detail)

    def test_other_unknown_server_errors_still_map_to_gateway(self):
        for status in (500, 501, 507, 599):
            with self.subTest(status=status), self.assertRaises(HTTPException) as raised:
                ai_engine.raise_provider_error(httpx.Response(status), 'gemini')
            self.assertEqual(raised.exception.status_code, 502)

    async def test_adapter_never_retries_503(self):
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(503, headers={'Retry-After': '41'},
                json={'error': {'status': 'UNAVAILABLE', 'message': 'secret ' + KEY}})
        real_client = httpx.AsyncClient
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(ai_engine.httpx, 'AsyncClient', client), self.assertRaises(HTTPException) as raised:
            await ai_engine.generate('gemini', 'gemini-2.5-flash', KEY, 'Short text only', economy_mode=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.headers, {'Retry-After': '41'})
        self.assertNotIn(KEY, raised.exception.detail)
        self.assertIn('text', json.loads(calls[0].content)['contents'][0]['parts'][0])


class ExtractionUnavailableTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, outcomes, *, chunks=None, retry='true', economy='true',
                       provider='gemini', stream=False, cloud=False, retry_after=None,
                       interval=30):
        chunks = chunks if chunks is not None else CHUNKS[:2]
        clock, calls = Clock(), []
        async def generator(*args, **kwargs):
            calls.append({'args': args, 'kwargs': kwargs, 'started': clock.now})
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, int):
                ai_engine.raise_provider_error(httpx.Response(outcome,
                    headers={'Retry-After': retry_after} if retry_after else {},
                    json={'error': {'message': KEY + ' private upstream body'}}), provider)
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome == 'invalid':
                return {'unexpected': 'format'}
            return references(args[3])

        fields = {'provider': provider, 'model': 'gemini-2.5-flash' if provider == 'gemini' else 'test-model',
                  'economy_mode': economy, 'economy_interval': str(interval)}
        if retry is not None:
            fields['retry_unavailable'] = retry
        if stream:
            fields['stream'] = 'true'
        with fixture(generator, chunks, clock):
            if cloud:
                entries = [{'name': k, 'value': v} for k, v in fields.items()] + [
                    {'name': 'file', 'file': True, 'filename': 'paper.pdf',
                     'mime': 'application/pdf', 'data': base64.b64encode(PDF).decode()}]
                request = streamlit_transport.make_request({'method': 'POST',
                    'path': '/api/ai/extract-references', 'headers': {'X-AI-API-Key': KEY},
                    'body': {'kind': 'form', 'entries': entries}})
                frames = []
                await streamlit_transport.forward(request, frames.append)
                body = b''.join(base64.b64decode(f['data']) for f in frames if f['kind'] == 'chunk')
                self.assertEqual(frames[-1]['kind'], 'end')
                response = httpx.Response(frames[0]['status'], headers=frames[0]['headers'], content=body)
            else:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                               base_url='http://unavailable.test') as client:
                    response = await client.post('/api/ai/extract-references', data=fields,
                        headers={'X-AI-API-Key': KEY},
                        files={'file': ('paper.pdf', PDF, 'application/pdf')})
        self.assertNotIn(KEY, response.text)
        self.assertNotIn('private upstream body', response.text)
        return response, calls, clock

    async def test_default_and_explicit_disabled_make_one_call(self):
        for retry in (None, 'false'):
            with self.subTest(retry=retry):
                response, calls, clock = await self.run_case([503], retry=retry, retry_after='75')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers['retry-after'], '75')
                self.assertIn('ส่วนที่ 1/2', response.json()['detail'])
                self.assertEqual(len(calls), 1)
                self.assertEqual(clock.sleeps, [])

    async def test_retry_only_failed_part_preserves_all_ids_and_call_accounting(self):
        response, calls, clock = await self.run_case(['ok', 503, 'ok'], stream=True)
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        retry_events = [e for e in events if e.get('stage') == 'retrying']
        self.assertTrue(retry_events)
        self.assertEqual(retry_events[0], {'type': 'progress', 'stage': 'retrying',
            'wait_seconds': 30, 'part': 2, 'total': 2, 'attempt': 1, 'max_retries': 1})
        self.assertEqual(events[-1]['type'], 'result')
        result = events[-1]['payload']
        self.assertEqual(result['retried_extraction_batches'], [2])
        self.assertEqual([r['reference_number'] for r in result['results']], [1, 2, 3, 4])
        self.assertEqual(result['economy']['requests'], 3)
        self.assertEqual(result['economy']['prompt_characters'], sum(len(c['args'][3]) for c in calls))
        self.assertEqual([c['started'] for c in calls], [0, 30, 60])
        self.assertNotEqual(calls[0]['args'][3], calls[1]['args'][3])
        self.assertEqual(calls[1]['args'], calls[2]['args'])
        self.assertEqual(calls[1]['kwargs'], calls[2]['kwargs'])
        self.assertTrue(all(s <= 1 for s in clock.sleeps))

    async def test_backoff_honors_interval_and_bounded_retry_after(self):
        for interval, header, expected in [(60, '5', 60), (30, '83', 83),
                                          (30, '900', 300), (30, KEY, 30)]:
            with self.subTest(interval=interval, header=header):
                response, calls, _ = await self.run_case([503, 'ok'], chunks=CHUNKS[:1],
                    interval=interval, retry_after=header)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([c['started'] for c in calls], [0, expected])
                self.assertEqual(response.json()['retried_extraction_batches'], [1])

    async def test_persistent_503_stops_after_one_extra_call_and_keeps_header(self):
        response, calls, _ = await self.run_case([503, 503], retry_after='81')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['retry-after'], '81')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['args'], calls[1]['args'])
        self.assertIn('ส่วนที่ 1/2', response.json()['detail'])

    async def test_one_retry_budget_for_entire_action_not_each_batch(self):
        response, calls, _ = await self.run_case(['ok', 503, 'ok', 503], chunks=CHUNKS, stream=True)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]['status'], 503)
        self.assertIn('ส่วนที่ 3/3', events[-1]['detail'])
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1]['args'], calls[2]['args'])
        self.assertNotEqual(calls[0]['args'][3], calls[1]['args'][3])
        self.assertNotEqual(calls[2]['args'][3], calls[3]['args'][3])
        self.assertEqual({e['part'] for e in events if e.get('stage') == 'retrying'}, {2})

    async def test_noncapacity_errors_and_timeout_never_retry(self):
        for status, expected in [(429, 429), (401, 401), (402, 402), (403, 403),
                                 (404, 422), (504, 504), (500, 502)]:
            with self.subTest(status=status):
                response, calls, clock = await self.run_case([status], retry_after='30')
                self.assertEqual(response.status_code, expected)
                self.assertEqual(len(calls), 1)
                self.assertEqual(clock.sleeps, [])

    async def test_other_providers_and_ordinary_mode_never_retry(self):
        for provider, economy in [('maxplus', 'true'), ('openai', 'true'),
                                 ('claude', 'true'), ('alibaba', 'true'), ('gemini', 'false')]:
            with self.subTest(provider=provider, economy=economy):
                response, calls, clock = await self.run_case([503], chunks=CHUNKS[:1],
                    provider=provider, economy=economy)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(len(calls), 1)
                self.assertEqual(clock.sleeps, [])

    async def test_malformed_outputs_are_recovered_without_retry(self):
        for malformed in ('invalid', ai_engine.AIResponseFormatError(502, 'Bad JSON')):
            with self.subTest(malformed=malformed):
                response, calls, _ = await self.run_case([malformed], chunks=CHUNKS[:1])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(calls), 1)
                self.assertNotIn('retried_extraction_batches', response.json())
                self.assertEqual(response.json()['failed_extraction_batches'], [1])

    async def test_stream_error_contains_only_sanitized_wait_hint(self):
        for hint, expected in [('999', 300), (KEY, None)]:
            with self.subTest(hint=hint):
                response, calls, _ = await self.run_case([503], retry='false', stream=True, retry_after=hint)
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(events[-1]['type'], 'error')
                self.assertEqual(events[-1]['status'], 503)
                self.assertEqual(events[-1].get('retry_after_seconds'), expected)
                self.assertEqual(len(calls), 1)

    async def test_streamlit_and_local_forward_identical_calls_results_and_errors(self):
        for outcomes, retry in [([503], 'false'), ([503, 'ok', 'ok'], 'true'),
                                ([503, 503], 'true')]:
            for stream in (False, True):
                with self.subTest(outcomes=outcomes, retry=retry, stream=stream):
                    local, local_calls, _ = await self.run_case(outcomes, retry=retry,
                        stream=stream, retry_after='45')
                    cloud, cloud_calls, _ = await self.run_case(outcomes, retry=retry,
                        stream=stream, retry_after='45', cloud=True)
                    self.assertEqual(local.status_code, cloud.status_code)
                    self.assertEqual(local.text, cloud.text)
                    self.assertEqual(local.headers.get('retry-after'), cloud.headers.get('retry-after'))
                    self.assertEqual(local_calls, cloud_calls)

    async def test_cancel_during_backoff_prevents_extra_provider_call(self):
        waiting = asyncio.Event()
        clock = Clock()
        async def blocked_sleep(seconds):
            waiting.set()
            await asyncio.Event().wait()
        clock.sleep = blocked_sleep
        generation = AsyncMock(side_effect=HTTPException(503, 'Gemini temporarily unavailable'))
        with fixture(generation, CHUNKS, clock):
            task = asyncio.create_task(ai_features.extract_bibliography_result(
                PDF, '\n'.join(CHUNKS), 'gemini', 'gemini-2.5-flash', KEY, None,
                economy_mode=True, retry_unavailable=True))
            await asyncio.wait_for(waiting.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        generation.assert_awaited_once()

    async def test_retry_flag_has_no_effect_on_other_features(self):
        generation = AsyncMock(side_effect=HTTPException(503, 'Gemini temporarily unavailable'))
        with patch.object(ai_features, 'source_material', AsyncMock(return_value=('Short source', 'DOI', None))), \
             patch.object(ai_features, 'generate', generation):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://qa.test') as client:
                response = await client.post('/api/ai/qa', headers={'X-AI-API-Key': KEY},
                    data={'provider': 'gemini', 'model': 'gemini-2.5-flash', 'doi': '10.1234/test',
                          'economy_mode': 'true', 'retry_unavailable': 'true',
                          'payload': json.dumps({'question': 'What did the study find?'})})
        self.assertEqual(response.status_code, 503)
        generation.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
