/* Optional AI pacing and result reuse. Credentials and PDFs never enter this cache. */
(() => {
  const mode=document.getElementById('ai-economy-mode'), interval=document.getElementById('ai-economy-interval');
  const cache=new Map(), pending=new WeakMap(), reused=new WeakSet();
  const MAX_ENTRIES=12,MAX_BYTES=2*1024*1024,TTL=30*60*1000;
  let generation=0,busy=false,lastFinished=null,totalBytes=0;
  const config=()=>({enabled:mode.checked,interval:[30,60,120].includes(Number(interval.value))?Number(interval.value):30});
  const invalidate=()=>{
    generation++;cache.clear();totalBytes=0;
    for(const id of ['ai-economy-usage','extraction-economy-usage','workspace-economy-usage']){
      const host=document.getElementById(id);if(host){host.textContent='';host.classList.add('hidden');}
    }
  };
  const sync=()=>{
    const profile=config(); interval.disabled=!profile.enabled;
    for(const host of document.querySelectorAll('[data-ai-budget-mode]'))host.textContent=profile.enabled
      ? `AI: โหมดประหยัด · พักอย่างน้อย ${profile.interval} วินาทีระหว่างคำขอ`
      : 'AI: โหมดปกติ';
  };
  const append=(form,profile=config())=>{
    form.append('economy_mode',String(profile.enabled));
    if(profile.enabled)form.append('economy_interval',String(profile.interval));
    return profile;
  };
  const fingerprint=async descriptor=>{
    if(!descriptor||!window.crypto?.subtle)return null;
    const encoded=new TextEncoder().encode(JSON.stringify(descriptor));
    const digest=await crypto.subtle.digest('SHA-256',encoded);
    return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
  };
  const clone=value=>JSON.parse(JSON.stringify(value,(key,item)=>
    /^(api[_-]?key|x-ai-api-key|x-openai-api-key|authorization|headers)$/i.test(key)?undefined:item));
  const remove=key=>{const old=cache.get(key);if(old){totalBytes-=old.bytes;cache.delete(key);}};
  const run=async(request,{profile=config(),descriptor=null,onWait=()=>{},isCurrent=()=>true,reuse=true}={})=>{
    if(busy)throw new Error('รอคำขอ AI ปัจจุบันเสร็จก่อน ไม่มีการเข้าคิวหรือเรียกซ้ำอัตโนมัติ');
    busy=true;const revision=generation;let sent=false;
    try{
      const signature=profile.enabled&&reuse?await fingerprint({generation:revision,...descriptor}):null;
      if(revision!==generation||!isCurrent())throw new Error('การตั้งค่าหรืองานต้นทางเปลี่ยนแล้ว กรุณากดใช้ AI กับชุดปัจจุบัน');
      const previous=signature&&cache.get(signature);
      if(previous&&Date.now()-previous.created<=TTL){const value=clone(previous.value);reused.add(value);return value;}
      if(previous)remove(signature);
      if(profile.enabled&&lastFinished!==null){
        let remaining=lastFinished+profile.interval*1000-Date.now();
        while(remaining>0){
          if(revision!==generation||!isCurrent())throw new Error('ยกเลิกคำขอที่รอพัก เพราะการตั้งค่าหรืองานต้นทางเปลี่ยนแล้ว');
          onWait(Math.ceil(remaining/1000));
          await new Promise(resolve=>setTimeout(resolve,Math.min(1000,remaining)));
          remaining=lastFinished+profile.interval*1000-Date.now();
        }
      }
      if(revision!==generation||!isCurrent())throw new Error('ยกเลิกคำขอที่รอพัก เพราะการตั้งค่าหรืองานต้นทางเปลี่ยนแล้ว');
      sent=true;const value=await request();
      if(signature&&value&&typeof value==='object')pending.set(value,{signature,revision,profile});
      return value;
    }finally{if(sent)lastFinished=Date.now();busy=false;}
  };
  const remember=value=>{
    const ticket=pending.get(value);pending.delete(value);
    if(!ticket||ticket.revision!==generation||!ticket.profile.enabled||value.extraction_complete===false
      ||value.failed_clustering_batches?.length||value.failed_extraction_batches?.length||value.recovered_reference_numbers?.length||value.unassigned_ids?.length)return;
    try{
      const saved=clone(value),bytes=new Blob([JSON.stringify(saved)]).size;
      if(bytes>MAX_BYTES)return;
      remove(ticket.signature);
      while(cache.size>=MAX_ENTRIES||totalBytes+bytes>MAX_BYTES)remove(cache.keys().next().value);
      cache.set(ticket.signature,{value:saved,bytes,created:Date.now()});totalBytes+=bytes;
    }catch(_){/* Result display must work even when a response cannot be cached. */}
  };
  const report=(value,id='ai-economy-usage')=>{
    const host=document.getElementById(id);if(!host)return;
    const economy=value?.economy;
    if(!economy?.enabled){host.textContent='';host.classList.add('hidden');return;}
    const requests=Number.isSafeInteger(economy.requests)?economy.requests:null;
    const characters=Number.isSafeInteger(economy.prompt_characters)?economy.prompt_characters:null;
    const seconds=Number.isSafeInteger(economy.interval_seconds)?economy.interval_seconds:null;
    const facts=[requests!==null?`${requests} คำขอ AI`:null,characters!==null?`ส่งข้อความรวม ${characters.toLocaleString()} อักขระ`:null,seconds!==null?`พักอย่างน้อย ${seconds} วินาที`:null].filter(Boolean);
    host.textContent=(reused.has(value)?'ใช้ผลเดิมในแท็บนี้ · ไม่เรียก AI เพิ่ม · ผลเดิม: ':'โหมดประหยัด · ')+facts.join(' · ')
      +(typeof economy.context_notice==='string'?' · '+economy.context_notice.slice(0,500):'');
    host.classList.remove('hidden');
  };
  for(const id of ['ai-provider','summary-model','maxplus-base-url','alibaba-region','gemini-model-picker','maxplus-model-picker','alibaba-model-picker','openai-key','ai-economy-mode','ai-economy-interval']){
    const input=document.getElementById(id);input?.addEventListener('input',invalidate);input?.addEventListener('change',()=>{invalidate();sync();});
  }
  for(const button of document.querySelectorAll('#clear-key,[data-clear-key]'))button.addEventListener('click',invalidate);
  sync();
  window.PaperRefAIBudget={config,append,run,remember,report,invalidate};
})();
