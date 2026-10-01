/* Pure local bibliography operations, also testable with Node. */
(() => {
  const doi=value=>String(value||'').trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i,'').replace(/^doi:\s*/i,'').toLowerCase();
  const title=p=>String(p.matched_title||p.title||'').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
  const uniqueSessions=sessions=>[...new Map(sessions.map(s=>{const p=s.payload.source_paper||{};const d=doi(p.doi),t=title(p)||String(s.payload.filename||'').normalize('NFKC').toLowerCase().trim();return [d?'doi:'+d:'title:'+t,s];})).values()];
  function sharedReferences(sessions){
    sessions=uniqueSessions(sessions);
    const groups=new Map(),byTitle=new Map();
    for(const s of sessions)for(const p of s.payload.results){const t=title(p),d=doi(p.doi);if(d&&t.length>=15){if(!byTitle.has(t))byTitle.set(t,new Set());byTitle.get(t).add(d);}}
    for(const s of sessions){const seen=new Set();for(const p of s.payload.results){
      const d=doi(p.doi),t=title(p),known=byTitle.get(t);
      const key=d?'doi:'+d:t.length>=15?(known?.size===1?'doi:'+Array.from(known)[0]:'title:'+t):null;
      if(!key||seen.has(key))continue;seen.add(key);
      if(!groups.has(key))groups.set(key,{key,paper:p,sessions:[],match:key.startsWith('doi:')?'DOI':'ชื่อเรื่องตรงกัน (ควรตรวจสอบ)'});
      const g=groups.get(key);if(d&&!g.paper.doi)g.paper=p;g.sessions.push({id:s.id,title:s.payload.filename});
    }}
    return [...groups.values()].filter(g=>g.sessions.length>=2).sort((a,b)=>b.sessions.length-a.sessions.length);
  }
  const escape=value=>String(value||'').replace(/[\x00-\x1f]/g,' ').replace(/[\\{}%&#_$~^]/g,c=>({'\\':'\\textbackslash{}','{':'\\{','}':'\\}','%':'\\%','&':'\\&','#':'\\#','_':'\\_','$':'\\$','~':'\\textasciitilde{}','^':'\\textasciicircum{}'}[c]));
  function bibtex(sessions){
    const records=new Map(),known=new Map();let fallback=0;
    for(const s of sessions)for(const p of s.payload.results){const t=title(p),d=doi(p.doi);if(d&&t.length>=15){if(!known.has(t))known.set(t,new Set());known.get(t).add(d);}}
    for(const s of sessions)for(const p of s.payload.results){const d=doi(p.doi),t=title(p),ids=known.get(t);const key=d?'doi:'+d:t.length>=15?(ids?.size===1?'doi:'+Array.from(ids)[0]:'title:'+t):'unique:'+fallback++;if(!records.has(key)||(!records.get(key).doi&&d))records.set(key,p);}
    return [...records.values()].map((p,i)=>{
      const fields={title:p.matched_title||p.title||p.original_text,author:(p.authors||[]).join(' and '),year:p.year,doi:doi(p.doi),url:p.paper_url||p.oa_pdf_url,journal:p.journal};
      return (p.journal?'@article{paperref':'@misc{paperref')+(i+1)+',\n'+Object.entries(fields).filter(([,v])=>v).map(([k,v])=>'  '+k+' = {'+escape(v)+'}').join(',\n')+'\n}';
    }).join('\n\n')+'\n';
  }
  const api={sharedReferences,bibtex,doi,uniqueSessions};if(typeof module!=='undefined')module.exports=api;else window.PaperRefWorkspace=api;
})();
