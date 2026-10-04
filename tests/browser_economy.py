"""Economy settings, pacing and ephemeral success reuse over the Streamlit bridge.

All API responses are fake. Browser time advances only for the pacing timer, so
this regression sends no provider request and does not spend minutes sleeping.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

if len(sys.argv)>1 and sys.argv[1]=='--serve':
    import streamlit_transport
    from starlette.requests import Request
    async def fake_app(scope,receive,send):
        path=scope['path'];form=await Request(scope,receive).form() if scope['method']=='POST' else {}
        economy=form.get('economy_mode')=='true';model=form.get('model','test')
        def paper(n):return {'reference_number':n,'title':'Paper '+str(n),'authors':['A. Example'],
            'original_text':'A. Example, Paper '+str(n)+', 2025.','year':'2025',
            'citation_contexts':[],'metadata_sources':[],'source_links':[],'access_status':'scholar_search'}
        async def response(payload,status=200):
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':json.dumps(payload).encode()})
        if path in ('/api/references','/api/doi'):
            await response({'mode':'doi' if path.endswith('/doi') else 'pdf','filename':'Economy fixture',
                            'total_references':3,'results':[paper(n) for n in (1,2,3)]});return
        if scope['method']=='GET':await response({});return
        if form.get('economy_mode') not in ('true','false'):
            await response({'detail':'Missing economy_mode'},400);return
        if model=='test-rate-limit':await response({'detail':'Provider HTTP 429'},429);return
        if path=='/api/summarize':payload={'summary':'Mock bounded summary','source':'PDF','provider':'openai','model':model}
        elif path.endswith('/intents'):payload={'intents':[{'id':1,'intent':'Background','reason':'Mock intent'}],'contexts':{}}
        elif path.endswith('/synthesis'):payload={'synthesis':'Mock bounded synthesis'}
        elif path.endswith('/qa'):payload={'answer':'Mock bounded answer'}
        elif path.endswith('/compare'):payload={'comparison':'Mock bounded comparison'}
        elif path.endswith('/clusters'):
            payload={'clusters':[{'name':'Methods','ids':[1,2]},{'name':'Results','ids':[3]}],'unassigned_ids':[]}
            if model=='test-invalid':payload['clusters'][1]['ids']=[1]
        elif path.endswith('/extract-references'):
            payload={'filename':'Economy fixture','total_references':3,'results':[paper(n) for n in (1,2,3)],
                     'extraction_complete':True,'expected_reference_count':3}
            if model=='test-recovered':payload['recovered_reference_numbers']=[3]
        else:await response({'detail':'Unknown mock route'},404);return
        if economy:payload['economy']={'enabled':True,'requests':2,'prompt_characters':1234,
            'interval_seconds':int(form.get('economy_interval',30)),'context_notice':'ใช้บริบทจำกัดจากส่วนที่เกี่ยวข้อง ไม่ได้อ่านทุกข้อความ'}
        if path.endswith(('/clusters','/extract-references')) or (path.endswith('/intents') and economy and form.get('stream')=='true'):
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/x-ndjson')]})
            event={'type':'progress','stage':'waiting','wait_seconds':15}
            if path.endswith('/extract-references') and economy and form.get('provider')=='gemini' and form.get('retry_unavailable')=='true':
                event.update(stage='retrying',part=1,total=2,attempt=1,max_attempts=1)
            await send({'type':'http.response.body','body':(json.dumps(event)+'\n').encode(),'more_body':True})
            await asyncio.sleep(.8)
            if model=='test-incomplete':
                await send({'type':'http.response.body','body':b''});return
            await send({'type':'http.response.body','body':(json.dumps({'type':'result','payload':payload})+'\n').encode()});return
        await response(payload)
    streamlit_transport.app=fake_app
    from streamlit.web import cli
    sys.argv=['streamlit','run',str(ROOT/'streamlit_app.py'),'--server.port='+sys.argv[2],
              '--server.address=127.0.0.1','--server.headless=true']
    cli.main()
    raise SystemExit

from playwright.sync_api import expect,sync_playwright
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
url=f'http://127.0.0.1:{port}'
server=subprocess.Popen([os.environ.get('PAPERREF_TEST_PYTHON',str(ROOT/'.venv/bin/python')),
    str(Path(__file__).resolve()),'--serve',str(port)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
try:
    for _ in range(150):
        try:urllib.request.urlopen(url+'/_stcore/health',timeout=1);break
        except OSError:time.sleep(.1)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000});page.route('https://**/*',lambda route:route.abort())
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.goto(url)
        frame=page.frame_locator('iframe').first;frame.locator('.hero h1').wait_for(timeout=30000)
        frame.locator('body').evaluate('''() => {
          window.economyClock={now:Date.now(),fast:true};Date.now=()=>window.economyClock.now;
          const timer=window.setTimeout;window.setTimeout=(fn,ms,...args)=>{
            if(ms===1000&&window.economyClock.fast){window.economyClock.now+=1000;return timer(fn,0,...args);}
            return timer(fn,ms,...args);
          };
          window.economyPosts=[];const original=window.fetch;
          window.fetch=(url,options={})=>{
            if(options.method==='POST'){
              const form=options.body instanceof FormData?options.body:null;
              window.economyPosts.push({url:String(url),at:Date.now(),economy:form?.get('economy_mode'),
                interval:form?.get('economy_interval'),provider:form?.get('provider'),
                model:form?.get('model'),payload:form?.get('payload'),retry:form?.get('retry_unavailable')});
            }
            return original(url,options);
          };
        }''')
        def posts(path=None):
            rows=frame.locator('body').evaluate('()=>window.economyPosts')
            return [row for row in rows if path is None or row['url']==path]
        def settings():frame.locator('.sidebar-settings').click()
        def close():frame.locator('#settings-close').click();frame.locator('#settings-drawer').wait_for(state='hidden')
        def model(value):
            settings();frame.locator('#summary-model').fill(value);frame.locator('#summary-model').dispatch_event('change');close()
        def summary():
            frame.locator('#summary-button').click();expect(frame.locator('#summary-button')).to_be_enabled(timeout=30000)
        settings();assert not frame.locator('#ai-economy-mode').is_checked();assert frame.locator('#ai-economy-interval').is_disabled()
        assert not frame.locator('#ai-retry-unavailable').is_checked() and frame.locator('#ai-retry-unavailable').is_disabled()
        frame.locator('#openai-key').fill('fake-economy-secret');frame.locator('#summary-model').fill('test');close()
        frame.locator('#file-input').set_input_files({'name':'paper.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4 fixture'})
        frame.locator('#analyze-button').click();frame.locator('#results-list .reference-card').first.wait_for(timeout=30000)
        assert posts('/api/references')[0]['economy'] is None
        summary();summary();assert len(posts('/api/summarize'))==2
        assert all(row['economy']=='false' and row['interval'] is None for row in posts('/api/summarize'))
        settings();frame.locator('#ai-economy-mode').check();assert not frame.locator('#ai-economy-interval').is_disabled()
        assert frame.locator('#ai-retry-unavailable').is_disabled();close()
        summary();count=len(posts('/api/summarize'));summary();assert len(posts('/api/summarize'))==count
        assert 'ใช้ผลเดิมในแท็บนี้' in frame.locator('#ai-economy-usage').inner_text()
        assert '1,234' in frame.locator('#ai-economy-usage').inner_text() and 'ไม่เรียก AI เพิ่ม' in frame.locator('#ai-economy-usage').inner_text()
        assert 'บริบทจำกัด' in frame.locator('#ai-economy-usage').inner_text()
        # Changing credentials invalidates the cache without including them in its fingerprint.
        settings();frame.locator('#openai-key').fill('changed-economy-secret');close();summary();assert len(posts('/api/summarize'))==count+1
        frame.locator('[data-ai=intents]').click();frame.locator('#ai-output').filter(has_text='อีก 15').wait_for(timeout=30000)
        assert 'อีก 15' not in frame.locator('#extraction-output').inner_text()
        expect(frame.locator('[data-ai=intents]')).to_be_enabled(timeout=30000)
        count=len(posts('/api/ai/intents'));frame.locator('[data-ai=intents]').click();expect(frame.locator('[data-ai=intents]')).to_be_enabled(timeout=30000);assert len(posts('/api/ai/intents'))==count
        for box in frame.locator('#results-list input[type=checkbox]').all():box.check()
        frame.locator('[data-ai=synthesis]').click();frame.locator('#ai-output').filter(has_text='Mock bounded synthesis').wait_for(timeout=30000)
        frame.locator('[data-ai=clusters]').click();frame.locator('#ai-output').filter(has_text='อีก 15').wait_for(timeout=30000)
        assert 'undefined' not in frame.locator('#ai-output').inner_text()
        expect(frame.locator('[data-ai=clusters]')).to_be_enabled(timeout=30000)
        count=len(posts('/api/ai/clusters'));frame.locator('[data-ai=clusters]').click();expect(frame.locator('[data-ai=clusters]')).to_be_enabled(timeout=30000);assert len(posts('/api/ai/clusters'))==count
        frame.locator('#results-list input[type=checkbox]').last.uncheck()
        frame.locator('[data-ai=clusters]').click();expect(frame.locator('[data-ai=clusters]')).to_be_enabled(timeout=30000);assert len(posts('/api/ai/clusters'))==count+1
        # Identical words with different conversation history are different requests.
        for _ in range(2):
            frame.locator('#qa-question').fill('What evidence is available?');frame.locator('#qa-form button').click();expect(frame.locator('#qa-form button')).to_be_enabled(timeout=30000)
        qa=posts('/api/ai/qa');assert len(qa)==2 and qa[0]['payload']!=qa[1]['payload']
        frame.locator('[data-ai=extract-references]').click();frame.locator('#dataset-ai[aria-pressed=true]').wait_for(timeout=30000)
        assert 'บริบทจำกัด' in frame.locator('#extraction-economy-usage').inner_text()
        frame.locator('body').evaluate('''async()=>{await window.PaperRefHistory.clearAll();for(let n=1;n<=2;n++)await window.PaperRefHistory.save({filename:'Study '+n,source_paper:{title:'Study '+n,doi:'10.1000/study'+n},results:[{reference_number:1,title:'Reference',original_text:'Reference'}]},'doi')}''')
        settings();frame.locator('[data-settings-section=history]').click()
        for box in frame.locator('#history-list input[type=checkbox]').all():box.check()
        frame.locator('#workspace-compare').click();frame.locator('#workspace-output').filter(has_text='Mock bounded comparison').wait_for(timeout=30000)
        assert 'บริบทจำกัด' in frame.locator('#workspace-economy-usage').inner_text()
        close()
        ai=[row for row in posts() if row['url'].startswith('/api/ai/') or row['url']=='/api/summarize']
        enabled=[row for row in ai if row['economy']=='true'];assert all(row['interval']=='30' for row in enabled)
        assert {row['url'] for row in enabled}=={'/api/summarize','/api/ai/intents','/api/ai/synthesis','/api/ai/clusters','/api/ai/qa','/api/ai/extract-references','/api/ai/compare'}
        assert all(enabled[index]['at']-enabled[index-1]['at']>=30000 for index in range(1,len(enabled)))
        assert all(row['retry'] is None for row in posts())  # Existing actions never silently enable retries.
        # An explicitly opted-in Gemini extraction can show its one 503 retry without enabling other features.
        settings();frame.locator('#ai-provider').select_option('gemini');frame.locator('#openai-key').fill('fake-gemini-retry-secret')
        frame.locator('#summary-model').fill('test');assert not frame.locator('#ai-retry-unavailable').is_disabled()
        assert not frame.locator('#ai-retry-unavailable').is_checked();close()
        frame.locator('[data-ai=extract-references]').click();expect(frame.locator('[data-ai=extract-references]')).to_be_enabled(timeout=30000)
        assert posts('/api/ai/extract-references')[-1]['retry'] is None
        settings();frame.locator('#ai-retry-unavailable').check();close()
        before=len(posts('/api/ai/extract-references'));frame.locator('[data-ai=extract-references]').click()
        frame.locator('#extraction-output').filter(has_text='503').wait_for(timeout=30000)
        retry_text=frame.locator('#extraction-output').inner_text()
        assert '1/2' in retry_text and '15' in retry_text and 'Gemini' in retry_text,retry_text
        assert '503' not in frame.locator('#ai-output').inner_text()
        expect(frame.locator('[data-ai=extract-references]')).to_be_enabled(timeout=30000)
        assert len(posts('/api/ai/extract-references'))==before+1 and posts('/api/ai/extract-references')[-1]['retry']=='true'
        summary();assert posts('/api/summarize')[-1]['retry'] is None
        # Toggling only the retry setting cancels a pending AI action before its first HTTP request.
        frame.locator('body').evaluate('()=>window.economyClock.fast=false')
        before=len(posts('/api/summarize'));model('test-retry-wait');frame.locator('#summary-button').click()
        frame.locator('#ai-progress').filter(has_text='กำลังพัก').wait_for()
        settings();frame.locator('#ai-retry-unavailable').uncheck();close()
        expect(frame.locator('#summary-button')).to_be_enabled(timeout=5000);assert len(posts('/api/summarize'))==before
        frame.locator('body').evaluate('()=>window.economyClock.fast=true')
        settings();frame.locator('#ai-retry-unavailable').check();frame.locator('#ai-economy-mode').uncheck()
        assert frame.locator('#ai-retry-unavailable').is_disabled() and not frame.locator('#ai-retry-unavailable').is_checked()
        frame.locator('#ai-economy-mode').check();frame.locator('#ai-retry-unavailable').check();frame.locator('#ai-provider').select_option('openai')
        assert frame.locator('#ai-retry-unavailable').is_disabled() and not frame.locator('#ai-retry-unavailable').is_checked()
        frame.locator('#openai-key').fill('fake-economy-secret');close()
        # HTTP failures, midstream failures and invalid HTTP-200 graph results cannot be reused.
        model('test-rate-limit');before=len(posts('/api/summarize'));summary();summary();assert len(posts('/api/summarize'))==before+2
        assert '429' in frame.locator('#summary-output').inner_text()
        model('test-invalid');before=len(posts('/api/ai/clusters'))
        for _ in range(2):frame.locator('[data-ai=clusters]').click();expect(frame.locator('[data-ai=clusters]')).to_be_enabled(timeout=30000)
        assert len(posts('/api/ai/clusters'))==before+2 and 'ผลกราฟเดิมยังอยู่' in frame.locator('#ai-output').inner_text()
        model('test-incomplete');before=len(posts('/api/ai/extract-references'))
        for _ in range(2):frame.locator('[data-ai=extract-references]').click();expect(frame.locator('[data-ai=extract-references]')).to_be_enabled(timeout=30000)
        assert len(posts('/api/ai/extract-references'))==before+2
        model('test-recovered');before=len(posts('/api/ai/extract-references'))
        for _ in range(2):frame.locator('[data-ai=extract-references]').click();expect(frame.locator('[data-ai=extract-references]')).to_be_enabled(timeout=30000)
        assert len(posts('/api/ai/extract-references'))==before+2  # Count coverage alone is not complete AI extraction.
        model('test');settings();frame.locator('#ai-economy-interval').select_option('60');close();summary();assert posts('/api/summarize')[-1]['interval']=='60'
        # A changed key or mode cancels an action still waiting before fetch.
        model('test-wait')
        frame.locator('body').evaluate('()=>window.economyClock.fast=false')
        before=len(posts('/api/summarize'));frame.locator('#summary-button').click();frame.locator('#ai-progress').filter(has_text='กำลังพัก').wait_for()
        settings();frame.locator('#openai-key').fill('fresh-economy-secret');close();expect(frame.locator('#summary-button')).to_be_enabled(timeout=5000);assert len(posts('/api/summarize'))==before
        frame.locator('#summary-button').click();frame.locator('#ai-progress').filter(has_text='กำลังพัก').wait_for();settings();frame.locator('#ai-economy-mode').uncheck();close();expect(frame.locator('#summary-button')).to_be_enabled(timeout=5000);assert len(posts('/api/summarize'))==before
        frame.locator('body').evaluate('()=>window.economyClock.fast=true')
        # Public DOI remains free of Economy fields; source replacement cannot reuse old summaries.
        frame.locator('#doi-input').fill('10.1000/new-source');frame.locator('#doi-button').click();frame.locator('#dataset-doi[aria-pressed=true]').wait_for(timeout=30000)
        assert posts('/api/doi')[-1]['economy'] is None
        settings();frame.locator('#ai-economy-mode').check();frame.locator('#ai-economy-interval').select_option('120');frame.locator('#remember-ai-preferences').check();close()
        before=len(posts('/api/summarize'));summary();assert len(posts('/api/summarize'))==before+1;assert posts('/api/summarize')[-1]['interval']=='120'
        summary();assert len(posts('/api/summarize'))==before+1
        frame.locator('#doi-input').fill('10.1000/source-replaced');frame.locator('#doi-button').click();frame.locator('#doi-progress').wait_for(state='hidden')
        summary();assert len(posts('/api/summarize'))==before+2  # Source replacement invalidates a prior successful summary.
        prefs=frame.locator('body').evaluate("()=>JSON.parse(localStorage.getItem('paperref.ai-preferences.v1'))")
        assert set(prefs)=={'provider','model'}
        settings();frame.locator('[data-settings-section=history]').click()
        with page.expect_download() as download:frame.locator('#history-export').click()
        saved=Path(download.value.path()).read_text();assert 'economy-secret' not in saved and 'api_key' not in saved and 'PaperRefAIBudget' not in saved
        for width,height in [(390,844),(768,1024)]:
            page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(100);assert frame.locator('#settings-drawer').evaluate('el=>el.scrollWidth<=el.clientWidth')
        frame.locator('[data-settings-section=ai]').click();frame.locator('#ai-provider').select_option('gemini');frame.locator('#ai-retry-unavailable').check()
        page.reload();frame=page.frame_locator('iframe').first;frame.locator('.hero h1').wait_for(timeout=30000);settings();assert not frame.locator('#ai-economy-mode').is_checked();assert frame.locator('#ai-economy-interval').input_value()=='30';assert frame.locator('#openai-key').input_value()==''
        assert not frame.locator('#ai-retry-unavailable').is_checked() and frame.locator('#ai-retry-unavailable').is_disabled()
        assert not errors,errors;browser.close()
    print('PASS: Economy default off, all seven AI forms, explicit extraction-only Gemini 503 retry/progress/reset, retry-setting cancellation and no persistence, actual-cost/limited-context notices, bounded ephemeral success reuse, key/selection/chat/source invalidation, 30/60/120 pacing after responses, cancelled waiting actions, no retry/cache of 429/invalid/partial results, unchanged public searches, privacy and real Streamlit component')
finally:server.terminate();server.wait(timeout=10)
