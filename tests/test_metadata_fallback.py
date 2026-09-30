"""Offline regressions for unavailable academic metadata in AI requests."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import ai_features
import resolver
from main import app

Client = httpx.AsyncClient


class MetadataFallbackTests(unittest.IsolatedAsyncioTestCase):
    def client_factory(self, crossref, works):
        def respond(request):
            if request.url.host == 'api.crossref.org':
                if crossref is None:
                    return httpx.Response(404)
                return httpx.Response(200, json={'message': crossref})
            return httpx.Response(200, json={'results': works})

        return lambda **kwargs: Client(transport=httpx.MockTransport(respond), **kwargs)

    async def test_missing_crossref_uses_openalex(self):
        work = {'display_name': 'Battery degradation study', 'publication_year': 2024,
                'abstract_inverted_index': {'Battery': [0], 'study': [1]},
                'authorships': [None, {'author': None},
                               {'author': {'display_name': 'A. Researcher'}}]}
        with patch.object(resolver.httpx, 'AsyncClient', self.client_factory(None, [work])):
            result = await resolver.fetch_doi_summary_material('10.1234/example')
        self.assertEqual(result['year'], '2024')
        self.assertEqual(result['abstract'], 'Battery study')
        self.assertEqual(result['authors'], ['A. Researcher'])

    async def test_both_records_missing_preserves_reference(self):
        for works in ([], [None]):
            with self.subTest(works=works):
                with patch.object(resolver.httpx, 'AsyncClient', self.client_factory(None, works)):
                    result = await resolver.enrich_ai_references([
                        {'id': 1, 'title': 'Original paper title', 'doi': '10.1234/example'}])
                self.assertEqual(result[0]['title'], 'Original paper title')
                self.assertEqual(result[0]['evidence_level'], 'title/metadata only')

    async def test_null_crossref_authors_and_dates(self):
        for authors in (None, [None, {'given': 'A', 'family': 'Researcher'}]):
            with self.subTest(authors=authors):
                record = {'title': ['A paper title'], 'author': authors,
                          'issued': None, 'created': {'date-parts': [[2023]]}}
                with patch.object(resolver.httpx, 'AsyncClient', self.client_factory(record, [])):
                    result = await resolver.fetch_doi_summary_material('10.1234/example')
                self.assertEqual(result['year'], '2023')

    async def test_clusters_endpoint_survives_missing_metadata(self):
        papers = [{'id': i, 'title': f'Original paper {i}', 'doi': f'10.1234/example{i}'}
                  for i in range(1, 4)]
        generate = AsyncMock(return_value={'clusters': [
            {'name': f'Topic {i}', 'ids': [i]} for i in range(1, 4)]})
        async with Client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(resolver.httpx, 'AsyncClient', self.client_factory(None, [])), \
                 patch.object(ai_features, 'source_material', AsyncMock(return_value=('Source text', 'PDF', b''))), \
                 patch.object(ai_features, 'generate', generate):
                response = await client.post('/api/ai/clusters',
                    headers={'X-AI-API-Key': 'fake-test-key'},
                    data={'provider': 'openai', 'payload': json.dumps({'papers': papers})})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['clusters']), 3)
        prompt = generate.call_args.args[3]
        self.assertIn('title/metadata only', prompt)
        self.assertIn('Original paper 1', prompt)


if __name__ == '__main__':
    unittest.main()
