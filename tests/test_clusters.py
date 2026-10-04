"""Cluster interoperability, full ID coverage and stateless provider regressions."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi import HTTPException
import ai_engine
import ai_features
from ai_clusters import normalize_clusters
from main import app

IDS = [2, 7, 11, 20]
GROUPS = [{'name': 'Methods', 'ids': [2, 7]}, {'name': 'Results', 'ids': [11]}, {'name': 'Background', 'ids': [20]}]

class ClusterTests(unittest.IsolatedAsyncioTestCase):
    def test_equivalent_shapes_and_numeric_strings(self):
        for value in [GROUPS, {'clusters': GROUPS}, {'topics': [
            {'topic': 'Methods', 'paper_ids': ['2', '7', '7'], 'description': 'Evidence'},
            {'topic': 'Results', 'paper_ids': ['11']},
            {'topic': 'Background', 'paper_ids': ['20']}]}]:
            self.assertEqual(normalize_clusters(value, IDS), {'clusters': GROUPS})

    def test_rejects_missing_conflicting_unknown_and_noninteger_ids(self):
        for ids, detail in [([2, 7, 11], 'หลายกลุ่ม'), ([2, 7, 99], 'ไม่อยู่'), ([2, 7, True], 'ไม่อยู่'), ([2, 7, 2.0], 'ไม่อยู่')]:
            with self.subTest(ids=ids), self.assertRaises(HTTPException) as caught:
                normalize_clusters({'clusters': [{'name': 'Methods', 'ids': ids}, *GROUPS[1:]]}, IDS)
            self.assertIn(detail, caught.exception.detail)

    def test_missing_ids_are_explicitly_unassigned_not_invented(self):
        result=normalize_clusters({'clusters': [dict(GROUPS[0],ids=[2]),*GROUPS[1:]]},IDS)
        self.assertEqual(result['unassigned_ids'],[7])
        self.assertNotIn(7,[i for c in result['clusters'] for i in c['ids']])
        self.assertIn('1 รายการ',result['warnings'][0])

    def test_rejects_ambiguous_or_invalid_groups(self):
        for value in [None, {'clusters': GROUPS, 'topics': GROUPS}, [], {'clusters': [*GROUPS, {'name': '', 'ids': [20]}]}, {'clusters': [dict(GROUPS[0], paper_ids=[2]), *GROUPS[1:]]}]:
            with self.subTest(value=value), self.assertRaises(HTTPException):
                normalize_clusters(value, IDS)

    async def test_empty_themes_do_not_fail_schema(self):
        papers = [{'id': i, 'title': f'Paper {i}'} for i in IDS]
        groups = [{'name': 'Methods', 'ids': IDS}, {'name': 'Results', 'ids': []}, {'name': 'Background', 'ids': []}]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(ai_features, 'source_material', AsyncMock(return_value=('Source', 'PDF', None))), patch.object(ai_features, 'enrich_ai_references', AsyncMock(return_value=papers)), patch.object(ai_features, 'generate', AsyncMock(return_value={'clusters': groups})):
                response = await client.post('/api/ai/clusters', data={'provider': 'maxplus', 'model': 'test', 'payload': json.dumps({'papers': papers})}, headers={'X-AI-API-Key': 'fake'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['clusters'], groups)

    def test_json_envelopes_and_truncation(self):
        data=json.dumps({'clusters': GROUPS})
        for text in [data, '\ufeff'+data, 'ผลการจัดกลุ่ม:\n'+data, 'ผลลัพธ์:\n```JSON\n'+data+'\n```\nจัดจาก metadata']:
            self.assertEqual(ai_engine.decode_structured_response(text), {'clusters': GROUPS})
        for text in [data[:-3], data+' {}', '```json\n{}\n```\n```json\n{}\n```', 'No JSON result']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                ai_engine.decode_structured_response(text)

    async def test_all_provider_adapters_accept_fenced_cluster_array(self):
        original=httpx.AsyncClient
        text='คำตอบ:\n```json\n'+json.dumps([{'theme': g['name'], 'reference_ids': [str(i) for i in g['ids']]} for g in GROUPS])+'\n```'
        for provider in ('openai','gemini','claude','maxplus'):
            calls=[]
            def respond(request):
                calls.append(request)
                body=({'candidates':[{'content':{'parts':[{'text':text}]}}]} if provider=='gemini' else
                      {'content':[{'type':'text','text':text}]} if provider=='claude' else
                      {'choices':[{'message':{'content':text}}]})
                return httpx.Response(200,json=body)
            with self.subTest(provider=provider), patch.object(ai_engine.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                result=await ai_engine.generate(provider,'test-model','fake-test-key','Group references',structured=True)
                self.assertEqual(normalize_clusters(result,IDS),{'clusters':GROUPS})
                self.assertEqual(len(calls),1)  # No paid retries.

    async def test_endpoint_normalizes_or_rejects_without_retry(self):
        papers=[{'id':i,'title':f'Paper {i}'} for i in IDS]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for value,status in [(GROUPS,200),({'clusters':GROUPS[:-1]},502),({'clusters':[dict(GROUPS[0],ids=[2]),*GROUPS[1:]]},200)]:
                generate=AsyncMock(return_value=value)
                with patch.object(ai_features,'source_material',AsyncMock(return_value=('Source','PDF',None))),patch.object(ai_features,'enrich_ai_references',AsyncMock(return_value=papers)),patch.object(ai_features,'generate',generate):
                    response=await client.post('/api/ai/clusters',data={'provider':'maxplus','model':'test','payload':json.dumps({'papers':papers})},headers={'X-AI-API-Key':'fake-test-key'})
                self.assertEqual(response.status_code,status,response.text)
                self.assertEqual(generate.await_count,1)
                if status==200:
                    body=response.json();covered=[i for c in body['clusters'] for i in c['ids']]+body['unassigned_ids']
                    self.assertEqual(set(covered),set(IDS))
                    self.assertEqual(len(covered),len(IDS))
                else:self.assertNotIn('fake-test-key',response.text)
