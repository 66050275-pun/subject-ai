"""Offline source-contract, matching and end-to-end OA discovery regressions."""
import asyncio
import os
import unittest
from unittest.mock import patch
import httpx

from extractor import Reference
from main import app
from open_access import OADiscovery, matches, location, merge_locations, openalex_locations, openalex_records, safe_url
import resolver

Client = httpx.AsyncClient
DOI = '10.1234/battery'
TITLE = 'Battery degradation and electrode cracking processes'


class OpenAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_doi_conflict_and_title_matching(self):
        self.assertFalse(matches('10.1234/other', TITLE, DOI, TITLE))
        self.assertTrue(matches(None, TITLE.upper() + '.', DOI, TITLE))
        self.assertFalse(matches(None, 'Battery degradation of another material', DOI, TITLE))
        self.assertFalse(matches(None, 'Fail', None, 'Fail'))
        ref = Reference(TITLE, TITLE, '2024', 1, None)
        self.assertFalse(resolver._good_crossref_match({'title': ['Battery degradation and optical testing'], 'score': 100}, ref))

    async def test_academic_keys_are_scoped_to_own_hosts(self):
        requests = []
        def respond(req):
            requests.append(req); return httpx.Response(200, json={})
        with patch.dict(os.environ, {'OPENALEX_API_KEY': 'fake-openalex', 'SEMANTIC_SCHOLAR_API_KEY': 'fake-semantic'}):
            async with Client(transport=httpx.MockTransport(respond)) as client:
                for url in (resolver.CROSSREF_URL, resolver.OPENALEX_URL, resolver.SEMANTIC_SCHOLAR_URL):
                    await resolver._academic_get(client, url)
        self.assertNotIn('api_key', requests[0].url.params)
        self.assertNotIn('x-api-key', requests[0].headers)
        self.assertEqual(requests[1].url.params['api_key'], 'fake-openalex')
        self.assertNotIn('x-api-key', requests[1].headers)
        self.assertEqual(requests[2].headers['x-api-key'], 'fake-semantic')

    def test_all_openalex_copies_deduplicated_and_ranked(self):
        work = {'best_oa_location': None, 'locations': [None,
            {'is_oa': True, 'pdf_url': 'https://repo.example/author.pdf', 'version': 'acceptedVersion', 'source': None},
            {'is_oa': False, 'pdf_url': 'https://publisher.example/paywall.pdf'},
            {'is_oa': True, 'pdf_url': 'https://publisher.example/paper.pdf', 'version': 'publishedVersion'},
            {'is_oa': True, 'pdf_url': 'https://repo.example/author.pdf#page=1'}]}
        result = {}; merge_locations(result, openalex_locations(work))
        self.assertEqual(len(result['pdf_locations']), 2)
        self.assertEqual(result['oa_pdf_url'], 'https://publisher.example/paper.pdf')

    def test_unsafe_links_are_not_candidates(self):
        for url in ('javascript:alert(1)', 'http://127.0.0.1/x', 'http://169.254.169.254/latest',
                    'https://user:secret@example.org/file.pdf', 'http://localhost/x', 'https://example.org:8080/x'):
            self.assertIsNone(safe_url(url), url)

    def test_oa_landing_only_is_a_record_not_a_pdf(self):
        work = {'locations': [None, {'is_oa': True, 'pdf_url': None,
                 'landing_page_url': 'https://university.example/record/1', 'source': None}]}
        self.assertEqual(openalex_locations(work), [])
        self.assertEqual(openalex_records(work)[0]['url'], 'https://university.example/record/1')

    async def test_hal_and_core_exact_doi_contracts(self):
        def respond(req):
            if req.url.host == 'api.archives-ouvertes.fr':
                return httpx.Response(200, json={'response': {'docs': [
                    {'doiId_s': DOI, 'title_s': [TITLE], 'fileMain_s': 'https://hal.science/file.pdf', 'uri_s': 'https://hal.science/hal-1'},
                    {'doiId_s': '10.1234/other', 'title_s': [TITLE], 'fileMain_s': 'https://hal.science/wrong.pdf'}]}})
            self.assertEqual(req.headers['Authorization'], 'Bearer fake-core')
            return httpx.Response(200, json={'results': [
                {'id': 123, 'doi': DOI, 'title': TITLE, 'downloadUrl': 'https://university.example/paper.pdf'}]})
        with patch.dict(os.environ, {'CORE_API_KEY': 'fake-core'}):
            async with Client(transport=httpx.MockTransport(respond)) as client:
                discovery = OADiscovery(client)
                hal, _, _ = await discovery.hal(DOI, TITLE, '')
                core, _, _ = await discovery.core(DOI, TITLE, '')
        self.assertEqual(len(hal), 1); self.assertEqual(len(core), 1)
        self.assertEqual(core[0]['source'], 'CORE')

    async def test_unpaywall_all_copies_and_missing_email(self):
        calls = []
        def respond(req):
            calls.append(req)
            return httpx.Response(200, json={'doi': DOI, 'oa_locations': [None,
                {'url_for_pdf': 'https://repo.example/accepted.pdf', 'version': 'acceptedVersion', 'license': 'cc-by'},
                {'url_for_pdf': 'https://publisher.example/published.pdf', 'version': 'publishedVersion'}]})
        async with Client(transport=httpx.MockTransport(respond)) as client:
            with patch.dict(os.environ, {'PAPERREF_CONTACT_EMAIL': ''}):
                empty = await OADiscovery(client).unpaywall(DOI, TITLE, '')
                self.assertEqual(empty[2], 'requires contact email'); self.assertEqual(calls, [])
            copies, _, error = await OADiscovery(client, 'reader@example.org').unpaywall(DOI, TITLE, '')
        self.assertIsNone(error)
        self.assertEqual(sum(c is not None for c in copies), 2)
        self.assertEqual(calls[0].url.params['email'], 'reader@example.org')

    async def test_pmc_rejects_closed_and_wrong_doi(self):
        data = {'resultList': {'result': [
            {'doi': DOI, 'title': TITLE, 'pmcid': 'PMC1234', 'isOpenAccess': 'Y'},
            {'doi': DOI, 'title': TITLE, 'pmcid': 'PMC5678', 'isOpenAccess': 'N'},
            {'doi': '10.1234/other', 'title': TITLE, 'pmcid': 'PMC9999', 'isOpenAccess': 'Y'}]}}
        async with Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))) as client:
            copies, records, error = await OADiscovery(client).europe_pmc(DOI, TITLE, '')
        self.assertEqual(len(copies), 1); self.assertIn('PMC1234', copies[0]['url'])
        self.assertEqual(len(records), 1)

    async def test_arxiv_preprint_matching(self):
        xml = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:x="http://arxiv.org/schemas/atom">
        <entry><id>http://arxiv.org/abs/2401.12345v2</id><title>{TITLE}</title><x:doi>{DOI}</x:doi></entry>
        <entry><id>http://arxiv.org/abs/2401.54321</id><title>{TITLE}</title><x:doi>10.1234/other</x:doi></entry></feed>'''
        async with Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=xml))) as client:
            copies, _, _ = await OADiscovery(client).arxiv(DOI, TITLE, '', search_title=True)
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]['version'], 'submittedVersion')
        self.assertEqual(copies[0]['url'], 'https://arxiv.org/pdf/2401.12345v2')

    async def test_zenodo_ignores_citing_papers_and_datasets(self):
        def record(relation, kind='publication'):
            return {'metadata': {'doi': '10.5281/zenodo.1', 'title': TITLE, 'access_right': 'open',
                'resource_type': {'type': kind}, 'related_identifiers': [{'identifier': DOI, 'relation': relation}]},
                'files': [{'key': 'paper.pdf', 'links': {'self': 'https://zenodo.org/api/files/example/paper.pdf'}}]}
        data = {'hits': {'hits': [record('cites'), record('isVersionOf', 'dataset'), record('isVersionOf')]}}
        async with Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))) as client:
            copies, _, _ = await OADiscovery(client).zenodo(DOI, TITLE, '')
        self.assertEqual(len(copies), 1); self.assertEqual(copies[0]['matched_by'], 'related_doi')

    async def test_429_stops_source_and_duplicate_request_is_cached(self):
        calls = 0
        async def respond(req):
            nonlocal calls
            calls += 1; await asyncio.sleep(.001)
            return httpx.Response(429)
        async with Client(transport=httpx.MockTransport(respond)) as client:
            source = OADiscovery(client, 'reader@example.org')
            await asyncio.gather(*(source.unpaywall(DOI, TITLE, '') for _ in range(20)))
            await source.unpaywall('10.1234/different', TITLE, '')
        self.assertEqual(calls, 1)

    async def test_crossref_discovered_doi_uses_exact_openalex_lookup(self):
        seen = []
        def respond(req):
            seen.append(req)
            if req.url.host == 'api.crossref.org':
                return httpx.Response(200, json={'message': {'items': [{'title': [TITLE], 'DOI': DOI, 'score': 100}]}})
            if req.url.host == 'api.openalex.org':
                return httpx.Response(200, json={'results': [{'display_name': TITLE, 'doi': DOI,
                    'locations': [{'is_oa': True, 'pdf_url': 'https://repo.example/paper.pdf'}]}]})
            return httpx.Response(200, json={})
        factory = lambda **kw: Client(transport=httpx.MockTransport(respond), **kw)
        with patch.object(resolver.httpx, 'AsyncClient', factory), patch.dict(os.environ, {'PAPERREF_CONTACT_EMAIL': ''}):
            results = await resolver.resolve_references([Reference(TITLE, TITLE, None, 4, None)])
        request = next(req for req in seen if req.url.host == 'api.openalex.org')
        self.assertEqual(request.url.params['filter'], 'doi:https://doi.org/' + DOI)
        self.assertEqual(len(results), 1); self.assertEqual(results[0]['doi'], DOI)
        self.assertEqual(results[0]['oa_pdf_url'], 'https://repo.example/paper.pdf')

    async def test_deep_stream_preserves_ids_and_reports_failures(self):
        def respond(req):
            host = req.url.host
            if host == 'api.crossref.org': return httpx.Response(404)
            if host == 'api.openalex.org': return httpx.Response(200, json={'results': []})
            if host == 'api.semanticscholar.org': return httpx.Response(404)
            if host == 'api.unpaywall.org':
                return httpx.Response(200, json={'doi': DOI, 'oa_locations': [
                    {'url_for_pdf': 'https://repo.example/paper.pdf', 'version': 'acceptedVersion'}]})
            if host == 'export.arxiv.org': return httpx.Response(200, text='<feed/>')
            if host == 'api.archives-ouvertes.fr': return httpx.Response(429)
            return httpx.Response(200, json={})
        factory = lambda **kw: Client(transport=httpx.MockTransport(respond), **kw)
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(resolver.httpx, 'AsyncClient', factory):
                response = await client.post('/api/open-access/search', json={'contact_email': 'reader@example.org',
                    'papers': [{'reference_number': 42, 'title': TITLE, 'doi': DOI}]})
        import json
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        items = [event['result'] for event in events if event['type'] == 'item']
        self.assertEqual(len(items), 1); self.assertEqual(items[0]['reference_number'], 42)
        self.assertEqual(items[0]['access_status'], 'pdf_available')
        self.assertEqual(items[0]['oa_search']['HAL'], 'HTTP 429')

    async def test_duplicate_ids_rejected_before_network(self):
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/open-access/search', json={'papers': [
                {'reference_number': 1}, {'reference_number': 1}]})
        self.assertEqual(response.status_code, 422)
