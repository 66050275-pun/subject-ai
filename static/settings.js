/* Settings drawer. Optional preferences never include credentials or paper data. */
(() => {
  const dialog=document.getElementById('settings-drawer');
  const key=document.getElementById('openai-key');
  const storageKey='paperref.ai-preferences.v1';
  const remember=document.getElementById('remember-ai-preferences');
  const status=document.getElementById('preferences-status');
  let opener=null, closing=false, previousOverflow='';
  const select=section=>{
    for(const button of dialog.querySelectorAll('[data-settings-section]'))button.setAttribute('aria-pressed',String(button.dataset.settingsSection===section));
    for(const name of ['ai','workspace','history','privacy'])document.getElementById('settings-'+name).classList.toggle('hidden',name!==section);
    dialog.querySelector('.drawer-scroll').scrollTop=0;
  };
  const open=(section='ai',trigger=document.activeElement)=>{
    if(closing)return;
    select(section);opener=trigger;
    if(!dialog.open){previousOverflow=document.body.style.overflow;document.body.style.overflow='hidden';dialog.showModal();}
    for(const button of document.querySelectorAll('[data-open-settings]'))button.setAttribute('aria-expanded','true');
    document.getElementById('settings-close').focus();
  };
  const finish=()=>{
    dialog.close();dialog.classList.remove('is-closing');closing=false;
    document.body.style.overflow=previousOverflow;
    for(const button of document.querySelectorAll('[data-open-settings]'))button.setAttribute('aria-expanded','false');
    opener?.focus();
  };
  const close=()=>{
    if(!dialog.open||closing)return;hideKey();closing=true;dialog.classList.add('is-closing');
    if(matchMedia('(prefers-reduced-motion: reduce)').matches)finish();else setTimeout(finish,180);
  };
  window.PaperRefSettings={open,close};
  for(const trigger of document.querySelectorAll('[data-open-settings],a[href="#ai-settings"]'))trigger.addEventListener('click',event=>{event.preventDefault();open(trigger.dataset.openSettings||'ai',trigger);});
  for(const button of dialog.querySelectorAll('[data-settings-section]'))button.onclick=()=>select(button.dataset.settingsSection);
  document.getElementById('settings-close').onclick=close;
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab'||event.ctrlKey||event.metaKey||event.altKey)return;
    const items=[...dialog.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')].filter(el=>!el.disabled&&el.tabIndex>=0&&el.getClientRects().length);
    const first=items[0],last=items[items.length-1];
    if(!first){event.preventDefault();return;}
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  });
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)close();}});
  for(const button of dialog.querySelectorAll('[data-jump]'))button.onclick=()=>{
    const target=document.getElementById(button.dataset.jump);
    if(target.classList.contains('hidden')){button.querySelector('span').textContent='เริ่มค้นหา PDF หรือ DOI ก่อน เพื่อดูผล';return;}
    close();setTimeout(()=>target.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'}),200);
  };
  const hideKey=()=>{key.type='password';const button=document.getElementById('toggle-key');button.textContent='แสดงคีย์';button.setAttribute('aria-pressed','false');};
  document.getElementById('toggle-key').onclick=()=>{const show=key.type==='password';key.type=show?'text':'password';document.getElementById('toggle-key').textContent=show?'ซ่อนคีย์':'แสดงคีย์';document.getElementById('toggle-key').setAttribute('aria-pressed',String(show));};
  dialog.addEventListener('close',hideKey);
  document.getElementById('ai-provider').addEventListener('change',hideKey);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)hideKey();});
  for(const button of document.querySelectorAll('[data-clear-key]'))button.onclick=()=>{key.value='';hideKey();document.getElementById('key-clear-status').textContent='ล้างคีย์ในหน้านี้แล้ว คำขอที่เริ่มส่งไปแล้วอาจยังประมวลผลอยู่';};
  document.getElementById('clear-key').addEventListener('click',hideKey);
  const save=()=>{
    if(!remember.checked)return;
    try{localStorage.setItem(storageKey,JSON.stringify({provider:document.getElementById('ai-provider').value,model:document.getElementById('summary-model').value.slice(0,100)}));status.textContent='จำเฉพาะ provider และโมเดลแล้ว';}
    catch(_){remember.checked=false;status.textContent='เบราว์เซอร์ไม่อนุญาตให้จำการตั้งค่า';}
  };
  remember.onchange=()=>{if(remember.checked)save();else{try{localStorage.removeItem(storageKey);status.textContent='ลบการตั้งค่าที่จำไว้แล้ว';}catch(_){status.textContent='ลบไม่ได้ โปรดล้างข้อมูลเว็บไซต์ในเบราว์เซอร์';}}};
  for(const id of ['ai-provider','summary-model','gemini-model-picker','maxplus-model-picker'])document.getElementById(id).addEventListener('change',()=>setTimeout(save,0));
  const restore=()=>{
    try{const prefs=JSON.parse(localStorage.getItem(storageKey)||'null');
      if(prefs&&['openai','gemini','claude','maxplus'].includes(prefs.provider)&&typeof prefs.model==='string'&&/^[A-Za-z0-9._:/-]{0,100}$/.test(prefs.model)){
        const provider=document.getElementById('ai-provider');provider.value=prefs.provider;provider.dispatchEvent(new Event('change'));document.getElementById('summary-model').value=prefs.model;remember.checked=true;status.textContent='ใช้การตั้งค่าที่คุณเลือกให้จำไว้';
      }
    }catch(_){/* Disabled or invalid storage must not block the workspace. */}
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',restore,{once:true});else setTimeout(restore,0);
})();
