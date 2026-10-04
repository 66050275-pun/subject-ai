"""Shared frontend: unassigned nodes, atomic theme updates and input-limit feedback."""
from pathlib import Path
import functools,http.server,threading,json,shutil
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000});page.route('https://**/*',lambda r:r.abort());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  state={'clusters':0,'papers':24,'invalid':False,'partial':False}
  def api(route):
   path=route.request.url.split('/api/')[-1]
   if path=='ai/providers':data={'providers':{'openai':'test'}}
   elif path=='ai/clusters':
    state['clusters']+=1
    data={'clusters':[{'name':'Methods','ids':list(range(1,9))},{'name':'Results','ids':list(range(9,17))},{'name':'Background','ids':list(range(17,23))}], 'unassigned_ids':[23,24], 'clustering_batches':2,'clustering_requests':3}
    if state['partial']:
     data['clusters'][1]['ids']=[];data['unassigned_ids']=list(range(9,17))+[23,24];data['failed_clustering_batches']=[2]
    if state['invalid']:data['clusters'][1]['ids']=[1]
   else:
    papers=[{'reference_number':i,'title':f'Paper {i}','original_text':f'Demo paper {i}','year':'2024','doi':None,'oa_pdf_url':None,'scholar_url':'https://example.org','access_status':'scholar_search','citation_contexts':[]} for i in range(1,state['papers']+1)]
    data={'mode':'doi','filename':'Demo paper','total_references':len(papers),'results':papers,'citing_papers':[]}
   if path=='ai/clusters':
    assert 'name="stream"' in route.request.post_data
    events=[{'type':'progress','stage':'planning','completed':0,'total':3},{'type':'progress','stage':'assigning','completed':3,'total':3},{'type':'result','payload':data}]
    route.fulfill(status=200,content_type='application/x-ndjson',body='\n'.join(json.dumps(e) for e in events)+'\n')
   else:route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
  page.route('**/api/**',api);page.goto(f'http://127.0.0.1:{server.server_port}/static/index.html')
  page.locator('#doi-input').fill('10.1234/demo');page.locator('#doi-button').click();page.locator('#results-list .reference-card').first.wait_for()
  page.locator('.sidebar-settings').click();page.locator('#openai-key').fill('fake-key');page.locator('#settings-close').click();page.locator('#settings-drawer').wait_for(state='hidden')
  page.locator('[data-ai=clusters]').click();page.locator('#ai-output').filter(has_text='AI ยังไม่จัดกลุ่ม 2').wait_for();assert page.locator('#network-legend').inner_text().count('ยังไม่จัดกลุ่ม')==1
  assert 'ประมวลผล 2 ชุด' in page.locator('#ai-output').inner_text();state['partial']=True;page.locator('[data-ai=clusters]').click();page.locator('#ai-output').filter(has_text='อ่านผลไม่ได้ 1 ชุด').wait_for();assert 'จัดกลุ่มได้ 14/24' in page.locator('#ai-output').inner_text();assert 'ยังไม่จัดกลุ่ม · 10' in page.locator('#network-legend').inner_text();before=page.locator('#network-legend').inner_text();state['invalid']=True;page.locator('[data-ai=clusters]').click();page.locator('#ai-output').filter(has_text='ผลกราฟเดิมยังอยู่').wait_for();assert page.locator('#network-legend').inner_text()==before
  state['papers']=101;page.locator('#doi-input').fill('10.1234/large');page.locator('#doi-button').click();page.locator('#total-stat').filter(has_text='101').wait_for();calls=state['clusters'];page.locator('[data-ai=clusters]').click();page.locator('#ai-output').filter(has_text='3–100').wait_for();assert state['clusters']==calls
  assert not errors,errors;browser.close()
 print('PASS: grey unassigned nodes, invalid response retains themes, >100 blocks paid request')
finally:server.shutdown()
