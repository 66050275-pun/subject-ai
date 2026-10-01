import os,sys,subprocess,socket,urllib.request,time,json,tempfile,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
url=f'http://127.0.0.1:{port}'
server=subprocess.Popen([os.environ.get('PAPERREF_TEST_PYTHON',sys.executable),str(root/'tests/browser_mock_streamlit.py'),str(port)],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
try:
 for _ in range(100):
  try:urllib.request.urlopen(url+'/_stcore/health',timeout=1);break
  except:time.sleep(.1)
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('PAPERREF_TEST_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':900});page.route('https://**/*',lambda r:r.abort());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(url);f=page.frame_locator('iframe').first;f.locator('.hero h1').wait_for(timeout=30000)
  f.locator('#file-input').set_input_files({'name':'source.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4 test'});f.locator('#analyze-button').click();f.locator('#results-list .reference-card').first.wait_for(timeout=30000)
  f.locator('.sidebar-settings').click();f.locator('#openai-key').fill('fake-secret-key');f.locator('#settings-close').click();f.locator('#settings-drawer').wait_for(state='hidden')
  f.locator('#summary-button').click();f.locator('#summary-output').filter(has_text='Mock shared summary').wait_for(timeout=30000)
  f.locator('#network-panel > summary').click();f.locator('#graph-zoom-in').click();page.wait_for_timeout(900)
  f.locator('.sidebar-settings').click();f.locator('[data-settings-section=history]').click()
  with page.expect_download() as download:f.locator('#history-export').click()
  backup=json.loads(Path(download.value.path()).read_text());assert backup['entries'][0]['payload']['workspace']['summary']=='Mock shared summary';assert backup['entries'][0]['payload']['workspace']['graphs']['references']['zoom']>1
  # Add a second research source with a shared DOI reference and author/year metadata.
  first=backup['entries'][0];first['payload']['source_paper']={'title':'Study One','authors':['Alice'],'year':'2024','doi':'10.1000/one'};first['payload']['results'][0]['doi']='10.1000/shared'
  import copy
  second=copy.deepcopy(first);second['id']='22222222-2222-2222-2222-222222222222';second['payload']['filename']='Study Two';second['payload']['source_paper']={'title':'Study Two','authors':['Bob'],'year':'2025','doi':'10.1000/two'}
  backup['entries'].append(second)
  f.locator('#history-import').set_input_files({'name':'history.json','mimeType':'application/json','buffer':json.dumps(backup).encode()});f.locator('#history-status').filter(has_text='นำเข้า 2').wait_for()
  f.locator('#history-search').fill('Alice');assert f.locator('.history-card').count()==1;f.locator('#history-search').fill('2025');assert f.locator('.history-card').count()==1;f.locator('#history-search').fill('')
  for box in f.locator('#history-list .history-card input[type=checkbox]').all():box.check()
  f.locator('#workspace-shared').click();f.locator('#workspace-output').filter(has_text='อ้างถึงโดย 2').wait_for()
  with page.expect_download() as download:f.locator('#workspace-bib').click()
  bib=Path(download.value.path()).read_text();assert bib.count('10.1000/shared')==1;assert '@misc{' in bib
  f.locator('#workspace-compare').click();f.locator('#workspace-output').filter(has_text='Mock cross-paper comparison').wait_for(timeout=30000)
  f.locator('#history-incognito').check();f.locator('#settings-close').click();f.locator('#settings-drawer').wait_for(state='hidden')
  f.locator('#doi-input').fill('10.1000/three');f.locator('#doi-button').click();f.locator('#dataset-doi[aria-pressed=true]').wait_for(timeout=30000)
  f.locator('.sidebar-settings').click();f.locator('[data-settings-section=history]').click();assert f.locator('#history-list .history-card').count()==2
  f.locator('#history-list .history-card').filter(has_text='Study Two').get_by_role('button',name='เปิดผลเดิม').click();f.locator('#settings-drawer').wait_for(state='hidden');assert f.locator('#summary-output').inner_text()=='Mock shared summary';assert f.locator('#graph-zoom-label').inner_text()=='120%'
  page.set_viewport_size({'width':390,'height':844});f.locator('.sidebar-settings').click();f.locator('[data-settings-section=history]').click();assert f.locator('#settings-drawer').evaluate('el=>el.scrollWidth<=el.clientWidth');page.screenshot(path=str(Path(tempfile.gettempdir())/'paperref-workspace-phone.png'))
  assert not errors,errors;browser.close()
 print('PASS: summary/graph persisted, author/year search, shared references, deduplicated BibTeX, comparison API bridge, incognito, offline restore, mobile')
finally:server.terminate();server.wait(timeout=10)
