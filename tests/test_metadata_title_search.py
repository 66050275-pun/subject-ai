"""Offline title recovery, conservative identity checks, and streaming API."""
import asyncio
import copy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from extractor import Reference
from main import app
import resolver

Client = httpx.AsyncClient
CITATION = 'Q. Qi, Y. Li, J. Energy Chem. 92 (2024) 605–618.'
TITLE = 'Data-driven health estimation for lithium ion batteries'
DOI = '10.1234/health-study'


def crossref(**updates):
    record = {'title': [TITLE], 'DOI': DOI, 'score': 60,
              'author': [{'given': 'Qiang', 'family': 'Qi'}, {'given': 'Yun', 'family': 'Li'}],
              'issued': {'date-parts': [[2024]]}, 'volume': '92', 'page': '605-618',
              'container-title': ['Journal of Energy Chemistry']}
    record.update(updates)
    return record


def reference(title=''):
    return Reference(CITATION, title, '2024', 1)


def responder(items=(), works=(), papers=(), crossref_status=200, scholar=(), requests=None):
    """Fixed-host API contracts; every other request is an offline 404."""
    def respond(request):
        if requests is not None:
            requests.append(request)
        if request.url.host == 'api.crossref.org':
            if crossref_status != 200:
                return httpx.Response(crossref_status)
            if request.url.path == '/works':
                return httpx.Response(200, json={'message': {'items': list(items)}})
            return httpx.Response(200, json={'message': crossref()})
        if request.url.host == 'api.openalex.org':
            return httpx.Response(200, json={'results': list(works) if 'search' in request.url.params else []})
        if request.url.host == 'api.semanticscholar.org':
            if request.url.path.endswith('/search'):
                return httpx.Response(200, json={'data': list(papers)})
            return httpx.Response(404)
        if request.url.host == 'serpapi.com':
            return httpx.Response(200, json={'organic_results': list(scholar)})
        return httpx.Response(404)
    return respond


class BibliographicIdentityTests(unittest.TestCase):
    def test_author_title_is_cleaned_without_mutating_reference(self):
        original = reference('Q. Qi, Y. Li')
        cleaned = resolver._clean_reference(original)
        self.assertEqual(cleaned.title, '')
        self.assertEqual(cleaned.authors, ('Q. Qi', 'Y. Li'))
        self.assertEqual(original.title, 'Q. Qi, Y. Li')
        self.assertIsNot(original, cleaned)

    def test_author_year_volume_page_identify_an_omitted_title(self):
        self.assertTrue(resolver._good_crossref_match(crossref(), reference()))
        self.assertTrue(resolver._coordinates_match(reference(), {'volume': '92', 'page': '605-618'}))

    def test_wrong_identity_is_rejected_despite_high_score(self):
        for change in ({'issued': {'date-parts': [[2023]]}},
                       {'author': [{'given': 'Qiang', 'family': 'Wang'}]},
                       {'volume': '93'}, {'page': '700-712'}, {'container-title': ['Journal of Neurosurgery']}):
            with self.subTest(change=change):
                self.assertFalse(resolver._good_crossref_match(crossref(score=100, **change), reference()))

    def test_last_page_is_not_a_matching_first_page(self):
        self.assertFalse(resolver._good_crossref_match(crossref(page='618-630'), reference()))

    def test_candidate_author_list_is_not_recovered_as_a_paper_title(self):
        self.assertFalse(resolver._good_crossref_match(crossref(title=['Q. Qi, Y. Li']), reference()))

    def test_article_identifier_matches_without_page_range(self):
        ref = Reference('C. Xu, Nat. Commun. 14 (2023) 119.', '', '2023')
        candidate = crossref(author=[{'family': 'Xu'}], issued={'date-parts': [[2023]]},
                             volume='14', page=None, **{'article-number': '119', 'container-title': ['Nature Communications']})
        self.assertTrue(resolver._good_crossref_match(candidate, ref))


class MetadataTitleSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'SERPAPI_API_KEY': '', 'OPENALEX_API_KEY': '',
                                                  'SEMANTIC_SCHOLAR_API_KEY': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def resolve(self, respond, ref=None):
        async with Client(transport=httpx.MockTransport(respond)) as client:
            return await resolver._resolve_one(client, asyncio.Semaphore(1), ref or reference())

    async def test_crossref_returns_real_title_and_canonical_doi(self):
        result = await self.resolve(responder(items=[crossref()]), reference('Q. Qi, Y. Li'))
        self.assertEqual(result['matched_title'], TITLE)
        self.assertEqual(result['doi'], DOI)
        self.assertEqual(result['title_status'], 'verified')
        self.assertEqual(result['title_source'], 'Crossref')
        self.assertEqual(result['authors'], ['Qiang Qi', 'Yun Li'])
        self.assertEqual(result['original_text'], CITATION)

    async def test_ambiguous_crossref_records_remain_unresolved(self):
        result = await self.resolve(responder(items=[crossref(), crossref(DOI='10.1234/other-work')]))
        self.assertIsNone(result['matched_title'])
        self.assertIsNone(result['doi'])
        self.assertEqual(result['title_status'], 'unresolved')

    async def test_crossref_rate_limit_uses_openalex_bibliography(self):
        work = {'id': 'https://openalex.org/W1', 'display_name': TITLE,
                'publication_year': 2024, 'doi': f'https://doi.org/{DOI}',
                'authorships': [{'author': {'display_name': 'Qiang Qi'}}],
                'biblio': {'volume': '92', 'first_page': '605', 'last_page': '618'}}
        result = await self.resolve(responder(crossref_status=429, works=[work]))
        self.assertEqual(result['matched_title'], TITLE)
        self.assertEqual(result['title_source'], 'OpenAlex')
        self.assertEqual(result['oa_search']['Crossref'], 'HTTP 429')

    async def test_semantic_scholar_uses_author_and_journal_coordinates(self):
        paper = {'paperId': 'paper1', 'title': TITLE, 'year': 2024,
                 'authors': [{'name': 'Qiang Qi'}], 'journal': {'volume': '92', 'pages': '605-618'},
                 'externalIds': {'DOI': DOI}, 'url': 'https://www.semanticscholar.org/paper/paper1'}
        result = await self.resolve(responder(papers=[paper]))
        self.assertEqual(result['matched_title'], TITLE)
        self.assertEqual(result['title_source'], 'Semantic Scholar')

    async def test_short_verified_title_is_used_for_scholar_query(self):
        result = await self.resolve(responder(items=[crossref(title=['Battery aging'])]))
        self.assertEqual(result['matched_title'], 'Battery aging')
        self.assertEqual(parse_qs(urlsplit(result['scholar_url']).query)['q'], ['Battery aging'])

    async def test_optional_scholar_key_stays_on_serpapi_host_and_out_of_result(self):
        calls = []
        with patch.dict(os.environ, {'SERPAPI_API_KEY': 'fake-test-serpapi',
                                     'OPENALEX_API_KEY': 'fake-test-openalex',
                                     'SEMANTIC_SCHOLAR_API_KEY': 'fake-test-semantic'}):
            result = await self.resolve(responder(
                scholar=[{'title': TITLE, 'link': f'https://doi.org/{DOI}'}], requests=calls))
        self.assertEqual(result['matched_title'], TITLE)
        self.assertEqual(result['title_source'], 'Google Scholar (SerpAPI)')
        self.assertNotIn('fake-test-', json.dumps(result))
        hosts = {request.url.host for request in calls}
        self.assertNotIn('scholar.google.com', hosts)
        for request in calls:
            text = str(request.url) + str(dict(request.headers))
            if request.url.host == 'serpapi.com':
                self.assertEqual(request.url.params['engine'], 'google_scholar')
                self.assertEqual(request.url.params['api_key'], 'fake-test-serpapi')
                self.assertNotIn('fake-test-openalex', text)
                self.assertNotIn('fake-test-semantic', text)
            else:
                self.assertNotIn('fake-test-serpapi', text)

    async def test_unconfigured_scholar_is_not_called(self):
        calls = []
        await self.resolve(responder(requests=calls))
        self.assertNotIn('serpapi.com', {request.url.host for request in calls})

    async def test_429_blocks_subsequent_same_host_requests_for_this_operation(self):
        calls = []
        async with Client(transport=httpx.MockTransport(responder(crossref_status=429, requests=calls))) as client:
            for suffix in ('', ' Citation continuation.'):
                ref = Reference(CITATION + suffix, '', '2024')
                result = await resolver._resolve_one(client, asyncio.Semaphore(1), ref)
                self.assertEqual(result['oa_search']['Crossref'], 'HTTP 429')
        self.assertEqual(sum(r.url.host == 'api.crossref.org' for r in calls), 1)

    async def test_ai_enrichment_recovers_titles_without_changing_saved_input(self):
        papers = [{'id': 1, 'title': 'Q. Qi, Y. Li', 'original_text': CITATION, 'year': '2024'}]
        original = copy.deepcopy(papers)
        factory = lambda **kwargs: Client(transport=httpx.MockTransport(responder(items=[crossref()])), **kwargs)
        with patch.object(resolver.httpx, 'AsyncClient', factory):
            result = await resolver.enrich_ai_references(papers)
        self.assertEqual(papers, original)
        self.assertEqual(result[0]['title'], TITLE)
        self.assertEqual(result[0]['evidence_level'], 'title/metadata only')

    async def test_metadata_stream_endpoint_needs_no_ai_key(self):
        papers = [{'reference_number': 3, 'title': 'Q. Qi, Y. Li',
                   'original_text': CITATION, 'year': '2024'}]
        original = copy.deepcopy(papers)
        factory = lambda **kwargs: Client(transport=httpx.MockTransport(responder(items=[crossref()])), **kwargs)
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(resolver.httpx, 'AsyncClient', factory), \
                 patch('ai_features.generate', AsyncMock(side_effect=AssertionError('Metadata must not call AI'))) as ai:
                response = await client.post('/api/metadata/search', json={'papers': papers})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('application/x-ndjson', response.headers['content-type'])
        self.assertIn('no-store', response.headers['cache-control'])
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[0]['type'], 'progress')
        items = [event['result'] for event in events if event['type'] == 'item']
        self.assertEqual(items[0]['reference_number'], 3)
        self.assertEqual(items[0]['matched_title'], TITLE)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['recovered'], 1)
        self.assertEqual(papers, original)
        ai.assert_not_called()

    async def test_metadata_endpoint_rejects_duplicate_reference_numbers(self):
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/metadata/search', json={'papers': [
                {'reference_number': 1, 'original_text': CITATION},
                {'reference_number': 1, 'original_text': CITATION}]})
        self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()
