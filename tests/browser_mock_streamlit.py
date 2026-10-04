import sys, asyncio, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
port=sys.argv[1] if len(sys.argv)>1 else '8510'
import streamlit_transport
async def fake_app(scope,receive,send):
 req=await receive()
 path=scope['path']
 def paper(n):return {'reference_number':n,'title':'Battery degradation research topic '+str(n),'matched_title':None,'original_text':'A. Author, Battery degradation research topic '+str(n),'doi':None,'year':'2024','oa_pdf_url':None,'metadata_sources':[],'source_links':[],'access_status':'scholar_search','access_label':'Search Scholar','citation_mentions':1,'citation_contexts':[]}
 if path in ('/api/ai/extract-references','/api/open-access/search'):
  await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/x-ndjson')]})
  await send({'type':'http.response.body','body':b'{"type":"progress","stage":"extracting","completed":1,"total":2}\n','more_body':True})
  await asyncio.sleep(.8)
  if path=='/api/ai/extract-references':
   event={'type':'result','payload':{'filename':'AI paper','results':[paper(1),paper(2),paper(3)],'total_references':3}}
  else:
   p=paper(1);p.update(oa_pdf_url='https://repo.example/1.pdf',pdf_locations=[{'url':'https://repo.example/1.pdf','source':'Unpaywall','version':'acceptedVersion'}]);event={'type':'item','result':p,'completed':1,'total':2}
  await send({'type':'http.response.body','body':(json.dumps(event)+'\n').encode(),'more_body':True})
  await send({'type':'http.response.body','body':b'{"type":"done","total":2}\n'})
  return
 if path=='/api/references': payload={'filename':'Shared mobile paper','results':[paper(1),paper(2)],'total_references':2,'parsed_title_count':2,'expected_reference_count':2,'extraction_complete':True,'citation_links_available':True}
 elif path=='/api/doi':payload={'mode':'doi','filename':'DOI paper','results':[paper(1),paper(2)],'total_references':2,'citing_papers':[]}
 elif path=='/api/summarize':payload={'summary':'Mock shared summary','source':'PDF','provider':'openai','model':'test'}
 elif path=='/api/ai/intents':payload={'intents':[{'id':i,'intent':'Background','reason':'Mock context'} for i in (1,2,3)],'contexts':{}}
 elif path=='/api/ai/synthesis':payload={'synthesis':'Mock shared research gap'}
 elif path=='/api/ai/clusters':payload={'clusters':[{'name':'Topic '+str(i),'ids':[i]} for i in (1,2,3)]}
 elif path=='/api/ai/compare':payload={'comparison':'Mock cross-paper comparison'}
 elif path=='/api/ai/qa':payload={'answer':'Mock shared answer'}
 elif path=='/api/ai/alibaba-models':payload={'models':['qwen-plus'],'verified':False,'source':'suggested','warning':'รายชื่อแนะนำ ไม่ใช่ผลตรวจคีย์'}
 elif path.endswith('-models'):payload={'models':['test-model'],'base_url':'https://api.maxplus-ai.cc/v1'}
 else:payload={'detail':'unknown route'}
 await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
 await send({'type':'http.response.body','body':json.dumps(payload).encode()})
streamlit_transport.app=fake_app
from streamlit.web import cli
sys.argv=['streamlit','run',str(ROOT/'streamlit_app.py'),'--server.port='+port,'--server.address=127.0.0.1','--server.headless=true']
cli.main()
