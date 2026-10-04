"""Economy endpoint contracts, using local ASGI requests and virtual time only."""
import asyncio
from contextlib import contextmanager
import json
import re
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import ai_economy
import ai_features
from ai_engine import AIResponseFormatError, raise_provider_error
from main import app


KEY = 'economy-test-secret-key'
PDF = b'%PDF-1.4 offline fixture'
THEMES = [{'id': i, 'name': f'Theme {i}'} for i in (1, 2, 3)]


def json_after(prompt, label):
    """Read one evidence value without depending on surrounding instructions."""
    match = re.search(re.escape(label) + r'\s*', prompt)
    if not match:
        raise AssertionError(f'Missing prompt evidence label: {label}')
    return json.JSONDecoder().raw_decode(prompt[match.end():])[0]


class VirtualClock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []
        self.real_sleep = asyncio.sleep

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await self.real_sleep(0)


@contextmanager
def virtual_clock():
    clock = VirtualClock()
    def caller(*args, **kwargs):
        return ai_economy.EconomyCaller(*args, **kwargs,
                                        clock=clock.monotonic, sleeper=clock.sleep)
    with patch.object(ai_features, 'EconomyCaller', caller):
        yield clock


def papers(count, *, long=False):
    return [{'id': 100 + i * 3, 'title': f'Reference title {i}' + (' T' * 300 if long else ''),
             'original_text': f'PRIVATE_ORIGINAL_REFERENCE_{i} ' + 'raw bibliography ' * 80}
            for i in range(1, count + 1)]


async def enrich(rows):
    return [{**p, 'abstract': f'Abstract for {p["id"]}. ' + 'Evidence ' * 300,
             'evidence_level': 'abstract'} for p in rows]


class EconomyFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                       base_url='http://economy.test')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def post(self, endpoint, payload=None, *, provider='openai', mode='true',
                   interval=30, stream=False, files=None, extra=None):
        data = {'provider': provider, 'model': 'test-model',
                'economy_mode': mode, 'economy_interval': str(interval)}
        if not endpoint.endswith(('compare', 'extract-references')):
            data['doi'] = '10.1234/offline-paper'
        if payload is not None:
            data['payload'] = json.dumps(payload)
        if stream:
            data['stream'] = 'true'
        if extra:
            data.update(extra)
        return await self.client.post(endpoint, headers={'X-AI-API-Key': KEY},
                                      data=data, files=files)

    def assert_metadata(self, payload, calls, *, interval=30):
        metadata = payload['economy']
        self.assertTrue(metadata['enabled'])
        self.assertEqual(metadata['requests'], len(calls))
        self.assertEqual(metadata['interval_seconds'], interval)
        self.assertEqual(metadata['prompt_characters'], sum(len(args[3]) for args, _ in calls))
        self.assertEqual(metadata['output_token_limits'],
                         [kw['max_output_tokens'] for _, kw in calls])
        self.assertIsInstance(metadata['context_notice'], str)
        self.assertNotIn(KEY, json.dumps(payload))

    async def test_invalid_intervals_block_all_features_before_source_or_ai(self):
        selected = papers(3)
        cases = [('/api/summarize', None), ('/api/ai/qa', {'question': 'What changed?'}),
                 ('/api/ai/synthesis', {'papers': selected}),
                 ('/api/ai/intents', {'papers': selected}),
                 ('/api/ai/clusters', {'papers': selected}),
                 ('/api/ai/extract-references', None),
                 ('/api/ai/compare', {'papers': [{'title': 'Source A'}, {'title': 'Source B'}]})]
        source = AsyncMock(side_effect=AssertionError('Validation must precede source access'))
        generation = AsyncMock(side_effect=AssertionError('Validation must precede paid calls'))
        read_pdf = AsyncMock(side_effect=AssertionError('Validation must precede PDF access'))
        no_body = AsyncMock(side_effect=AssertionError('Validation must precede source selection'))
        with patch.object(ai_features, 'source_material', source), \
             patch.object(ai_features, 'source_without_main_text', no_body), \
             patch.object(ai_features, 'generate', generation), \
             patch.object(ai_features, 'read_pdf', read_pdf):
            for endpoint, payload in cases:
                for interval in (29, 121, 'not-an-interval'):
                    with self.subTest(endpoint=endpoint, interval=interval):
                        response = await self.post(endpoint, payload, interval=interval,
                            files={'file': ('paper.pdf', PDF, 'application/pdf')}
                            if endpoint.endswith('extract-references') else None)
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertIn('economy_interval', response.text)
                        self.assertNotIn(KEY, response.text)
        source.assert_not_awaited()
        generation.assert_not_awaited()
        read_pdf.assert_not_awaited()
        no_body.assert_not_awaited()

    async def test_default_and_explicit_off_keep_full_source_and_existing_generation(self):
        source_text = 'Original source content. ' * 800 + 'FULL_SOURCE_END'
        calls = []
        async def generate(*args, **kwargs):
            calls.append((args, kwargs))
            return 'Summary'
        with virtual_clock() as clock, \
             patch.object(ai_features, 'source_material', AsyncMock(return_value=(source_text, 'PDF', None))), \
             patch.object(ai_features, 'generate', generate):
            default = await self.client.post('/api/summarize', headers={'X-AI-API-Key': KEY},
                data={'provider': 'openai', 'model': 'test-model'})
            disabled = await self.post('/api/summarize', mode='false')
        self.assertEqual(default.status_code, 200, default.text)
        self.assertEqual(default.json(), disabled.json())
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(calls[0][0][3].endswith(source_text))
        self.assertEqual(clock.waits, [])
        self.assertNotIn('economy', default.json())

    async def test_summary_compacts_source_and_reports_actual_budget(self):
        source_text = 'ABSTRACT_FIRST: useful opening evidence.\n\n' + 'Source detail. ' * 850 \
            + 'SOURCE_MIDDLE_MUST_OMIT ' + 'Source detail. ' * 850
        generation = AsyncMock(return_value='Thai summary')
        with patch.object(ai_features, 'source_material', AsyncMock(return_value=(source_text, 'PDF', None))), \
             patch.object(ai_features, 'generate', generation):
            response = await self.post('/api/summarize')
        self.assertEqual(response.status_code, 200, response.text)
        prompt = generation.call_args.args[3]
        self.assertIn('ABSTRACT_FIRST', prompt)
        self.assertNotIn('SOURCE_MIDDLE_MUST_OMIT', prompt)
        self.assertLess(len(prompt), 6500)
        self.assertEqual(generation.call_args.kwargs['max_output_tokens'], 1600)
        self.assert_metadata(response.json(), [(generation.call_args.args, generation.call_args.kwargs)])

    async def test_synthesis_keeps_every_selected_id_and_compact_reference_evidence(self):
        selected = papers(3, long=True)
        source = 'Source abstract.\n\n' + 'Research source content. ' * 900 + 'SOURCE_TAIL'
        generation = AsyncMock(return_value='Synthesis')
        with patch.object(ai_features, 'source_material', AsyncMock(return_value=(source, 'PDF', None))), \
             patch.object(ai_features, 'enrich_ai_references', enrich), \
             patch.object(ai_features, 'generate', generation):
            response = await self.post('/api/ai/synthesis', {'papers': selected})
        self.assertEqual(response.status_code, 200, response.text)
        prompt = generation.call_args.args[3]
        source_evidence = prompt.split('Source:\n', 1)[1].split('\nReferences:', 1)[0]
        self.assertLessEqual(len(source_evidence), 6000)
        references = json_after(prompt, 'References:')
        self.assertEqual([row['id'] for row in references], [p['id'] for p in selected])
        for row in references:
            self.assertLessEqual(len(row['title']), 160)
            self.assertLessEqual(len(row['abstract']), 600)
            self.assertNotIn('original_text', row)
        self.assertNotIn('PRIVATE_ORIGINAL_REFERENCE', prompt)
        self.assertEqual(generation.call_args.kwargs['max_output_tokens'], 2000)
        self.assert_metadata(response.json(), [(generation.call_args.args, generation.call_args.kwargs)])

    async def test_qa_selects_late_relevant_paragraphs_and_neighbors_with_four_recent_messages(self):
        selected = papers(25, long=True)
        irrelevant = '\n\n'.join(f'Unrelated catalogue paragraph {i}. ' + 'generic filler ' * 65
                                  for i in range(20))
        source = irrelevant + '\n\nNEIGHBOR_BEFORE: sample preparation.\n\n' \
            'ISOTOPE_RESULT: isotope tracing detected carbon transfer at 42 percent.\n\n' \
            'NEIGHBOR_AFTER: uncertainty came from instrument calibration.'
        history = [{'role': 'user' if i % 2 == 0 else 'assistant',
                    'content': f'HISTORY_{i:02d}: ' + 'conversation ' * 110} for i in range(10)]
        generation = AsyncMock(return_value='The source reported 42 percent.')
        with patch.object(ai_features, 'source_material', AsyncMock(return_value=(source, 'PDF', None))), \
             patch.object(ai_features, 'enrich_ai_references', enrich), \
             patch.object(ai_features, 'generate', generation):
            response = await self.post('/api/ai/qa', {'papers': selected, 'history': history,
                                                     'question': 'What did isotope tracing detect?'})
        self.assertEqual(response.status_code, 200, response.text)
        prompt = generation.call_args.args[3]
        source_evidence = prompt.split('Source:\n', 1)[1].split('\nReferences:', 1)[0]
        self.assertLessEqual(len(source_evidence), 6000)
        for marker in ('ISOTOPE_RESULT', 'NEIGHBOR_BEFORE', 'NEIGHBOR_AFTER'):
            self.assertIn(marker, source_evidence)
        references = json_after(prompt, 'References:')
        self.assertEqual([row['id'] for row in references], [p['id'] for p in selected])
        for row in references:
            self.assertLessEqual(len(row['title']), 160)
            self.assertLessEqual(len(row['abstract']), 600)
            self.assertNotIn('original_text', row)
        recent = json_after(prompt, 'Conversation (untrusted):')
        self.assertEqual(len(recent), 4)
        self.assertTrue(all(len(row['content']) <= 600 for row in recent))
        self.assertEqual([row['role'] for row in recent], [row['role'] for row in history[-4:]])
        for i in range(6):
            self.assertNotIn(f'HISTORY_{i:02d}', prompt)
        for i in range(6, 10):
            self.assertIn(f'HISTORY_{i:02d}', prompt)
        self.assertEqual(generation.call_args.kwargs['max_output_tokens'], 1400)
        self.assert_metadata(response.json(), [(generation.call_args.args, generation.call_args.kwargs)])

    async def test_comparison_compacts_each_snapshot_and_preserves_all_reference_ids(self):
        snapshots = [{'title': f'Source {i}', 'abstract': 'Abstract material. ' * 420,
                      'summary': 'Saved summary material. ' * 460, 'references': papers(18, long=True)}
                     for i in (1, 2)]
        generation = AsyncMock(return_value='Comparison')
        with patch.object(ai_features, 'generate', generation), \
             patch.object(ai_features, 'source_material', AsyncMock(side_effect=AssertionError('Local snapshot only'))), \
             patch.object(ai_features, 'enrich_ai_references', AsyncMock(side_effect=AssertionError('Local snapshot only'))):
            response = await self.post('/api/ai/compare', {'papers': snapshots})
        self.assertEqual(response.status_code, 200, response.text)
        prompt = generation.call_args.args[3]
        start = prompt.index('{"papers"')
        evidence = json.JSONDecoder().raw_decode(prompt[start:])[0]['papers']
        self.assertEqual(len(evidence), 2)
        self.assertLessEqual(sum(len(row['source_excerpt']) for row in evidence), 6000)
        for row, snapshot in zip(evidence, snapshots):
            self.assertTrue(row['source_excerpt'])
            self.assertNotIn('abstract', row)
            self.assertNotIn('summary', row)
            self.assertEqual([ref['id'] for ref in row['references']],
                             [ref['id'] for ref in snapshot['references']])
            self.assertTrue(all(len(ref['title']) <= 160 for ref in row['references']))
            self.assertTrue(all('original_text' not in ref for ref in row['references']))
        self.assertNotIn('PRIVATE_ORIGINAL_REFERENCE', prompt)
        self.assertEqual(generation.call_args.kwargs['max_output_tokens'], 2200)
        self.assert_metadata(response.json(), [(generation.call_args.args, generation.call_args.kwargs)])

    async def test_intents_without_local_context_return_every_id_without_paid_calls(self):
        selected = papers(19)
        generation = AsyncMock(side_effect=AssertionError('No evidence means no paid intent call'))
        with patch.object(ai_features, 'source_material', AsyncMock(return_value=('Source body', 'DOI', None))), \
             patch.object(ai_features, 'generate', generation), \
             patch.object(ai_features, 'enrich_ai_references', AsyncMock(side_effect=AssertionError('Intent uses local contexts'))):
            response = await self.post('/api/ai/intents', {'papers': selected})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([row['id'] for row in result['intents']], [p['id'] for p in selected])
        self.assertTrue(all(row['intent'] == 'Unknown' for row in result['intents']))
        generation.assert_not_awaited()
        self.assert_metadata(result, [])

    async def test_intent_batches_send_only_citation_contexts_and_start_serially(self):
        selected = papers(22, long=True)
        contexts = {p['id']: [{'page': 2, 'text': f'LOCAL_CONTEXT_{p["id"]} ' + 'method evidence ' * 70,
                               'method': 'numbered-marker'}] for p in selected[:-3]}
        for provider, batch_size in [('maxplus', 8), ('openai', 12)]:
            calls, starts = [], []
            active = maximum = 0
            with virtual_clock() as clock:
                async def generate(*args, **kwargs):
                    nonlocal active, maximum
                    active += 1
                    maximum = max(maximum, active)
                    starts.append(clock.now)
                    calls.append((args, kwargs))
                    try:
                        rows = json_after(args[3], 'Evidence:')
                        self.assertLessEqual(len(rows), batch_size)
                        self.assertNotIn('MAIN_BODY_MUST_NOT_BE_SENT', args[3])
                        self.assertNotIn('PRIVATE_ORIGINAL_REFERENCE', args[3])
                        for row in rows:
                            self.assertIn(row['id'], contexts)
                            self.assertLessEqual(len(row['title']), 160)
                            self.assertTrue(row['contexts'])
                            self.assertTrue(all(len(c['text']) <= 500 for c in row['contexts']))
                        await clock.real_sleep(0)
                        return {'intents': [{'id': row['id'], 'intent': 'Methodology', 'reason': 'Local citation context'}
                                           for row in rows]}
                    finally:
                        active -= 1
                with self.subTest(provider=provider), \
                     patch.object(ai_features, 'source_material', AsyncMock(side_effect=AssertionError('Intent must not read source body'))), \
                     patch.object(ai_features, 'source_without_main_text', AsyncMock(return_value=('MAIN_BODY_MUST_NOT_BE_SENT', 'PDF', PDF))), \
                     patch.object(ai_features, 'extract_citation_contexts', return_value=contexts), \
                     patch.object(ai_features, 'generate', generate):
                    response = await self.post('/api/ai/intents', {'papers': selected}, provider=provider)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual([row['id'] for row in result['intents']], [p['id'] for p in selected])
            self.assertTrue(all(row['intent'] == 'Methodology' for row in result['intents'][:-3]))
            self.assertTrue(all(row['intent'] == 'Unknown' for row in result['intents'][-3:]))
            context_ids = [row['id'] for args, _ in calls for row in json_after(args[3], 'Evidence:')]
            self.assertEqual(set(context_ids), set(contexts))
            self.assertEqual(len(context_ids), len(contexts))
            self.assertEqual(maximum, 1)
            self.assertTrue(all(b - a >= 30 for a, b in zip(starts, starts[1:])))
            self.assert_metadata(result, calls)

    async def test_cluster_stream_reports_waiting_and_bounded_calls_without_source_body(self):
        selected = papers(25, long=True)
        calls, starts = [], []
        with virtual_clock() as clock:
            async def generate(*args, **kwargs):
                calls.append((args, kwargs))
                starts.append(clock.now)
                prompt = args[3]
                self.assertNotIn('MAIN_BODY_MUST_NOT_BE_SENT', prompt)
                self.assertNotIn('PRIVATE_ORIGINAL_REFERENCE', prompt)
                if 'Catalogue:' in prompt:
                    self.assertEqual(kwargs['max_output_tokens'], 800)
                    rows = json_after(prompt, 'Catalogue:')
                    self.assertEqual([row['id'] for row in rows], [p['id'] for p in selected])
                    self.assertTrue(all(len(row['title']) <= 160 for row in rows))
                    return {'themes': THEMES}
                self.assertEqual(kwargs['max_output_tokens'], 1200)
                rows = json_after(prompt, 'Papers:')
                self.assertLessEqual(len(rows), 12)
                self.assertTrue(all(len(row['title']) <= 160 and len(row['abstract']) <= 600 for row in rows))
                return {'assignments': [{'id': row['id'], 'theme_id': row['id'] % 3 + 1} for row in rows]}
            with patch.object(ai_features, 'source_material', AsyncMock(side_effect=AssertionError('Clusters must not read source body'))), \
                 patch.object(ai_features, 'source_without_main_text', AsyncMock(return_value=('MAIN_BODY_MUST_NOT_BE_SENT', 'PDF', None))), \
                 patch.object(ai_features, 'enrich_ai_references', enrich), \
                 patch.object(ai_features, 'generate', generate):
                response = await self.post('/api/ai/clusters', {'papers': selected}, stream=True)
        self.assertEqual(response.status_code, 200, response.text)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertTrue(any(event.get('stage') == 'waiting' for event in events))
        self.assertEqual(events[-1]['type'], 'result')
        result = events[-1]['payload']
        assigned = [number for cluster in result['clusters'] for number in cluster['ids']]
        self.assertEqual(set(assigned), {p['id'] for p in selected})
        self.assertEqual(len(assigned), len(selected))
        self.assertTrue(all(b - a >= 30 for a, b in zip(starts, starts[1:])))
        self.assertEqual(len(calls), result['clustering_requests'])
        self.assertNotIn(KEY, response.text)
        self.assert_metadata(result, calls)

    async def test_extraction_keeps_whole_long_entry_every_number_and_serial_pacing(self):
        long_entry = '[1] A. Author, Long reference title, ' + 'unabridged evidence ' * 260 + 'WHOLE_ENTRY_END, 2024.'
        text = long_entry + '\n' + '\n'.join(f'[{i}] A. Author, Reference research title {i}, 2024.'
                                              for i in range(2, 15))
        calls, starts, resolved_refs = [], [], []
        active = maximum = 0
        async def resolve(refs):
            resolved_refs.extend(refs)
            return [{'title': ref.title, 'original_text': ref.original_text} for ref in refs]
        with virtual_clock() as clock:
            async def generate(*args, **kwargs):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                starts.append(clock.now)
                calls.append((args, kwargs))
                chunk = args[3].split('Bibliography excerpt:\n', 1)[1].split('\nEconomy mode:', 1)[0]
                numbers = [int(n) for n in re.findall(r'(?m)^\s*\[(\d+)\]', chunk)]
                self.assertLessEqual(len(numbers), 6)
                if 1 in numbers:
                    self.assertEqual(numbers, [1])
                    self.assertIn(long_entry, chunk)
                else:
                    self.assertLessEqual(len(chunk), 3000)
                self.assertEqual(kwargs['max_output_tokens'], 2500)
                schema = json_after(args[3], 'Schema:')
                self.assertNotIn('"original_text"', json.dumps(schema))
                try:
                    await clock.real_sleep(0)
                finally:
                    active -= 1
                # Omit one printed item to verify local recovery without a paid retry.
                return {'references': [{'number': n, 'title': f'Reference research title {n}',
                        'authors': [], 'year': '2024', 'doi': None}
                        for n in numbers if n != 9]}
            with patch.object(ai_features, 'extract_bibliography_text', return_value=text), \
                 patch.object(ai_features, 'generate', generate), \
                 patch.object(ai_features, 'resolve_references', resolve), \
                 patch.object(ai_features, 'extract_citation_contexts', return_value={}), \
                 patch.object(ai_features, 'extract_citation_counts', return_value=(False, {})), \
                 patch.object(ai_features, 'extract_source_metadata', return_value={}):
                response = await self.post('/api/ai/extract-references',
                    files={'file': ('paper.pdf', PDF, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([row['reference_number'] for row in result['results']], list(range(1, 15)))
        self.assertEqual([ref.number for ref in resolved_refs], list(range(1, 15)))
        self.assertIn('WHOLE_ENTRY_END', resolved_refs[0].original_text)
        self.assertIn(9, result['recovered_reference_numbers'])
        self.assertEqual(len(calls), result['extraction_batches'])
        self.assertEqual(len(calls), 4)  # One long entry, then six, six, one.
        self.assertEqual(maximum, 1)
        self.assertTrue(all(b - a >= 30 for a, b in zip(starts, starts[1:])))
        self.assertTrue(result['extraction_complete'])
        self.assert_metadata(result, calls)

    async def test_qa_retrieves_pdf_passage_missing_from_initial_summary_excerpt(self):
        source = 'Unrelated source content. ' * 1300 + '\n\n' \
            'LATE_ISOTOPE_METHOD: isotope tracing used a calibrated carbon detector.\n\n' \
            + 'Other trailing material. ' * 300
        generation = AsyncMock(return_value='A calibrated carbon detector.')
        extraction = patch.object(ai_features, 'extract_summary_text', return_value=source)
        with patch.object(ai_features, 'source_material', AsyncMock(return_value=('Initial truncated excerpt', 'PDF', PDF))), \
             extraction as full_source, patch.object(ai_features, 'generate', generation):
            response = await self.post('/api/ai/qa', {'question': 'Which detector did isotope tracing use?'})
        self.assertEqual(response.status_code, 200, response.text)
        full_source.assert_called_once_with(PDF, max_chars=90000)
        self.assertIn('LATE_ISOTOPE_METHOD', generation.call_args.args[3])
        self.assertLess(len(generation.call_args.args[3]), 7000)

    async def test_malformed_numbered_extraction_recovers_all_printed_ids_without_retry(self):
        text = '\n'.join(f'[{i}] A. Author, Printed reference research title {i}, 2024.'
                         for i in range(1, 4))
        generation = AsyncMock(side_effect=AIResponseFormatError(502, 'Invalid JSON'))
        async def resolve(refs):
            return [{'title': ref.title, 'original_text': ref.original_text} for ref in refs]
        with virtual_clock() as clock, \
             patch.object(ai_features, 'extract_bibliography_text', return_value=text), \
             patch.object(ai_features, 'generate', generation), \
             patch.object(ai_features, 'resolve_references', resolve), \
             patch.object(ai_features, 'extract_citation_contexts', return_value={}), \
             patch.object(ai_features, 'extract_citation_counts', return_value=(False, {})), \
             patch.object(ai_features, 'extract_source_metadata', return_value={}):
            response = await self.post('/api/ai/extract-references',
                files={'file': ('paper.pdf', PDF, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([row['reference_number'] for row in result['results']], [1, 2, 3])
        self.assertEqual(result['total_references'], 3)
        self.assertTrue(result['extraction_complete'])
        self.assertEqual(result['failed_extraction_batches'], [1])
        self.assertEqual(result['recovered_reference_numbers'], [1, 2, 3])
        self.assertTrue(all(row['extraction_fallback'] for row in result['results']))
        generation.assert_awaited_once()
        self.assertEqual(generation.call_args.kwargs['max_output_tokens'], 2500)
        self.assertNotIn('"original_text"', json.dumps(json_after(generation.call_args.args[3], 'Schema:')))
        self.assertEqual(clock.waits, [])
        self.assert_metadata(result, [(generation.call_args.args, generation.call_args.kwargs)])

    async def test_unnumbered_extraction_retains_original_text_in_full_schema(self):
        original = 'A. Author. Printed reference research title. Journal of Research, 2024.'
        generation = AsyncMock(return_value={'references': [{'number': None, 'title': 'Printed reference research title',
                                    'authors': [], 'year': '2024', 'doi': None, 'original_text': original}]})
        async def resolve(refs):
            self.assertEqual(refs[0].original_text, original)
            return [{'title': ref.title} for ref in refs]
        with patch.object(ai_features, 'extract_bibliography_text', return_value=original), \
             patch.object(ai_features, 'generate', generation), \
             patch.object(ai_features, 'resolve_references', resolve), \
             patch.object(ai_features, 'extract_citation_contexts', return_value={}), \
             patch.object(ai_features, 'extract_citation_counts', return_value=(False, {})), \
             patch.object(ai_features, 'extract_source_metadata', return_value={}):
            response = await self.post('/api/ai/extract-references',
                files={'file': ('paper.pdf', PDF, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total_references'], 1)
        self.assertEqual(generation.call_args.kwargs['max_output_tokens'], 4000)
        self.assertIn('"original_text"', json.dumps(json_after(generation.call_args.args[3], 'Schema:')))
        self.assert_metadata(response.json(), [(generation.call_args.args, generation.call_args.kwargs)])

    async def test_oversized_reference_fails_before_paid_calls_instead_of_being_cut(self):
        text = '[1] A. Author, ' + 'Unabridged reference text ' * 900 + ', 2024.\n' \
            '[2] B. Author, A second reference title, 2024.'
        generation = AsyncMock(side_effect=AssertionError('Reject the oversized entry before any paid request'))
        with patch.object(ai_features, 'extract_bibliography_text', return_value=text), \
             patch.object(ai_features, 'generate', generation):
            response = await self.post('/api/ai/extract-references',
                files={'file': ('paper.pdf', PDF, 'application/pdf')})
        self.assertEqual(response.status_code, 422, response.text)
        generation.assert_not_awaited()
        self.assertNotIn(KEY, response.text)

    async def test_stream_429_stops_before_next_batch_and_never_exposes_credentials(self):
        text = '\n'.join(f'[{i}] A. Author, Reference research title {i}, 2024.' for i in range(1, 20))
        calls = []
        with virtual_clock():
            async def generate(*args, **kwargs):
                calls.append((args, kwargs))
                if len(calls) == 2:
                    raise_provider_error(httpx.Response(429, headers={'Retry-After': '30'},
                        json={'error': {'message': 'private provider content ' + KEY}}), 'openai')
                chunk = args[3].split('Bibliography excerpt:\n', 1)[1].split('\nEconomy mode:', 1)[0]
                numbers = [int(n) for n in re.findall(r'(?m)^\s*\[(\d+)\]', chunk)]
                return {'references': [{'number': n, 'title': f'Reference research title {n}',
                        'authors': [], 'year': '2024', 'doi': None,
                        'original_text': f'A. Author, Reference research title {n}, 2024.'} for n in numbers]}
            with patch.object(ai_features, 'extract_bibliography_text', return_value=text), \
                 patch.object(ai_features, 'generate', generate), \
                 patch.object(ai_features, 'resolve_references', AsyncMock(side_effect=AssertionError('Failed extraction must not publish'))):
                response = await self.post('/api/ai/extract-references', stream=True,
                    files={'file': ('paper.pdf', PDF, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertTrue(any(event.get('stage') == 'waiting' for event in events))
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['status'], 429)
        self.assertIn('30', events[-1]['detail'])
        self.assertFalse(any(event['type'] == 'result' for event in events))
        self.assertNotIn(KEY, response.text)
        self.assertNotIn('private provider content', response.text)


if __name__ == '__main__':
    unittest.main()
