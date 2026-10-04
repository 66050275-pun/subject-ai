"""Author-only references cannot become invented thematic evidence."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import ai_features
from ai_cluster_batches import cluster_in_batches
from main import app


class ClusterEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_titles_or_abstracts_skips_paid_calls(self):
        papers = [{'id': i, 'title': '', 'abstract': ''} for i in range(1, 53)]
        generate = AsyncMock()
        with self.assertRaises(HTTPException) as caught:
            await cluster_in_batches(papers, 'maxplus', 'test', 'fake', None, generate)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('เติมชื่อเรื่องจริง', caught.exception.detail)
        generate.assert_not_awaited()

    async def test_unknown_title_stays_neutral_even_when_ai_assigns_it(self):
        papers = [{'id': i, 'title': 'Battery degradation' if i < 14 else ''} for i in range(1, 15)]
        async def generate(*args, **kwargs):
            if 'Catalogue:' in args[3]: return {'themes': ['Aging', 'Testing', 'Recycling']}
            batch = json.loads(args[3].split('\nPapers:\n')[1])
            return {'assignments': [{'id': p['id'], 'theme_id': 1} for p in batch]}
        result = await cluster_in_batches(papers, 'maxplus', 'test', 'fake', None, generate)
        self.assertEqual(result['unassigned_ids'], [14])
        self.assertEqual(result['clusters'][0]['ids'], list(range(1, 14)))

    async def test_small_graph_uses_same_evidence_rule_and_returns_title_updates(self):
        input_papers = [{'id': i, 'title': 'Q. Qi, Y. Li'} for i in range(1, 4)]
        enriched = [{'id': 1, 'title': 'Battery aging', 'title_status': 'verified', 'title_source': 'Crossref'},
                    {'id': 2, 'title': 'Recycling measurements', 'title_status': 'from_pdf'},
                    {'id': 3, 'title': '', 'title_status': 'unresolved'}]
        generate = AsyncMock(return_value={'clusters': [{'name': 'Aging', 'ids': [1, 3]}, {'name': 'Recycling', 'ids': [2]}]})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_features, 'source_material', AsyncMock(return_value=('Paper', 'PDF', None))), patch.object(ai_features, 'enrich_ai_references', AsyncMock(return_value=enriched)), patch.object(ai_features, 'generate', generate):
                r = await client.post('/api/ai/clusters', headers={'X-AI-API-Key': 'fake'}, data={'provider': 'maxplus', 'model': 'test', 'payload': json.dumps({'papers': input_papers})})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body['clusters'][0]['ids'], [1])
        self.assertEqual(body['unassigned_ids'], [3])
        self.assertEqual(body['resolved_papers'][0]['title'], 'Battery aging')
