/* Local PDF preview. PDF bytes and passwords never leave this browser. */
(() => {
  const byId = id => document.getElementById(id);
  const panel = byId('pdf-preview'), body = byId('pdf-preview-body');
  const canvas = byId('pdf-preview-canvas'), viewport = byId('pdf-preview-viewport');
  const status = byId('pdf-preview-status'), pageLabel = byId('pdf-page-label');
  const toggle = byId('pdf-preview-toggle'), open = byId('pdf-open');
  const prev = byId('pdf-prev'), next = byId('pdf-next');
  const zoomOut = byId('pdf-zoom-out'), zoomIn = byId('pdf-zoom-in');
  let generation = 0, renderVersion = 0, session = null, renderTask = null;
  let pageNumber = 1, zoom = 1, busy = false, fileUrl = null;
  let workerUrl = null, workerAsset = null, resizeTimer = null, lastWidth = 0;

  const buttons = () => {
    const ready = !!session?.document && !busy;
    prev.disabled = !ready || pageNumber <= 1;
    next.disabled = !ready || pageNumber >= session.document.numPages;
    zoomOut.disabled = !ready || zoom <= 0.75;
    zoomIn.disabled = !ready || zoom >= 2;
    byId('pdf-zoom-label').textContent = Math.round(zoom * 100) + '%';
  };
  const setBusy = value => {
    busy = value; viewport.setAttribute('aria-busy', String(value)); buttons();
  };
  const dispose = entry => {
    if (!entry) return;
    entry.disposed = true;
    entry.reject?.(new Error('ยกเลิกพรีวิว'));
    // getDocument does not own an explicitly supplied worker/port.
    let terminated = false;
    const terminate = () => {
      if (terminated) return; terminated = true;
      try { entry.pdfWorker?.destroy(); } finally { entry.worker?.terminate(); }
    };
    const timer = setTimeout(terminate, 1000);
    Promise.resolve(entry.loading?.destroy()).catch(() => {}).finally(() => {
      clearTimeout(timer); terminate();
    });
  };
  const clear = () => {
    generation++; renderVersion++; clearTimeout(resizeTimer);
    renderTask?.cancel(); renderTask = null;
    dispose(session); session = null;
    if (fileUrl) URL.revokeObjectURL(fileUrl);
    fileUrl = null; open.removeAttribute('href');
    canvas.width = canvas.height = 0; canvas.classList.add('hidden');
    panel.classList.add('hidden'); status.textContent = ''; pageLabel.textContent = 'หน้า —';
    pageNumber = 1; zoom = 1; setBusy(false);
  };
  async function workerSource() {
    if (!workerAsset) workerAsset = (async () => {
      const embedded = byId('pdf-worker-data');
      let source;
      if (embedded) source = Uint8Array.from(atob(embedded.content.textContent.trim()), c => c.charCodeAt(0));
      else {
        const response = await fetch('/static/vendor/pdf.worker.min.js');
        if (!response.ok) throw new Error('เปิดตัวอ่าน PDF ไม่สำเร็จ');
        source = await response.arrayBuffer();
      }
      workerUrl = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
      return workerUrl;
    })().catch(error => { workerAsset = null; throw error; });
    return workerAsset;
  }
  async function bounded(promise) {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('PDF page rendering timed out')), 30000);
      })]);
    } finally { clearTimeout(timer); }
  }
  async function render() {
    if (!session?.document || body.classList.contains('hidden')) return;
    const current = session, version = ++renderVersion, source = generation;
    const oldRender = renderTask; oldRender?.cancel(); renderTask = null;
    setBusy(true); status.textContent = 'กำลังแสดงหน้า ' + pageNumber + '…';
    try {
      if (oldRender) await oldRender.promise.catch(() => {});
      const page = await bounded(current.document.getPage(pageNumber));
      if (source !== generation || version !== renderVersion) return;
      const original = page.getViewport({scale: 1});
      const available = Math.max(160, viewport.clientWidth - 32);
      const view = page.getViewport({scale: available / original.width * zoom});
      // Keep one page in a bounded canvas, including high-DPI phones/tablets.
      const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(2400000 / (view.width * view.height)));
      canvas.width = Math.ceil(view.width * ratio); canvas.height = Math.ceil(view.height * ratio);
      canvas.style.width = view.width + 'px'; canvas.style.height = view.height + 'px';
      canvas.setAttribute('aria-label', current.name + ' · หน้า ' + pageNumber + ' จาก ' + current.document.numPages);
      renderTask = page.render({canvas, viewport: view, transform: [ratio, 0, 0, ratio, 0, 0], background: '#ffffff'});
      await bounded(renderTask.promise);
      if (source !== generation || version !== renderVersion) return;
      pageLabel.textContent = 'หน้า ' + pageNumber + ' / ' + current.document.numPages;
      canvas.classList.remove('hidden'); status.textContent = '';
      viewport.scrollTop = 0; viewport.scrollLeft = 0;
    } catch (error) {
      if (source === generation && version === renderVersion && error.name !== 'RenderingCancelledException') {
        renderTask?.cancel();
        canvas.classList.add('hidden');
        status.textContent = 'แสดงหน้านี้ไม่ได้ ลองหน้าอื่นหรือเลือก “เปิดแยก”';
      }
    } finally {
      if (source === generation && version === renderVersion) { renderTask = null; setBusy(false); }
    }
  }
  async function setFile(file) {
    clear(); if (!file) return;
    const source = generation;
    panel.classList.remove('hidden'); body.classList.remove('hidden');
    toggle.textContent = 'ซ่อนพรีวิว'; toggle.setAttribute('aria-expanded', 'true');
    // Force PDF MIME even if an operating system supplies an ambiguous type.
    fileUrl = URL.createObjectURL(file.slice(0, file.size, 'application/pdf')); open.href = fileUrl;
    status.textContent = 'กำลังเปิดพรีวิว…'; setBusy(true);
    let timeout;
    const entry = {name: file.name}; session = entry;
    try {
      if (!window.pdfjsLib) throw new Error('เบราว์เซอร์นี้เปิดพรีวิวไม่ได้ กรุณาเลือก “เปิดแยก”');
      const failure = new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error('เปิดพรีวิวนานเกินไป ลองแนบใหม่หรือเลือก “เปิดแยก”')), 30000);
        entry.reject = reject;
      });
      const load = (async () => {
        const [data, url] = await Promise.all([file.arrayBuffer(), workerSource()]);
        if (source !== generation || entry.disposed) return null;
        // Supply a classic worker port; PDF.js otherwise wraps blob URLs in
        // a module import. No eval or blob permission for page scripts needed.
        entry.worker = new Worker(url, {name: 'paperref-pdf-preview'});
        const workerFailed = event => {
          event.preventDefault();
          const message = 'ตัวอ่าน PDF เปิดไม่สำเร็จ กรุณาเลือก “เปิดแยก”';
          if (source === generation && entry.document && session === entry) {
            renderVersion++; renderTask?.cancel(); renderTask = null;
            dispose(entry); session = null; canvas.classList.add('hidden');
            status.textContent = message; setBusy(false);
          }
          entry.reject(new Error(message));
        };
        entry.worker.addEventListener('error', workerFailed);
        entry.worker.addEventListener('messageerror', workerFailed);
        entry.pdfWorker = new pdfjsLib.PDFWorker({port: entry.worker});
        entry.loading = pdfjsLib.getDocument({data: new Uint8Array(data), worker: entry.pdfWorker,
          useSystemFonts: true, useWasm: false, useWorkerFetch: false, stopAtErrors: false,
          isImageDecoderSupported: false});
        entry.loading.onPassword = () => entry.reject(new Error('PDF นี้มีรหัสผ่าน กรุณาเลือก “เปิดแยก”'));
        return entry.loading.promise;
      })();
      entry.document = await Promise.race([load, failure]);
      if (source !== generation || !entry.document) return;
      clearTimeout(timeout); await render();
    } catch (error) {
      if (source !== generation) return;
      dispose(entry); session = null; canvas.classList.add('hidden');
      status.textContent = error.name === 'InvalidPDFException'
        ? 'เปิดพรีวิวไม่ได้ ไฟล์ PDF อาจไม่สมบูรณ์ กรุณาตรวจไฟล์หรือเลือก “เปิดแยก”'
        : error.message || 'เปิดพรีวิวไม่ได้ กรุณาเลือก “เปิดแยก”';
    } finally {
      clearTimeout(timeout); if (source === generation) setBusy(false);
    }
  }
  prev.addEventListener('click', () => { if (pageNumber > 1) { pageNumber--; render(); } });
  next.addEventListener('click', () => { if (pageNumber < session?.document?.numPages) { pageNumber++; render(); } });
  zoomOut.addEventListener('click', () => { zoom = Math.max(0.75, zoom - 0.25); render(); });
  zoomIn.addEventListener('click', () => { zoom = Math.min(2, zoom + 0.25); render(); });
  toggle.addEventListener('click', () => {
    const collapsed = body.classList.toggle('hidden');
    toggle.setAttribute('aria-expanded', String(!collapsed)); toggle.textContent = collapsed ? 'แสดงพรีวิว' : 'ซ่อนพรีวิว';
    if (!collapsed) render();
  });
  const resized = () => {
    const width = viewport.clientWidth; if (width === lastWidth) return;
    lastWidth = width; clearTimeout(resizeTimer);
    if (width > 0 && session?.document) resizeTimer = setTimeout(render, 160);
  };
  if (window.ResizeObserver) new ResizeObserver(resized).observe(viewport);
  else window.addEventListener('resize', resized);
  window.addEventListener('pagehide', event => {
    if (event.persisted) return;
    clear(); if (workerUrl) URL.revokeObjectURL(workerUrl);
  });
  window.PaperRefPreview = {setFile, clear};
})();
