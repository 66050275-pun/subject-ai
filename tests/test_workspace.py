import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from main import app
from ai_features import compare_workspace
from starlette.formparsers import MultiPartParser


class WorkspaceTests(unittest.TestCase):
    def test_comparison_uses_only_selected_evidence(self):
        async def run():
            with patch('ai_features.generate', new_callable=AsyncMock, return_value='Comparison') as generate:
                result=await compare_workspace(provider='openai',model='gpt-4o-mini',key='test-key',base_url=None,payload=json.dumps({'papers':[{'title':'One','summary':'Method A'},{'title':'Two','abstract':'Result B'}]}))
                self.assertEqual(result['comparison'],'Comparison')
                prompt=generate.call_args.args[3]
                self.assertIn('Method A',prompt);self.assertIn('Result B',prompt)
                self.assertNotIn('test-key',prompt)
        asyncio.run(run())

    def test_comparison_validates_count_and_secret_fields(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                for papers in [[{'title':'One'}],[{'title':'One','api_key':'secret'},{'title':'Two'}]]:
                    result=await client.post('/api/ai/compare',data={'payload':json.dumps({'papers':papers})},headers={'X-AI-API-Key':'test-key'})
                    self.assertEqual(result.status_code,422)
                    self.assertEqual(result.headers['cache-control'],'no-store')
        asyncio.run(run())

    def test_large_pdf_stays_in_memory(self):
        # Parser normally rolls uploads to disk at 1 MB. Inspect inside read_pdf.
        import ai_features
        original=ai_features.read_pdf
        observed=[]
        async def inspect(file):
            observed.append(file.file._rolled)
            return await original(file)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                with patch('ai_features.read_pdf',inspect):
                    result=await client.post('/api/summarize',data={'provider':'openai','model':'gpt-4o-mini'},headers={'X-AI-API-Key':'test-key'},files={'file':('large.pdf',b'%PDF-'+b'x'*(2*1024*1024),'application/pdf')})
                    self.assertEqual(observed,[False])
                    self.assertGreater(MultiPartParser.spool_max_size,64*1024*1024)
        asyncio.run(run())
