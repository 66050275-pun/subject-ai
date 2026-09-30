"""Portable interactive SVG component using the existing graph renderer."""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def graph_html(items, source_name, themes=None):
    payload = json.dumps({'items': items, 'source': source_name, 'themes': themes or {}},
                         ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026')
    script = (BASE_DIR / 'static' / 'graph.js').read_text()
    return '''<!doctype html><html lang="th"><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{margin:0;font:14px system-ui,sans-serif;color:#193a2b;background:#f5f8f4}
    button{padding:8px 12px;background:white;border:1px solid #cadace;border-radius:8px;cursor:pointer}
    .tools{display:flex;flex-wrap:wrap;gap:6px;padding:10px}svg{width:100%;height:400px;touch-action:none}
    .hidden{display:none!important}#network-legend{display:flex;flex-wrap:wrap;gap:8px;padding:8px}
    .graph-legend-chip{display:flex;align-items:center;gap:5px;font-size:12px}.graph-legend-chip i{width:9px;height:9px;border-radius:50%}
    #graph-inspector{padding:12px;background:white;border:1px solid #cadace;border-radius:10px}
    #graph-paper-title{font-weight:700}#graph-paper-meta{font-size:12px;margin:8px 0}
    #graph-select-paper{display:none}</style></head><body>
    <div class="tools"><button id="graph-fit">Fit</button><button id="graph-reset">Reset</button>
    <button id="graph-zoom-in">＋</button><button id="graph-zoom-out">－</button><span id="graph-zoom-label"></span></div>
    <div id="network-legend"></div><svg id="network-svg" aria-label="เครือข่ายอ้างอิง"></svg>
    <div id="graph-inspector" class="hidden"><div id="graph-paper-title"></div><div id="graph-paper-meta"></div>
    <button id="graph-open-paper">เปิดเปเปอร์ ↗</button><button id="graph-select-paper"></button></div>
    <script>''' + script + '''</script><script>
    const data = ''' + payload + ''';
    PaperRefGraph.render({svg:document.getElementById('network-svg'),items:data.items,sourceName:data.source,
      relationText:'References',themes:new Map(Object.entries(data.themes).map(([k,v])=>[Number(k),v])),
      onOpen:item=>{const url=item.oa_pdf_url||item.paper_url||item.scholar_url;
        if(url&&(url.startsWith('https://')||url.startsWith('http://')))window.open(url,'_blank','noopener,noreferrer');},
      onSelect:()=>{},isSelected:()=>false,allowSelection:false});
    document.getElementById('graph-fit').click();
    </script></body></html>'''
