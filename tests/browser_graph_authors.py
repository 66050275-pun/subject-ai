"""Reference graph authors, safe text, interaction and saved coordinates."""
import functools
import http.server
from pathlib import Path
import shutil
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(
    http.server.SimpleHTTPRequestHandler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which('chromium'),
                                            headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content('''<!doctype html><html><head><style>
          body{font-family:Arial,sans-serif}.hidden{display:none}svg{width:1180px;height:700px}
          </style></head><body><div id="network-legend"></div>
          <svg id="citation-graph"></svg><span id="graph-zoom-label"></span>
          <button id="graph-zoom-in">+</button><button id="graph-zoom-out">-</button>
          <button id="graph-fit">Fit</button><button id="graph-reset">Reset</button>
          <div id="graph-inspector" class="hidden"><h4 id="graph-paper-title"></h4>
          <p id="graph-paper-meta"></p><button id="graph-open-paper">Open</button>
          <button id="graph-select-paper">Select</button></div></body></html>''')
        page.add_script_tag(url=f'http://127.0.0.1:{server.server_port}/static/graph.js')
        page.evaluate('''() => {
          window.authorItems = [
            {reference_number:1, title:'A long battery research title that wraps', year:'2025',
             authors:['Ada Lovelace','Charles Babbage','Very Long Author Name'], access_status:'scholar_search'},
            {reference_number:2, title:'Missing authors', year:'2024', access_status:'scholar_search'},
            {reference_number:3, title:'Literal text only', year:'2023',
             authors:[null,{name:'ignore'},'<img src=x onerror=alert(1)>','Grace Hopper'], access_status:'scholar_search'}
          ];
          window.renderAuthors = state => window.PaperRefGraph.render({
            svg:document.getElementById('citation-graph'),items:window.authorItems,sourceName:'Test paper',
            relationText:'References',themes:new Map([[1,{name:'AI methods',color:'#7c3aed'}],[2,{name:'AI methods',color:'#7c3aed'}],[3,{name:'AI context',color:'#b69adf'}]]),
            onOpen:()=>{},onSelect:()=>{},isSelected:()=>false,allowSelection:true,state
          });
          window.renderAuthors({zoom:1,offsetX:0,offsetY:0,nodes:[{id:'paper:1',x:300,y:350}]});
        }''')
        papers = page.locator('[data-graph-node=paper]')
        assert papers.count() == 3
        assert page.locator('.graph-paper-authors').count() == 3
        assert papers.nth(0).locator('.graph-paper-authors').text_content().startswith('Ada Lovelace')
        assert papers.nth(0).locator('.graph-paper-authors').get_attribute('fill') == '#89947e'
        assert papers.nth(0).locator('.graph-paper-authors').get_attribute('font-size') == '9'
        tip = papers.nth(0).locator('title').text_content()
        assert 'Charles Babbage' in tip and 'Very Long Author Name' in tip
        assert papers.nth(1).locator('.graph-paper-authors').text_content() == 'ยังไม่พบชื่อผู้แต่ง'
        assert '<img src=x onerror=alert(1)>' in papers.nth(2).locator('title').text_content()
        assert 'Grace Hopper' in papers.nth(2).get_attribute('aria-label')
        assert '[object Object]' not in page.locator('svg').text_content()
        assert page.locator('img').count() == 0
        assert page.evaluate('''() => [...document.querySelectorAll('[data-graph-node=paper]')].every(node => {
          const lines=[...node.querySelectorAll('text')].filter(el=>el.getAttribute('x')==='-72');
          return lines.every((line,index)=>index===0||line.getBBox().y>=lines[index-1].getBBox().y+lines[index-1].getBBox().height);
        })''')
        papers.nth(0).focus()
        page.keyboard.press('Enter')
        assert 'Ada Lovelace, Charles Babbage, Very Long Author Name' in page.locator('#graph-paper-meta').inner_text()
        initial = page.evaluate("window.PaperRefGraph.exportState(document.getElementById('citation-graph'))")
        assert next(row for row in initial['nodes'] if row['id'] == 'paper:1')['x'] == 300
        box = papers.nth(0).locator('rect').first.bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 55, y + 26, steps=4)
        page.mouse.up()
        moved = page.evaluate("window.PaperRefGraph.exportState(document.getElementById('citation-graph'))")
        assert next(row for row in moved['nodes'] if row['id'] == 'paper:1')['x'] > 300
        page.locator('#graph-zoom-in').click()
        assert page.locator('#graph-zoom-label').inner_text() == '120%'
        saved = page.evaluate("window.PaperRefGraph.exportState(document.getElementById('citation-graph'))")
        page.evaluate('state=>window.renderAuthors(state)', saved)
        restored = page.evaluate("window.PaperRefGraph.exportState(document.getElementById('citation-graph'))")
        assert restored == saved
        assert page.locator('.graph-paper-authors').count() == 3
        assert not errors, errors
        browser.close()
    print('PASS: small grey author labels in AI-themed nodes, full-name tooltip/inspector, missing authors, safe text, no vertical overlap, dragging, zoom and saved graph coordinates')
finally:
    server.shutdown()
