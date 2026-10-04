"""Large citation graphs share themes across serial bounded AI requests."""
import asyncio,json,unittest
from unittest.mock import AsyncMock,patch
import httpx
from fastapi import HTTPException
from ai_cluster_batches import cluster_in_batches,parse_assignments,evidence_batches
import ai_features
from main import app
THEMES=[{'id':i,'name':f'Theme {i}'} for i in (1,2,3)]
PAPERS=[{'id':i*3,'title':f'Battery paper {i}','abstract':'Evidence'} for i in range(1,55)]

class ClusterBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_54_references_serial_calls_and_shared_themes(self):
        active=maximum=calls=0;progress=[]
        async def generate(*args,**kwargs):
            nonlocal active,maximum,calls
            calls+=1;active+=1;maximum=max(maximum,active)
            try:
                await asyncio.sleep(.001);prompt=args[3]
                if 'Catalogue:\n' in prompt:return {'themes':THEMES}
                batch=json.loads(prompt.split('\nPapers:\n')[1]);self.assertLessEqual(len(batch),12)
                themes=json.loads(prompt.split('Themes:\n')[1].split('\nPapers:\n')[0]);self.assertEqual(themes,THEMES)
                return {'assignments':[{'id':p['id'],'theme_id':p['id']//3%3+1} for p in batch]}
            finally:active-=1
        result=await cluster_in_batches(PAPERS,'maxplus','test','fake-key',None,generate,lambda e:append(progress,e))
        ids=[i for c in result['clusters'] for i in c['ids']]
        self.assertEqual(set(ids),{p['id'] for p in PAPERS});self.assertEqual(len(ids),54)
        self.assertEqual((calls,maximum),(6,1));self.assertEqual(result['clustering_batches'],5)
        self.assertEqual(progress[-1]['completed'],progress[-1]['total'])

    async def test_missing_or_null_assignment_is_neutral(self):
        async def generate(*args,**kw):
            if 'Catalogue:' in args[3]:return {'themes':THEMES}
            batch=json.loads(args[3].split('\nPapers:\n')[1])
            return {'assignments':[{'id':p['id'],'theme_id':None} for p in batch[1:]]}
        result=await cluster_in_batches(PAPERS,'maxplus','test','fake-key',None,generate)
        self.assertEqual(set(result['unassigned_ids']),{p['id'] for p in PAPERS});self.assertTrue(all(not c['ids'] for c in result['clusters']))

    async def test_failed_batch_is_not_retried_or_published(self):
        calls=0
        async def generate(*args,**kw):
            nonlocal calls
            calls+=1
            if calls==1:return {'themes':THEMES}
            raise HTTPException(429,'โควตาหมด')
        with self.assertRaises(HTTPException) as caught:await cluster_in_batches(PAPERS,'maxplus','test','fake-key',None,generate)
        self.assertEqual(caught.exception.status_code,429);self.assertIn('ชุด 1/5',caught.exception.detail);self.assertEqual(calls,2)

    def test_rejects_unknown_duplicate_fractional_ids(self):
        for rows in [[{'id':999,'theme_id':1}],[{'id':3,'theme_id':9}],[{'id':3,'theme_id':1},{'id':3,'theme_id':2}],[{'id':True,'theme_id':1}]]:
            with self.assertRaises(ValueError):parse_assignments({'assignments':rows},{3},THEMES)
        long=[dict(p,title='x'*1200,abstract='a'*8000) for p in PAPERS]
        for batch in evidence_batches(long):self.assertLessEqual(len(batch),12);self.assertLessEqual(len(json.dumps(batch,ensure_ascii=False)),7000)

    def test_equivalent_explicit_assignment_shapes(self):
        for value in [
            {'assignments': {'3': '1', '6': None}},
            {'assignments': [{'paper_id': '3', 'cluster_id': '1'}, {'reference_id': 6, 'theme': None}]},
            {'groups': [{'name': 'Theme 1', 'reference_ids': [3]}, {'id': 2, 'name': 'Theme 2', 'ids': []}]},
        ]:
            with self.subTest(value=value):
                result = parse_assignments(value, {3, 6}, THEMES)
                self.assertEqual(result[3], 1)
                self.assertIsNone(result.get(6))
        for value in [
            {'assignments': [{'id': 3, 'paper_id': 3, 'theme_id': 1}]},
            {'groups': [{'id': 1, 'name': 'Theme 2', 'ids': [3]}]},
            {'assignments': {'1': 1}},  # Never remap local positions to IDs 3,6.
        ]:
            with self.assertRaises(ValueError):
                parse_assignments(value, {3, 6}, THEMES)

    async def test_malformed_middle_batch_preserves_successes_without_retry(self):
        from ai_engine import AIResponseFormatError
        for error in (AIResponseFormatError(502, 'Invalid JSON'), ValueError()):
            calls = 0
            async def generate(*args, **kw):
                nonlocal calls
                calls += 1
                if calls == 1: return {'themes': THEMES}
                if calls == 3:
                    if isinstance(error, AIResponseFormatError): raise error
                    return {'assignments': [{'id': 999, 'theme_id': 1}]}
                batch = json.loads(args[3].split('\nPapers:\n')[1])
                return {'assignments': [{'id': p['id'], 'theme_id': 1} for p in batch]}
            result = await cluster_in_batches(PAPERS, 'maxplus', 'test', 'fake', None, generate)
            self.assertEqual(calls, 6)
            self.assertEqual(result['failed_clustering_batches'], [2])
            self.assertEqual(result['unassigned_ids'], [p['id'] for p in PAPERS[12:24]])
            self.assertEqual(len(result['clusters'][0]['ids']), 42)
            self.assertFalse(result['clustering_complete'])

    async def test_malformed_planning_is_specific_and_stops_paid_calls(self):
        from ai_engine import AIResponseFormatError
        generate = AsyncMock(side_effect=AIResponseFormatError(502, 'Invalid JSON'))
        with self.assertRaises(HTTPException) as caught:
            await cluster_in_batches(PAPERS, 'maxplus', 'test', 'fake', None, generate)
        self.assertIn('แผนธีม', caught.exception.detail)
        self.assertEqual(generate.await_count, 1)

    async def test_stream_progress_result_and_error_events(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for fail in (False,True):
                async def generate(*args,**kw):
                    if 'Catalogue:' in args[3]:return {'themes':THEMES}
                    if fail:raise HTTPException(502,'AI ส่งผลลัพธ์ไม่ครบ')
                    batch=json.loads(args[3].split('\nPapers:\n')[1]);return {'assignments':[{'id':p['id'],'theme_id':p['id']//3%3+1} for p in batch]}
                with patch.object(ai_features,'generate',generate),patch.object(ai_features,'source_material',AsyncMock(return_value=('source','PDF',None))),patch.object(ai_features,'enrich_ai_references',AsyncMock(return_value=PAPERS)):
                    response=await client.post('/api/ai/clusters',headers={'X-AI-API-Key':'fake-key'},data={'provider':'maxplus','model':'test','stream':'true','payload':json.dumps({'papers':[{k:v for k,v in p.items() if k!='abstract'} for p in PAPERS]})})
                events=[json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(response.status_code,200);self.assertEqual(events[0]['stage'],'metadata')
                self.assertEqual(events[-1]['type'],'error' if fail else 'result')
                if fail:self.assertFalse(any(e['type']=='result' for e in events))
                else:self.assertEqual(events[-1]['payload']['clustering_batches'],5)
                self.assertNotIn('fake-key',response.text)

async def append(rows,item):rows.append(item)
