/* Device-local result snapshots. Only explicitly allowed metadata is persisted. */
(() => {
  const MAX_RECORDS=50,MAX_TOTAL=8*1024*1024,MAX_RECORD=1024*1024;
  const list=document.getElementById('history-list'),status=document.getElementById('history-status'),toggle=document.getElementById('history-enabled');
  const bytes=value=>new Blob([JSON.stringify(value)]).size;
  const text=(value,max=1200)=>{let out=typeof value==='string'?value.slice(0,max):'';const key=document.getElementById('openai-key')?.value.trim();if(key&&key.length>=8)out=out.split(key).join('[redacted]');return out;};
  const url=value=>{try{const u=new URL(value);const key=document.getElementById('openai-key')?.value.trim();const secret=[...u.searchParams.keys()].some(k=>/^(api[_-]?key|access[_-]?token|authorization|x-ai-api-key|token|x-amz-signature)$/i.test(k));return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password&&!secret&&!(key&&key.length>=8&&u.href.includes(key))?u.href:'';}catch(_){return '';}};
  const workspace=value=>{
    const out={summary:text(value?.summary,12000),analysis:text(value?.analysis,12000),graphs:{},themes:Array.isArray(value?.themes)?value.themes.slice(0,1000).filter(t=>Number.isSafeInteger(t.id)&&/^#[0-9a-f]{6}$/i.test(t.color)).map(t=>({id:t.id,color:t.color,name:text(t.name,120)})):[]};
    for(const view of ['references','citations']){const state=value?.graphs?.[view];if(!state||!Array.isArray(state.nodes))continue;
      out.graphs[view]={zoom:Number.isFinite(state.zoom)?Math.max(.08,Math.min(5,state.zoom)):1,offsetX:Number.isFinite(state.offsetX)?Math.max(-100000,Math.min(100000,state.offsetX)):0,offsetY:Number.isFinite(state.offsetY)?Math.max(-100000,Math.min(100000,state.offsetY)):0,nodes:state.nodes.slice(0,1100).filter(n=>typeof n.id==='string'&&n.id.length<=200&&Number.isFinite(n.x)&&Number.isFinite(n.y)).map(n=>({id:text(n.id,200),x:Math.max(-100000,Math.min(100000,n.x)),y:Math.max(-100000,Math.min(100000,n.y))}))};
    }
    return out;
  };
  const paper=value=>{
    if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('ข้อมูลรายการอ้างอิงไม่ถูกต้อง');
    const out={};
    for(const key of ['title','matched_title','doi','year','original_text','access_label','access_detail','abstract','journal'])out[key]=text(value[key],key==='original_text'?4000:key==='abstract'?8000:key==='year'?10:key==='doi'?300:1200);
    out.title_source=text(value.title_source,80);
    out.title_status=['verified','from_pdf','unresolved'].includes(value.title_status)?value.title_status:'';
    out.metadata_search={};
    if(value.metadata_search&&typeof value.metadata_search==='object')for(const [source,result] of Object.entries(value.metadata_search).slice(0,10))if(/^[\w .()/-]{1,80}$/.test(source)&&typeof result==='string')out.metadata_search[source]=text(result,150);
    for(const key of ['paper_url','oa_pdf_url','scholar_url'])out[key]=url(value[key]);
    out.authors=Array.isArray(value.authors)?value.authors.filter(v=>typeof v==='string').slice(0,40).map(v=>text(v,300)):[];
    out.reference_number=Number.isSafeInteger(value.reference_number)&&value.reference_number>0?value.reference_number:1;
    out.citation_mentions=Number.isSafeInteger(value.citation_mentions)?Math.max(0,value.citation_mentions):0;
    out.extraction_fallback=value.extraction_fallback===true;
    out.access_status=['pdf_available','open_source_record','scholar_search'].includes(value.access_status)?value.access_status:'scholar_search';
    out.metadata_sources=Array.isArray(value.metadata_sources)?value.metadata_sources.slice(0,20).map(v=>text(v,80)):[];
    out.source_links=Array.isArray(value.source_links)?value.source_links.slice(0,20).map(v=>({url:url(v?.url),name:text(v?.name,80),source:text(v?.source,80),label:text(v?.label,100)})).filter(v=>v.url):[];
    out.pdf_locations=Array.isArray(value.pdf_locations)?value.pdf_locations.slice(0,20).map(v=>({url:url(v?.url),landing_url:url(v?.landing_url),source:text(v?.source,80),version:text(v?.version,80)})).filter(v=>v.url):[];
    out.citation_contexts=[]; // Body excerpts, AI answers and chat are not saved.
    return out;
  };
  const snapshot=(payload,kind)=>{
    if(!['code','ai','doi'].includes(kind)||!payload||!Array.isArray(payload.results)||payload.results.length>1000)throw new Error('รูปแบบประวัติไม่ถูกต้อง');
    if(payload.citing_papers&&!Array.isArray(payload.citing_papers))throw new Error('รูปแบบ citation ไม่ถูกต้อง');
    if((payload.citing_papers||[]).length>1000)throw new Error('รายการ citation มากเกินไป');
    const out={filename:text(payload.filename,500),mode:kind==='doi'?'doi':'pdf',results:payload.results.map(paper),citing_papers:(payload.citing_papers||[]).map(paper),source_paper:payload.source_paper?paper(payload.source_paper):null,workspace:workspace(payload.workspace)};
    out.total_references=out.results.length;
    out.extraction_warnings=Array.isArray(payload.extraction_warnings)?payload.extraction_warnings.slice(0,10).map(v=>text(v,1200)):[];
    out.expected_reference_count=Number.isSafeInteger(payload.expected_reference_count)&&payload.expected_reference_count>0?payload.expected_reference_count:null;
    out.extraction_complete=payload.extraction_complete===true;
    for(const key of ['citation_link_count','cited_reference_count'])out[key]=Number.isSafeInteger(payload[key])?Math.max(0,payload[key]):0;
    for(const key of ['citation_links_available','references_truncated','citations_truncated'])out[key]=payload[key]===true;
    if(bytes(out)>MAX_RECORD)throw new Error('ผลชุดนี้ใหญ่เกิน 1 MB จึงไม่ได้บันทึกประวัติ ใช้ export ผลรายการแทนได้');
    return out;
  };
  const message=error=>{status.textContent=error?.name==='QuotaExceededError'?'พื้นที่เก็บข้อมูลเต็ม ประวัติเดิมยังอยู่ ลบหรือ export ประวัติก่อน':(error instanceof Error?error.message:String(error));};
  let db=null,enabled=true,records=[],onRestore=null;
  const selected=new Set();
  let incognito=false,storageReady=false,savingPreference=false;
  function syncPrivacyControls(){
    toggle.checked=enabled&&!incognito;
    toggle.disabled=!storageReady||savingPreference||incognito;
    document.documentElement.toggleAttribute('data-incognito',incognito);
    if(incognito)document.documentElement.dataset.theme='dark';
    else delete document.documentElement.dataset.theme;
  }
  const ready=new Promise(resolve=>{
    try{
      const request=indexedDB.open('paperref-history',1);
      request.onupgradeneeded=()=>{request.result.createObjectStore('searches',{keyPath:'id'});request.result.createObjectStore('preferences');};
      request.onerror=()=>{message('เบราว์เซอร์ไม่อนุญาต IndexedDB ยังค้นหาได้ตามปกติ แต่บันทึกประวัติไม่ได้');resolve(false);};
      request.onblocked=()=>message('กรุณาปิดแท็บ PaperRef อื่นแล้วลองรีเฟรชเพื่อเปิดประวัติ');
      request.onsuccess=()=>{
        db=request.result;db.onversionchange=()=>db.close();
        const tx=db.transaction(['searches','preferences'],'readonly');
        tx.objectStore('searches').getAll().onsuccess=e=>records=e.target.result;
        tx.objectStore('preferences').get('enabled').onsuccess=e=>enabled=e.target.result!==false;
        tx.oncomplete=()=>{storageReady=true;syncPrivacyControls();render();resolve(true);};
        tx.onabort=()=>{message('อ่านประวัติไม่สำเร็จ');resolve(false);};
      };
    }catch(_){message('IndexedDB ใช้งานไม่ได้ในเบราว์เซอร์นี้ ยังค้นหาได้ตามปกติ');resolve(false);}
  });
  const commit=change=>new Promise((resolve,reject)=>{
    const tx=db.transaction('searches','readwrite'),store=tx.objectStore('searches');let next;
    store.getAll().onsuccess=e=>{
      try{
        next=change(e.target.result).sort((a,b)=>b.savedAt.localeCompare(a.savedAt));
        next=next.slice(0,MAX_RECORDS);while(bytes(next)>MAX_TOTAL&&next.length>1)next.pop();
        store.clear();for(const record of next)store.put(record);
      }catch(error){tx.abort();reject(error);}
    };
    tx.oncomplete=()=>{records=next;render();resolve();};tx.onabort=()=>reject(tx.error||new Error('บันทึกไม่สำเร็จ ประวัติเดิมยังอยู่'));
  });
  const save=async(payload,kind,id=null)=>{
    if(!enabled||incognito)return null;
    if(!await ready||!enabled||incognito)return null;
    try{
      const record={id:id||crypto.randomUUID(),kind,savedAt:new Date().toISOString(),payload:snapshot(payload,kind)};
      let written=true;
      await commit(rows=>{if(id&&!rows.some(r=>r.id===id)){written=false;return rows;}const previous=rows.find(r=>r.id===record.id);if(previous)record.savedAt=previous.savedAt;return [record,...rows.filter(r=>r.id!==record.id)];});
      if(!written)return null;status.textContent='บันทึกประวัติในเบราว์เซอร์นี้แล้ว';return record.id;
    }catch(error){message(error);return null;}
  };
  function render(){
    for(const id of selected)if(!records.some(r=>r.id===id))selected.delete(id);
    document.getElementById('workspace-selection').textContent=`เลือก ${selected.size} เปเปอร์ · เปรียบเทียบ AI ได้ 2–3 เรื่อง`;
    list.replaceChildren();document.getElementById('history-count').textContent=`${records.length}/50 รายการ · ${(bytes(records)/1024/1024).toFixed(2)} MB`;
    document.getElementById('history-export').disabled=!records.length;document.getElementById('history-clear').disabled=!records.length;
    if(!searchSessions(document.getElementById('history-search').value).length){const p=document.createElement('p');p.className='history-empty';p.textContent='ยังไม่มีประวัติ ค้นหาจาก PDF หรือ DOI เพื่อเริ่มบันทึก';list.append(p);return;}
    for(const record of searchSessions(document.getElementById('history-search').value).sort((a,b)=>b.savedAt.localeCompare(a.savedAt))){
      const card=document.createElement('article');card.className='history-card';
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selected.has(record.id);checkbox.setAttribute('aria-label','เลือก '+record.payload.filename);checkbox.onchange=()=>{if(checkbox.checked)selected.add(record.id);else selected.delete(record.id);render();};card.append(checkbox);
      const badge=document.createElement('span');badge.className='history-access';badge.textContent=(record.kind==='doi'?'DOI':'PDF')+' · PDF เปิด '+record.payload.results.filter(p=>p.oa_pdf_url).length+'/'+record.payload.results.length;card.append(badge);
      const title=document.createElement('h4');title.textContent=record.payload.filename||record.payload.source_paper?.doi||'งานวิจัย';
      const meta=document.createElement('p');meta.textContent=`${{code:'สกัดด้วยโค้ด',ai:'สกัดด้วย AI',doi:'ค้น DOI'}[record.kind]} · ${record.payload.results.length} อ้างอิง · ${new Date(record.savedAt).toLocaleString('th-TH')}`;
      const actions=document.createElement('div');const open=document.createElement('button');open.type='button';open.textContent='เปิดผลเดิม →';open.className='text-button';open.onclick=()=>{if(onRestore?.(structuredClone(record.payload),record.kind,record.id)!==false)window.PaperRefSettings.close();};
      const remove=document.createElement('button');remove.type='button';remove.textContent='ลบ';remove.setAttribute('aria-label','ลบประวัติ '+title.textContent);remove.onclick=async()=>{try{await commit(rows=>rows.filter(r=>r.id!==record.id));status.textContent='ลบรายการแล้ว';}catch(error){message(error);}};
      const bib=document.createElement('button');bib.type='button';bib.textContent='.bib';bib.onclick=()=>downloadBib([record]);actions.append(open,bib,remove);card.append(title,meta,actions);list.append(card);
    }
  }
  toggle.onchange=async()=>{
    const requested=toggle.checked,previous=enabled;enabled=requested;
    if(!await ready){enabled=previous;syncPrivacyControls();return;}savingPreference=true;syncPrivacyControls();
    try{await new Promise((resolve,reject)=>{const tx=db.transaction('preferences','readwrite');tx.objectStore('preferences').put(requested,'enabled');tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});enabled=requested;status.textContent=enabled?'เปิดบันทึกประวัติแล้ว':'หยุดบันทึกใหม่แล้ว ประวัติเดิมยังอยู่ ลบได้ด้วยปุ่มล้างประวัติ';}
    catch(error){enabled=previous;message(error);}finally{savingPreference=false;syncPrivacyControls();}
  };
  document.getElementById('history-clear').onclick=async()=>{if(!confirm('ลบประวัติทั้งหมดในเบราว์เซอร์นี้? ดาวน์โหลดสำรองก่อนหากต้องการเก็บไว้'))return;try{await commit(()=>[]);status.textContent='ล้างประวัติแล้ว';}catch(error){message(error);}};
  document.getElementById('history-export').onclick=()=>{
    const blob=new Blob([JSON.stringify({format:'paperref-history',version:1,entries:records},null,2)],{type:'application/json'});
    const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='paperref-history.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  };
  document.getElementById('history-import').onchange=async event=>{
    const file=event.target.files[0];event.target.value='';if(!file||!await ready)return;
    try{
      if(file.size>16*1024*1024)throw new Error('ไฟล์ประวัติต้องไม่เกิน 16 MB');
      const data=JSON.parse(await file.text());if(data.format!=='paperref-history'||data.version!==1||!Array.isArray(data.entries)||data.entries.length>MAX_RECORDS)throw new Error('ใช้ไฟล์ JSON ที่ export จากประวัติ PaperRef ไม่เกิน 50 รายการ');
      const imported=data.entries.map(entry=>{
        if(!entry||typeof entry.savedAt!=='string'||!Number.isFinite(Date.parse(entry.savedAt)))throw new Error('วันที่ในประวัติไม่ถูกต้อง');
        return {id:typeof entry.id==='string'&&/^[a-f0-9-]{36}$/i.test(entry.id)?entry.id:crypto.randomUUID(),kind:entry.kind,savedAt:new Date(entry.savedAt).toISOString(),payload:snapshot(entry.payload,entry.kind)};
      });
      const merged=new Map(records.map(r=>[r.id,r]));for(const r of imported)merged.set(r.id,r);
      if(merged.size>MAX_RECORDS||bytes([...merged.values()])>MAX_TOTAL)throw new Error('ประวัติรวมเกิน 50 รายการหรือ 8 MB กรุณาลบรายการก่อนนำเข้า');
      await commit(rows=>{const map=new Map(rows.map(r=>[r.id,r]));for(const r of imported)map.set(r.id,r);if(map.size>MAX_RECORDS||bytes([...map.values()])>MAX_TOTAL)throw new Error('พื้นที่ประวัติเต็ม กรุณาลบรายการก่อนนำเข้า');return [...map.values()];});status.textContent=`นำเข้า ${imported.length} รายการแล้ว`;
    }catch(error){message(error);}
  };
  function searchSessions(query=''){
    const q=String(query).normalize('NFKC').toLocaleLowerCase().trim();return structuredClone(records.filter(r=>!q||[r.payload.filename,r.payload.source_paper?.title,r.payload.source_paper?.matched_title,r.payload.source_paper?.year,...(r.payload.source_paper?.authors||[])].join(' ').normalize('NFKC').toLocaleLowerCase().includes(q)));
  }
  const getSessions=async()=>{await ready;return structuredClone(records);};
  const deleteSession=async id=>{if(await ready)await commit(rows=>rows.filter(r=>r.id!==id));};
  const clearAll=async()=>{if(await ready)await commit(()=>[]);};
  const download=(content,name,type)=>{const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([content],{type}));link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);};
  function downloadBib(rows){download(window.PaperRefWorkspace.bibtex(rows),'paperref-references.bib','application/x-bibtex');}
  document.getElementById('history-search').oninput=render;
  document.getElementById('history-incognito').onchange=event=>{incognito=event.target.checked;syncPrivacyControls();status.textContent=incognito?'โหมดไม่บันทึก: ไม่เพิ่มหรืออัปเดตประวัติจนกว่าจะปิดโหมดนี้':'ปิดโหมดไม่บันทึกแล้ว';};
  document.getElementById('workspace-bib').onclick=()=>{const rows=records.filter(r=>selected.has(r.id));if(!rows.length){status.textContent='เลือกอย่างน้อย 1 เปเปอร์';return;}downloadBib(rows);};
  document.getElementById('workspace-shared').onclick=()=>{
    const rows=window.PaperRefWorkspace.uniqueSessions(records.filter(r=>selected.has(r.id))),output=document.getElementById('workspace-output');output.replaceChildren();
    if(rows.length<2){output.textContent='เลือกอย่างน้อย 2 เปเปอร์';return;}
    const shared=window.PaperRefWorkspace.sharedReferences(rows);
    const note=document.createElement('p');note.textContent=`พบ ${shared.length} อ้างอิงร่วม · เป็นจุดร่วมที่อาจสำคัญ ยังไม่ใช่หลักฐานว่าเป็น seminal paper`;output.append(note);
    for(const group of shared){const card=document.createElement('article');card.className='history-card shared-reference';const h=document.createElement('h4');h.textContent=group.paper.matched_title||group.paper.title;const p=document.createElement('p');p.textContent=`อ้างถึงโดย ${group.sessions.length} เปเปอร์ · ${group.match} · `+group.sessions.map(s=>s.title).join(' / ');card.append(h,p);output.append(card);}
  };
  document.getElementById('workspace-compare').onclick=async()=>{
    window.PaperRefAIBudget.report(null,'workspace-economy-usage');
    const rows=window.PaperRefWorkspace.uniqueSessions(records.filter(r=>selected.has(r.id))),output=document.getElementById('workspace-output'),button=document.getElementById('workspace-compare');
    if(rows.length<2||rows.length>3){output.textContent='เลือก 2–3 เปเปอร์';return;}
    const key=document.getElementById('openai-key').value.trim();if(!key){window.PaperRefSettings.open('ai');return;}
    const papers=rows.map(r=>({title:r.payload.source_paper?.matched_title||r.payload.source_paper?.title||r.payload.filename,doi:r.payload.source_paper?.doi||null,year:r.payload.source_paper?.year||'',authors:r.payload.source_paper?.authors||[],abstract:r.payload.source_paper?.abstract?.slice(0,8000)||'',summary:r.payload.workspace?.summary||'',references:r.payload.results.slice(0,100).map(p=>({id:p.reference_number,title:p.matched_title||p.title||p.original_text,doi:p.doi||null,year:p.year||null,original_text:''}))}));
    const form=new FormData();form.append('provider',document.getElementById('ai-provider').value);form.append('model',document.getElementById('summary-model').value);form.append('payload',JSON.stringify({papers}));if(document.getElementById('ai-provider').value==='maxplus')form.append('base_url',document.getElementById('maxplus-base-url').value);if(document.getElementById('ai-provider').value==='alibaba')form.append('base_url',document.getElementById('alibaba-region').value);
    const economy=window.PaperRefAIBudget.append(form),selection=[...selected].sort();
    button.disabled=true;window.PaperRefProgress.start('workspace-progress','กำลังเปรียบเทียบเปเปอร์จาก metadata และสรุปที่บันทึกไว้…');output.textContent='';
    try{const data=await window.PaperRefAIBudget.run(async()=>{const response=await fetch('/api/ai/compare',{method:'POST',body:form,headers:{'X-AI-API-Key':key}});const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'ข้อมูลเปรียบเทียบไม่ถูกต้อง');return value;},{profile:economy,reuse:false,isCurrent:()=>JSON.stringify([...selected].sort())===JSON.stringify(selection),onWait:seconds=>window.PaperRefProgress.update('workspace-progress',`โหมดประหยัด · กำลังพักก่อนเปรียบเทียบ อีก ${seconds} วินาที`)});if(typeof data.comparison!=='string'||!data.comparison.trim())throw new Error('AI ไม่ส่งข้อความเปรียบเทียบที่อ่านได้');output.textContent=data.comparison+'\n\nหลักฐาน: metadata/abstract/สรุป AI ที่บันทึกไว้ ไม่ได้อ่าน PDF ใหม่';window.PaperRefAIBudget.report(data,'workspace-economy-usage');}catch(error){output.textContent=error.message;}finally{button.disabled=false;window.PaperRefProgress.finish('workspace-progress');}
  };
  window.PaperRefHistory={isEphemeral:()=>incognito||!enabled,initDB:()=>ready,saveSession:save,save,getSessions,deleteSession,clearAll,searchSessions,
    exportJSON:()=>({format:'paperref-history',version:1,entries:structuredClone(records)}),
    importJSON:async data=>{if(!await ready)return;const file=new File([JSON.stringify(data)],'history.json',{type:'application/json'});return document.getElementById('history-import').onchange({target:{files:[file],value:''}});},
    onRestore:handler=>{onRestore=handler;}};
})();
