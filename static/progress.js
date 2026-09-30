/* Honest progress: counts from streaming APIs, indeterminate for other requests. */
(() => {
  const get=id=>{
    const host=document.getElementById(id);
    if(!host.firstElementChild){
      const caption=document.createElement('div');caption.className='operation-caption';
      const label=document.createElement('span');label.className='operation-label';label.setAttribute('role','status');
      const count=document.createElement('span');count.className='operation-count';
      caption.append(label,count);
      const track=document.createElement('div');track.className='operation-track';track.setAttribute('role','progressbar');
      const fill=document.createElement('div');fill.className='operation-fill';track.append(fill);host.append(caption,track);
    }
    return host;
  };
  const update=(id,message,completed=null,total=null)=>{
    const host=get(id),track=host.querySelector('.operation-track'),fill=host.querySelector('.operation-fill');
    host.classList.remove('hidden');host.setAttribute('aria-busy','true');host.querySelector('.operation-label').textContent=message;
    track.setAttribute('aria-label',message);
    const measured=Number.isFinite(completed)&&Number.isFinite(total)&&total>0;
    host.classList.toggle('is-indeterminate',!measured);
    if(measured){
      const done=Math.max(0,Math.min(total,completed)),percent=Math.round(done/total*100);
      fill.style.width=percent+'%';track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.setAttribute('aria-valuenow',String(percent));track.setAttribute('aria-valuetext',`${done}/${total}`);
      host.querySelector('.operation-count').textContent=`${done}/${total} · ${percent}%`;
    }else{
      fill.style.width='';for(const attr of ['aria-valuemin','aria-valuemax','aria-valuenow','aria-valuetext'])track.removeAttribute(attr);
      host.querySelector('.operation-count').textContent='กำลังทำงาน';
    }
  };
  window.PaperRefProgress={start:(id,message)=>update(id,message),update,
    finish:id=>{const host=document.getElementById(id);host.classList.add('hidden');host.setAttribute('aria-busy','false');}};
})();
