"""Run explicitly with Playwright installed; only fake provider responses are used."""
import subprocess,time,urllib.request,json,sys,os,socket,shutil,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
root=str(Path(__file__).resolve().parents[1])
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
endpoint='http://127.0.0.1:'+str(port)
test_python=os.environ.get('PAPERREF_TEST_PYTHON',sys.executable)
log=tempfile.TemporaryFile(mode='w+')
server=subprocess.Popen([test_python,root+'/tests/browser_mock_streamlit.py',str(port)],cwd=root,stdout=log,stderr=subprocess.STDOUT)
try:
 for _ in range(100):
  try:urllib.request.urlopen(endpoint+'/_stcore/health',timeout=1);break
  except Exception:time.sleep(.1)
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('PAPERREF_TEST_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
  ctx=browser.new_context(viewport={'width':1440,'height':900});page=ctx.new_page();page.route('https://**/*',lambda r:r.abort());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(endpoint+'/');f=page.frame_locator('iframe').first;f.locator('.hero h1').wait_for(timeout=30000)
  def history():
   f.locator('.sidebar-settings').click();f.locator('[data-settings-section=history]').click()
  def close():
   f.locator('#settings-close').click();f.locator('#settings-drawer').wait_for(state='hidden')
  f.locator('#file-input').set_input_files({'name':'paper.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4 test'});f.locator('#analyze-button').click();f.locator('#results-list .reference-card').first.wait_for(timeout=30000)
  history();f.locator('.history-card').wait_for();assert f.locator('.history-card').count()==1
  close();f.locator('.sidebar-settings').click();f.locator('#openai-key').fill('fake-private-secret');close();f.locator('[data-ai=extract-references]').click();f.locator('#dataset-ai[aria-pressed=true]').wait_for(timeout=30000)
  history();page.wait_for_timeout(700);assert f.locator('.history-card').count()==2;close()
  f.locator('#oa-search-button').click();f.locator('#oa-status').filter(has_text='ค้นครบ').wait_for(timeout=30000);page.wait_for_timeout(500)
  f.locator('#doi-input').fill('10.1000/test');f.locator('#doi-button').click();f.locator('#dataset-doi[aria-pressed=true]').wait_for(timeout=30000)
  history();page.wait_for_timeout(500);assert f.locator('.history-card').count()==3
  with page.expect_download() as download:f.locator('#history-export').click()
  saved=Path(download.value.path()).read_text();data=json.loads(saved);assert len(data['entries'])==3;assert 'fake-private-secret' not in saved;assert 'api_key' not in saved;assert 'chat' not in saved;assert '%PDF' not in saved
  assert any(row['payload']['results'][0]['oa_pdf_url'] for row in data['entries'])
  page.reload();f=page.frame_locator('iframe').first;f.locator('.hero h1').wait_for(timeout=30000);history();assert f.locator('.history-card').count()==3;f.locator('.history-card').filter(has_text='สกัดด้วยโค้ด').get_by_role('button',name='เปิดผลเดิม').click();f.locator('#settings-drawer').wait_for(state='hidden');assert f.locator('#results-list .reference-card').count()==2;assert f.locator('#openai-key').input_value()==''
  history();assert f.locator('.history-card').count()==3;f.locator('#history-enabled').uncheck();close();f.locator('#doi-input').fill('10.1000/another');f.locator('#doi-button').click();f.locator('#doi-progress').wait_for(state='hidden');page.wait_for_timeout(700);history();assert f.locator('.history-card').count()==3
  page.reload();f=page.frame_locator('iframe').first;f.locator('.hero h1').wait_for(timeout=30000);history();assert not f.locator('#history-enabled').is_checked()
  # Corrupt imports are rejected atomically.
  f.locator('#history-import').set_input_files({'name':'bad.json','mimeType':'application/json','buffer':b'{"format":"paperref-history","version":1,"entries":[{}]}'});f.locator('#history-status').filter(has_text='วันที่').wait_for();assert f.locator('.history-card').count()==3
  page.once('dialog',lambda d:d.accept());f.locator('#history-clear').click();f.locator('.history-empty').wait_for()
  f.locator('#history-import').set_input_files({'name':'history.json','mimeType':'application/json','buffer':saved.encode()});f.locator('#history-status').filter(has_text='นำเข้า 3').wait_for();assert f.locator('.history-card').count()==3
  f.locator('#history-import').set_input_files({'name':'history.json','mimeType':'application/json','buffer':saved.encode()});page.wait_for_timeout(500);assert f.locator('.history-card').count()==3
  f.locator('.history-card').first.get_by_role('button',name='ลบ',exact=False).click();page.wait_for_timeout(400);assert f.locator('.history-card').count()==2
  # Import does not retain secrets, unsafe links or unknown fields; text never becomes HTML.
  evil=json.loads(saved);evil['entries']=evil['entries'][:1];row=evil['entries'][0];row['id']='11111111-1111-1111-1111-111111111111';row['payload']['filename']='<img src=x onerror=alert(1)>';row['payload']['api_key']='do-not-store';row['payload']['results'][0]['paper_url']='javascript:alert(1)'
  f.locator('#history-import').set_input_files({'name':'evil.json','mimeType':'application/json','buffer':json.dumps(evil).encode()});page.wait_for_timeout(500);assert f.locator('#history-list img').count()==0
  with page.expect_download() as download:f.locator('#history-export').click()
  sanitized=Path(download.value.path()).read_text();assert 'do-not-store' not in sanitized and 'javascript:' not in sanitized
  for width,height,label in [(390,844,'phone'),(768,1024,'ipad')]:
   page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(500);assert f.locator('#settings-drawer').evaluate('el=>el.scrollWidth<=el.clientWidth');page.screenshot(path=str(Path(tempfile.gettempdir())/('paperref-history-'+label+'.png')))
  f.locator('#history-enabled').check()
  base=data['entries'][0]['payload']
  f.locator('body').evaluate("async (el,payload)=>{for(let i=0;i<51;i++)await window.PaperRefHistory.save({...payload,filename:'cap-test-'+i},'code')}",base)
  assert f.locator('.history-card').count()==50
  assert f.locator('.history-card h4').filter(has_text='cap-test-50').count()==1
  assert f.locator('.history-card h4').filter(has_text='cap-test-0').count()==0
  f.locator('body').evaluate("async (el,payload)=>await window.PaperRefHistory.save({...payload,filename:'deleted-should-not-return'},'code','00000000-0000-0000-0000-000000000000')",base)
  assert f.locator('.history-card h4').filter(has_text='deleted-should-not-return').count()==0
  # A failed transaction must preserve saved records.
  f.locator('body').evaluate("el=>{window._originalPut=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(){throw new DOMException('quota','QuotaExceededError')}}")
  f.locator('body').evaluate("async (el,payload)=>await window.PaperRefHistory.save(payload,'code')",base)
  f.locator('#history-status').filter(has_text='พื้นที่เก็บข้อมูลเต็ม').wait_for()
  assert f.locator('.history-card').count()==50
  f.locator('body').evaluate('el=>IDBObjectStore.prototype.put=window._originalPut')
  assert not errors,errors
  other=browser.new_context();fresh=other.new_page();fresh.route('https://**/*',lambda r:r.abort());fresh.goto(endpoint+'/');g=fresh.frame_locator('iframe').first;g.locator('.hero h1').wait_for(timeout=30000);g.locator('.sidebar-settings').click();g.locator('[data-settings-section=history]').click();g.locator('.history-empty').wait_for();assert g.locator('.history-card').count()==0
  denied=browser.new_context();denied.add_init_script("Object.defineProperty(window,'indexedDB',{get(){throw new DOMException('blocked','SecurityError')}})")
  d=denied.new_page();d.route('https://**/*',lambda r:r.abort());d.goto(endpoint+'/');h=d.frame_locator('iframe').first;h.locator('.hero h1').wait_for(timeout=30000)
  h.locator('#file-input').set_input_files({'name':'no-storage.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4 test'});h.locator('#analyze-button').click();h.locator('#results-list .reference-card').first.wait_for(timeout=30000)
  h.locator('.sidebar-settings').click();h.locator('[data-settings-section=history]').click();h.locator('#history-status').filter(has_text='IndexedDB ใช้งานไม่ได้').wait_for();assert h.locator('#history-enabled').is_disabled()
  browser.close()
 print('PASS: local history code/AI/DOI, reload/reopen, opt-out persistence, delete/clear, export/import/dedup, invalid imports, key exclusion, XSS/link sanitation, device isolation, mobile/iPad')
finally:
 server.terminate();server.wait(timeout=10);log.close()
