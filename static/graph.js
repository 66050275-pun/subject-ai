/* SVG graph: source above grouped reference cards; all text comes from metadata. */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const make = (tag, attrs = {}) => {
    const el = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
    return el;
  };
  const text = (value, x, y, attrs = {}) => {
    const el = make('text', {x, y, ...attrs}); el.textContent = value; return el;
  };
  const short = (value, count) => Array.from(String(value || '')).slice(0, count).join('');
  const titleLines = value => {
    const cut = str => {
      if (Array.from(str).length <= 27) return [str, ''];
      const head = short(str, 27), boundary = head.lastIndexOf(' ');
      const count = boundary > 12 ? boundary : head.length;
      return [str.slice(0, count).trim(), str.slice(count).trim()];
    };
    const [first, tail] = cut(value), [second, rest] = cut(tail);
    return [first, second + (rest ? '…' : '')];
  };
  window.PaperRefGraph = {
    exportState: svg=>svg?.paperrefState?.()||null,
    render({svg, items, sourceName, relationText, themes, onOpen, onSelect, isSelected, allowSelection, state, onChange}) {
      const grouped = new Map();
      const access = {
        pdf_available: {name: 'ดาวน์โหลด PDF ได้', color: '#628646'},
        open_source_record: {name: 'พบในฐานข้อมูลเปิด', color: '#8b6ab0'},
        scholar_search: {name: 'ค้นต่อใน Google Scholar', color: '#b08b55'}
      };
      for (const item of items) {
        const theme = themes.get(item.reference_number);
        const group = theme || access[item.access_status] || access.scholar_search;
        const key = (theme ? 'theme:' : 'access:') + group.name;
        if (!grouped.has(key)) grouped.set(key, {...group, items: []});
        grouped.get(key).items.push(item);
      }
      const groups = [...grouped.values()];
      const groupSpacing = groups.length === 2 ? 560 : 320;
      const columns = groups.length <= 1 ? 3 : groups.length === 2 ? 2 : 1;
      const width = Math.max(1180, groups.length * groupSpacing + 80), height = 700;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.replaceChildren();
      const world = make('g', {'data-graph-world': 'true'});
      const edges = make('g', {fill: 'none', 'stroke-linecap': 'round'});
      const nodeLayer = make('g'); world.append(edges, nodeLayer); svg.append(world);
      const nodes = [];
      const root = {kind: 'root', x: width / 2, y: 78, width: 350, height: 92, color: '#193a2b'};
      nodes.push(root);
      groups.forEach((group, index) => {
        const hub = {kind: 'group', x: width / 2 + (index - (groups.length - 1) / 2) * groupSpacing,
          y: 215, width: 260, height: 64, color: group.color, group, parent: root};
        nodes.push(hub);
        group.items.forEach((item, row) => nodes.push({kind: 'paper', x: hub.x + (row % columns - (columns - 1) / 2) * 270, y: 340 + Math.floor(row / columns) * 100,
          width: 248, height: 80, color: group.color, item, parent: hub}));
      });
      const nodeId=n=>n.kind==='paper'?'paper:'+n.item.reference_number:n.kind==='group'?'group:'+n.group.name:'root';
      for(const node of nodes){const saved=state?.nodes?.find(s=>s.id===nodeId(node));if(saved&&Number.isFinite(saved.x)&&Number.isFinite(saved.y)){node.x=saved.x;node.y=saved.y;}}
      const legend = document.getElementById('network-legend');
      legend.replaceChildren(); legend.classList.remove('hidden');
      groups.forEach(group => {
        const label = document.createElement('span'); label.className = 'graph-legend-chip';
        const dot = document.createElement('i'); dot.style.background = group.color;
        const name = document.createElement('span'); name.textContent = group.name + ' · ' + group.items.length;
        label.append(dot, name); legend.append(label);
      });
      document.getElementById('graph-inspector').classList.add('hidden');
      let zoom = 1, offsetX = 0, offsetY = 0, active = null, interaction = null;
      if(state){zoom=state.zoom||1;offsetX=state.offsetX||0;offsetY=state.offsetY||0;}
      svg.paperrefState=()=>({zoom,offsetX,offsetY,nodes:nodes.map(n=>({id:nodeId(n),x:n.x,y:n.y}))});
      const update = () => {
        world.setAttribute('transform', `translate(${offsetX} ${offsetY}) scale(${zoom})`);
        for (const node of nodes) {
          node.el.setAttribute('transform', `translate(${node.x} ${node.y})`);
          if (node.parent) {
            const startX = node.parent.x, startY = node.parent.y + node.parent.height / 2;
            const endY = node.y - node.height / 2;
            const bend = startY + (endY - startY) * .55;
            node.edge.setAttribute('d', `M ${startX} ${startY} C ${startX} ${bend}, ${node.x} ${bend}, ${node.x} ${endY}`);
          }
        }
        document.getElementById('graph-zoom-label').textContent = Math.round(zoom * 100) + '%';
      };
      const inspect = node => {
        if (active) active.body.setAttribute('stroke-width', active.item && isSelected(active.item) ? '2.5' : '1');
        active = node; node.body.setAttribute('stroke-width', '3');
        if (!node.item) { document.getElementById('graph-inspector').classList.add('hidden'); return; }
        const item = node.item;
        document.getElementById('graph-inspector').classList.remove('hidden');
        document.getElementById('graph-paper-title').textContent = item.matched_title || item.title || item.original_text;
        document.getElementById('graph-paper-meta').textContent = `Reference ${item.reference_number} · ${item.year || 'ไม่พบปี'} · ${item.access_label || 'ยังไม่พบลิงก์ PDF'}`;
        document.getElementById('graph-open-paper').onclick = () => onOpen(item);
        const select = document.getElementById('graph-select-paper');
        select.disabled = !allowSelection;
        const setLabel = () => { select.textContent = isSelected(item) ? '✓ เลือกไว้แล้ว · ยกเลิก' : '+ เลือกเพื่อสังเคราะห์'; };
        setLabel();
        select.onclick = () => {
          onSelect(item); setLabel();
          node.body.setAttribute('stroke', isSelected(item) ? '#7457aa' : node.color);
        };
      };
      for (const node of nodes) {
        const el = make('g', {'data-graph-node': node.kind, tabindex: '0', role: 'button',
          'aria-label': node.kind === 'root' ? 'งานต้นทาง: ' + sourceName : node.kind === 'group'
            ? node.group.name + ': ' + node.group.items.length + ' รายการ'
            : 'Reference ' + node.item.reference_number + ': ' + (node.item.matched_title || node.item.title || node.item.original_text),
          style: 'cursor:grab'});
        node.el = el;
        const body = make('rect', {x: -node.width / 2, y: -node.height / 2, width: node.width,
          height: node.height, rx: node.kind === 'root' ? 20 : 14,
          fill: node.kind === 'root' ? '#193a2b' : node.kind === 'group' ? node.color + '18' : '#ffffff',
          stroke: node.item && isSelected(node.item) ? '#7457aa' : node.color,
          'stroke-width': node.item && isSelected(node.item) ? 2.5 : 1});
        node.body = body; el.append(body);
        const tip = make('title'); tip.textContent = el.getAttribute('aria-label'); el.append(tip);
        if (node.kind === 'root') {
          el.append(text('YOUR PAPER / ' + relationText, 0, -22, {'text-anchor': 'middle', fill: '#d5edaa', 'font-size': 10, 'letter-spacing': 1.3}));
          el.append(text(short(sourceName, 38) + (sourceName.length > 38 ? '…' : ''), 0, 3,
            {'text-anchor': 'middle', fill: '#ffffff', 'font-size': 15, 'font-weight': 600}));
          el.append(text(`${items.length} papers · ${groups.length} groups`, 0, 27,
            {'text-anchor': 'middle', fill: '#b9cbb1', 'font-size': 11}));
        } else if (node.kind === 'group') {
          el.append(text(short(node.group.name, 28), 0, -3, {'text-anchor': 'middle', fill: node.color, 'font-size': 12, 'font-weight': 600}));
          el.append(text(node.group.items.length + ' papers', 0, 17, {'text-anchor': 'middle', fill: '#788370', 'font-size': 10}));
        } else {
          el.append(make('rect', {x: -111, y: -26, width: 29, height: 29, rx: 8, fill: node.color + '18'}));
          el.append(text(node.item.reference_number, -96, -7, {'text-anchor': 'middle', fill: node.color, 'font-size': 10, 'font-weight': 700}));
          const title = String(node.item.matched_title || node.item.title || node.item.original_text || 'ไม่พบชื่อเรื่อง');
          const [firstLine, secondLine] = titleLines(title);
          el.append(text(firstLine, -72, -15, {fill: '#263d30', 'font-size': 11, 'font-weight': 600}));
          el.append(text(secondLine, -72, 2,
            {fill: '#263d30', 'font-size': 11}));
          el.append(text((node.item.year || 'ไม่พบปี') + (node.item.oa_pdf_url ? '  ·  PDF available' : ''), -72, 24,
            {fill: '#89947e', 'font-size': 9}));
          node.body.setAttribute('class', 'graph-paper-card');
        }
        if (node.parent) {
          node.edge = make('path', {stroke: node.color, 'stroke-width': node.kind === 'group' ? 1.7 : 1,
            'stroke-opacity': node.kind === 'group' ? .55 : .22}); edges.append(node.edge);
        }
        el.addEventListener('click', () => { if (node.wasDragged) { node.wasDragged = false; return; } inspect(node); });
        el.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); inspect(node); }
        });
        nodeLayer.append(el);
      }
      const point = event => {
        const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY;
        return p.matrixTransform(svg.getScreenCTM().inverse());
      };
      const toWorld = p => ({x: (p.x - offsetX) / zoom, y: (p.y - offsetY) / zoom});
      svg.onpointerdown = event => {
        if (event.button !== 0) return;
        const p = point(event), target = event.target.closest('[data-graph-node]');
        const node = nodes.find(n => n.el === target);
        const q = toWorld(p);
        interaction = node ? {node, id: event.pointerId, start: p, dx: node.x - q.x, dy: node.y - q.y, moved: false}
          : {id: event.pointerId, start: p, x: offsetX, y: offsetY};
        (node ? node.el : svg).setPointerCapture(event.pointerId);
      };
      svg.onpointermove = event => {
        if (!interaction || interaction.id !== event.pointerId) return;
        const p = point(event);
        if (interaction.node) {
          const q = toWorld(p), n = interaction.node;
          const x = q.x + interaction.dx, y = q.y + interaction.dy;
          const dx = x - n.x, dy = y - n.y;
          if (n.kind === 'group') for (const child of nodes.filter(v => v.parent === n)) { child.x += dx; child.y += dy; }
          n.x = x; n.y = y;
          interaction.moved ||= Math.hypot(p.x - interaction.start.x, p.y - interaction.start.y) > 3;
        } else { offsetX = interaction.x + p.x - interaction.start.x; offsetY = interaction.y + p.y - interaction.start.y; }
        update();
      };
      const end = event => {
        if (!interaction || interaction.id !== event.pointerId) return;
        if (interaction.node) interaction.node.wasDragged = interaction.moved;
        interaction = null; onChange?.();
      };
      svg.onpointerup = end; svg.onpointercancel = end;
      const zoomAt = (factor, p = {x: width / 2, y: height / 2}) => {
        const q = toWorld(p); zoom = Math.max(.08, Math.min(5, zoom * factor));
        offsetX = p.x - q.x * zoom; offsetY = p.y - q.y * zoom; update();
      };
      svg.onwheel = event => { event.preventDefault(); zoomAt(event.deltaY < 0 ? 1.12 : .89, point(event));onChange?.(); };
      document.getElementById('graph-zoom-in').onclick = () => {zoomAt(1.2);onChange?.();};
      document.getElementById('graph-zoom-out').onclick = () => {zoomAt(1 / 1.2);onChange?.();};
      document.getElementById('graph-fit').onclick = () => {
        const maxY = Math.max(...nodes.map(n => n.y + n.height / 2)) + 30;
        zoom = Math.min(1, (height - 40) / maxY); offsetX = width / 2 * (1 - zoom); offsetY = 20; update();onChange?.();
      };
      document.getElementById('graph-reset').onclick = () => {
        // Rebuild from metadata, discarding manual positions.
        window.PaperRefGraph.render({svg, items, sourceName, relationText, themes, onOpen, onSelect, isSelected, allowSelection, onChange});onChange?.();
      };
      update();
    }
  };
})();
