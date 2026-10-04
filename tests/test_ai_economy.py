"""Economy evidence selection, quota pacing and explicit Gemini model controls."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import ai_engine
from ai_economy import EconomyCaller, OUTPUT_LIMITS, compact_history, compact_papers, compact_source
from extractor import ExtractionError, split_bibliography_batches


class EvidenceTests(unittest.TestCase):
    def test_question_retrieves_middle_passages_without_sending_whole_source(self):
        paragraphs = ['Introduction. General paper context. ' + 'Other evidence. ' * 40]
        paragraphs += ['Unrelated section ' + str(i) + '. ' + 'Details. ' * 85 for i in range(30)]
        paragraphs[16] = 'Logistic regression used L2 regularization. The accuracy was 0.82.'
        source = '\n\n'.join(paragraphs)
        result = compact_source(source, feature='qa', question='What logistic regression regularization was used?')
        self.assertIn(paragraphs[16], result)
        self.assertLessEqual(len(result), 6000)
        self.assertIn('other source passages omitted', result)
        self.assertLess(len(result), len(source) // 2)

    def test_short_source_is_unchanged_and_long_context_is_bounded(self):
        self.assertEqual(compact_source('Small supplied abstract.'), 'Small supplied abstract.')
        for budget in (1000, 2000, 6000):
            source = '\n\n'.join('Results. ' + str(i) + ' ' + 'Evidence ' * 100 for i in range(40))
            result = compact_source(source, max_chars=budget)
            self.assertLessEqual(len(result), budget)
            self.assertIn('other source passages omitted', result)

    def test_every_reference_id_survives_evidence_compaction(self):
        papers = [{'id': i * 7, 'title': 'Title ' * 100, 'abstract': 'Abstract ' * 900,
                   'original_text': 'Original long citation', 'api_key': 'do-not-copy'} for i in range(1, 101)]
        rows = compact_papers(papers, feature='qa')
        self.assertEqual([row['id'] for row in rows], [paper['id'] for paper in papers])
        self.assertLessEqual(sum(len(row.get('abstract', '')) for row in rows), 3000)
        self.assertTrue(all(len(row['title']) <= 160 for row in rows))
        self.assertNotIn('do-not-copy', json.dumps(rows))
        self.assertNotIn('original_text', json.dumps(rows))

    def test_history_is_bounded_without_altering_messages(self):
        messages = [{'role': 'user', 'content': str(i) + 'x' * 2000} for i in range(10)]
        result = compact_history(messages)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(len(message['content']) <= 600 for message in result))
        self.assertTrue(result[0]['content'].startswith('6'))
        self.assertEqual(len(messages[0]['content']), 2001)

    def test_oversized_numbered_entry_stays_whole_in_economy_batches(self):
        long_entry = '[1] A. Author, ' + 'Long citation content. ' * 230
        source = long_entry + '\n[2] B. Author, Short citation, 2024.'
        chunks = split_bibliography_batches(source, 3000, 6, preserve_entries=True)
        self.assertTrue(any(long_entry in chunk for chunk in chunks))
        self.assertTrue(any('[2]' in chunk for chunk in chunks))
        self.assertTrue(any(len(chunk) > 3000 for chunk in chunks))
        with self.assertRaises(ExtractionError):
            split_bibliography_batches('[1] ' + 'x' * 21000 + '\n[2] Second citation.', 3000, 6, preserve_entries=True)


class CallerTests(unittest.IsolatedAsyncioTestCase):
    async def test_serial_calls_are_spaced_and_metadata_contains_no_credentials(self):
        now, starts, events = [0.0], [], []
        async def sleep(seconds):
            now[0] += seconds
            await asyncio.sleep(0)
        async def generate(*args, **kwargs):
            starts.append(now[0])
            self.assertTrue(kwargs['economy_mode'])
            self.assertEqual(kwargs['max_output_tokens'], OUTPUT_LIMITS['clusters'])
            await asyncio.sleep(0)
            return {'themes': ['Actual theme']}
        async def report(event):
            events.append(event)
        caller = EconomyCaller(generate, 'clusters', 30, report, clock=lambda: now[0], sleeper=sleep)
        await asyncio.gather(*(caller('gemini', 'test', 'private-test-key', 'Evidence') for _ in range(3)))
        self.assertEqual(starts, [0, 30, 60])
        metadata = caller.metadata()
        self.assertEqual(metadata['requests'], 3)
        self.assertEqual(len(metadata['output_token_limits']), 3)
        self.assertTrue(any(event['stage'] == 'waiting' for event in events))
        self.assertNotIn('private-test-key', json.dumps(metadata))

    async def test_elapsed_generation_time_counts_toward_spacing(self):
        now = [0]
        sleeper = AsyncMock()
        async def generate(*args, **kwargs):
            now[0] += 40
            return 'Answer'
        caller = EconomyCaller(generate, 'summary', 30, clock=lambda: now[0], sleeper=sleeper)
        await caller('openai', 'test', 'fake', 'Evidence')
        await caller('openai', 'test', 'fake', 'Evidence')
        sleeper.assert_not_awaited()

    async def test_provider_errors_are_not_retried(self):
        generate = AsyncMock(side_effect=HTTPException(429, 'Quota limit'))
        caller = EconomyCaller(generate, 'qa')
        with self.assertRaises(HTTPException):
            await caller('gemini', 'test', 'fake', 'Evidence')
        self.assertEqual(generate.await_count, 1)

    async def test_wait_can_be_cancelled_before_second_provider_call(self):
        generate = AsyncMock(return_value='Answer')
        caller = EconomyCaller(generate, 'summary')
        await caller('gemini', 'test', 'fake', 'Evidence')
        task = asyncio.create_task(caller('gemini', 'test', 'fake', 'Evidence'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(generate.await_count, 1)


class GeminiBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_verified_flash_aliases_disable_thinking_when_opted_in(self):
        client = httpx.AsyncClient
        for model, economy, expected in [
            ('gemini-2.5-flash', True, True), ('models/gemini-2.5-flash-lite', True, True),
            ('gemini-2.5-pro', True, False), ('gemini-3.8-flash', True, False),
            ('gemini-2.5-flash', False, False),
        ]:
            bodies = []
            def respond(request):
                bodies.append(json.loads(request.content))
                return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'Grounded answer'}]}}]})
            with self.subTest(model=model, economy=economy), patch.object(ai_engine.httpx, 'AsyncClient',
                    lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)):
                await ai_engine.generate('gemini', model, 'fake', 'Evidence', economy_mode=economy)
            config = bodies[0]['generationConfig']
            self.assertEqual('thinkingConfig' in config, expected)
            if expected:
                self.assertEqual(config['thinkingConfig'], {'thinkingBudget': 0})


if __name__ == '__main__':
    unittest.main()
