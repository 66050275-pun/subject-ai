/* Device-local result snapshots. Only explicitly allowed metadata is persisted. */
(() => {
  const MAX_RECORDS=50,MAX_TOTAL=8*1024*1024,MAX_RECORD=1024*1024;
  const list=document.getElementById('history-list'),status=document.getElementById('history-status'),toggle=document.getElementById('history-enabled');
  const bytes=value=>new Blob([JSON.stringify(value)]).size;
  const text=(value,max=1200)=>typeof value==='string'?value.slice(0,max):'';
  const url=value=>{try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch(_){return '';}};
  const paper=value=>{
    if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('ข้อมูลรายการอ้างอิงไม่ถูกต้อง');
    const out={};
    for(const key of ['title','matched_title','doi','year','original_text','access_label','access_detail'])out[key]=text(value[key],key==='original_text'?4000:1200);
    for(const key of ['paper_url','oa_pdf_url','scholar_url'])out[key]=url(value[key]);
    out.reference_number=Number.isSafeInteger(value.reference_number)&&value.reference_number>0?value.reference_number:1;
    out.citation_mentions=Number.isSafeInteger(value.citation_mentions)?Math.max(0,value.citation_mentions):0;
    out.access_status=['pdf_available','open_source_record','scholar_search'].includes(value.access_status)?value.access_status:'scholar_search';
    out.metadata_sources=Array.isArray(value.metadata_sources)?value.metadata_sources.slice(0,20).map(v=>text(v,80)):[];
    out.source_links=Array.isArray(value.source_links)?value.source_links.slice(0,20).map(v=>({url:url(v?.url),source:text(v?.source,80),label:text(v?.label,100)})).filter(v=>v.url):[];
    out.pdf_locations=Array.isArray(value.pdf_locations)?value.pdf_locations.slice(0,20).map(v=>({url:url(v?.url),landing_url:url(v?.landing_url),source:text(v?.source,80),version:text(v?.version,80)})).filter(v=>v.url):[];
    out.citation_contexts=[]; // Body excerpts, AI answers and chat are not saved.
    return out;
  };
  const snapshot=(payload,kind)=>{
    if(!['code','ai','doi'].includes(kind)||!payload||!Array.isArray(payload.results)||payload.results.length>1000)throw new Error('รูปแบบประวัติไม่ถูกต้อง');
    if(payload.citing_papers&&!Array.isArray(payload.citing_papers))throw new Error('รูปแบบ citation ไม่ถูกต้อง');
    if((payload.citing_papers||[]).length>1000)throw new Error('รายการ citation มากเกินไป');
    const out={filename:text(payload.filename,500),mode:kind==='doi'?'doi':'pdf',results:payload.results.map(paper),citing_papers:(payload.citing_papers||[]).map(paper),source_paper:payload.source_paper?paper(payload.source_paper):null};
    out.total_references=out.results.length;
    for(const key of ['citation_link_count','cited_reference_count'])out[key]=Number.isSafeInteger(payload[key])?Math.max(0,payload[key]):0;
    for(const key of ['citation_links_available','references_truncated','citations_truncated'])out[key]=payload[key]===true;
    if(bytes(out)>MAX_RECORD)throw new Error('ผลชุดนี้ใหญ่เกิน 1 MB จึงไม่ได้บันทึกประวัติ ใช้ export ผลรายการแทนได้');
    return out;
  };
  const message=error=>{status.textContent=error?.name==='QuotaExceededError'?'พื้นที่เก็บข้อมูลเต็ม ประวัติเดิมยังอยู่ ลบหรือ export ประวัติก่อน':(error instanceof Error?error.message:String(error));};
  let db=null,enabled=true,records=[],onRestore=null;
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
        tx.oncomplete=()=>{toggle.disabled=false;toggle.checked=enabled;render();resolve(true);};
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
    if(!await ready||!enabled)return null;
    try{
      const record={id:id||crypto.randomUUID(),kind,savedAt:new Date().toISOString(),payload:snapshot(payload,kind)};
      let written=true;
      await commit(rows=>{if(id&&!rows.some(r=>r.id===id)){written=false;return rows;}return [record,...rows.filter(r=>r.id!==record.id)];});
      if(!written)return null;status.textContent='บันทึกประวัติในเบราว์เซอร์นี้แล้ว';return record.id;
    }catch(error){message(error);return null;}
  };
  function render(){
    list.replaceChildren();document.getElementById('history-count').textContent=`${records.length}/50 รายการ · ${(bytes(records)/1024/1024).toFixed(2)} MB`;
    document.getElementById('history-export').disabled=!records.length;document.getElementById('history-clear').disabled=!records.length;
    if(!records.length){const p=document.createElement('p');p.className='history-empty';p.textContent='ยังไม่มีประวัติ ค้นหาจาก PDF หรือ DOI เพื่อเริ่มบันทึก';list.append(p);return;}
    for(const record of [...records].sort((a,b)=>b.savedAt.localeCompare(a.savedAt))){
      const card=document.createElement('article');card.className='history-card';
      const title=document.createElement('h4');title.textContent=record.payload.filename||record.payload.source_paper?.doi||'งานวิจัย';
      const meta=document.createElement('p');meta.textContent=`${{code:'สกัดด้วยโค้ด',ai:'สกัดด้วย AI',doi:'ค้น DOI'}[record.kind]} · ${record.payload.results.length} อ้างอิง · ${new Date(record.savedAt).toLocaleString('th-TH')}`;
      const actions=document.createElement('div');const open=document.createElement('button');open.type='button';open.textContent='เปิดผลเดิม →';open.className='text-button';open.onclick=()=>{if(onRestore?.(structuredClone(record.payload),record.kind,record.id)!==false)window.PaperRefSettings.close();};
      const remove=document.createElement('button');remove.type='button';remove.textContent='ลบ';remove.setAttribute('aria-label','ลบประวัติ '+title.textContent);remove.onclick=async()=>{try{await commit(rows=>rows.filter(r=>r.id!==record.id));status.textContent='ลบรายการแล้ว';}catch(error){message(error);}};
      actions.append(open,remove);card.append(title,meta,actions);list.append(card);
    }
  }
  toggle.onchange=async()=>{
    if(!await ready)return;const requested=toggle.checked;toggle.disabled=true;
    try{await new Promise((resolve,reject)=>{const tx=db.transaction('preferences','readwrite');tx.objectStore('preferences').put(requested,'enabled');tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});enabled=requested;status.textContent=enabled?'เปิดบันทึกประวัติแล้ว':'หยุดบันทึกใหม่แล้ว ประวัติเดิมยังอยู่ ลบได้ด้วยปุ่มล้างประวัติ';}
    catch(error){toggle.checked=enabled;message(error);}finally{toggle.disabled=false;}
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
  window.PaperRefHistory={save,onRestore:handler=>{onRestore=handler;}};
})();
