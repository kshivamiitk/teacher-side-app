// static/app.js
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';

(() => {
  // DOM references (same as previous file)
  const statusEl = document.getElementById('status');
  const roleBanner = document.getElementById('roleBanner');
  const btnTeacher = document.getElementById('btnTeacher');
  const btnStudent = document.getElementById('btnStudent');
  const teacherPanel = document.getElementById('teacherPanel');
  const studentPanel = document.getElementById('studentPanel');

  const uploadForm = document.getElementById('uploadForm');
  const pdfFile = document.getElementById('pdfFile');
  const classInfo = document.getElementById('classInfo');

  const joinClassId = document.getElementById('joinClassId');
  const studentName = document.getElementById('studentName');
  const joinBtn = document.getElementById('joinBtn');
  const joinInfo = document.getElementById('joinInfo');

  const pendingList = document.getElementById('pendingList');
  const participantsTeacher = document.getElementById('participantsTeacher');
  const participantsStudent = document.getElementById('participantsStudent');

  const requestAnnotateBtn = document.getElementById('requestAnnotateBtn');
  const annotateStatus = document.getElementById('annotateStatus');
  const colorPicker = document.getElementById('colorPicker');
  const widthPicker = document.getElementById('widthPicker');

  const teacherPageInput = document.getElementById('teacherPage');
  const gotoPageBtn = document.getElementById('gotoPageBtn');
  const clearBtn = document.getElementById('clearBtn');

  const pdfContainer = document.getElementById('pdfContainer');
  const annotatorBadge = document.getElementById('annotatorBadge');
  const annotatorNameEl = document.getElementById('annotatorName');
  const stopAnnotateBtn = document.getElementById('stopAnnotateBtn');

  // state
  let socket = null;
  let myId = null;
  let myRole = null;
  let currentClass = null;
  let pdfDoc = null;
  let currentAnnotator = null;
  let appliedStrokes = {}; // page -> [strokes]
  let pageCanvases = {};   // page -> {pdfCanvas, annoCanvas, width, height}
  let isDrawing = false;
  let currentStroke = null;

  // Role selection UI
  btnTeacher.addEventListener('click', () => {
    roleBanner.style.display = 'none';
    teacherPanel.style.display = 'block';
    studentPanel.style.display = 'none';
    myRole = 'teacher';
    statusEl.textContent = 'Role: Teacher (not connected)';
  });
  btnStudent.addEventListener('click', () => {
    roleBanner.style.display = 'none';
    teacherPanel.style.display = 'none';
    studentPanel.style.display = 'block';
    myRole = 'student';
    statusEl.textContent = 'Role: Student (not connected)';
  });

  // WebSocket
  function connectAndSendJoin(joinMsg) {
    if (socket) socket.close();
    const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    socket = new WebSocket(`${proto}://${location.host}/ws`);
    socket.onopen = () => {
      socket.send(JSON.stringify(joinMsg));
      statusEl.textContent = 'Connected — joining...';
    };
    socket.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      handleMessage(msg);
    };
    socket.onclose = () => {
      statusEl.textContent = 'Disconnected';
    };
    socket.onerror = (e) => {
      console.error('ws error', e);
    };
  }

  function handleMessage(msg) {
    if (msg.type === 'error') {
      console.error('Server error', msg.error);
      if (!myId) joinInfo.innerText = 'Error: ' + (msg.error || JSON.stringify(msg));
      statusEl.textContent = 'Error: ' + (msg.error || '');
      return;
    }
    switch (msg.type) {
      case 'joined':
        myId = msg.id; myRole = msg.role; currentClass = msg.class_id;
        statusEl.textContent = `Connected as ${myRole} (class ${currentClass})`;
        if (msg.pdf_url) loadPdf(msg.pdf_url);
        if (myRole === 'teacher') {
          classInfo.innerHTML = `<div>Class ID: <b>${currentClass}</b></div><div>Teacher Key: <b>${msg.teacher_key || ''}</b></div>`;
        } else {
          joinInfo.innerText = `Joined class ${currentClass} as ${msg.name || 'Student'}`;
          requestAnnotateBtn.disabled = false;
        }
        break;

      case 'presence':
        participantsTeacher.innerHTML = ''; participantsStudent.innerHTML = '';
        (msg.clients || []).forEach(p => {
          const li = document.createElement('li'); li.textContent = p.name + (p.role === 'teacher' ? ' (Teacher)' : '');
          participantsTeacher.appendChild(li);
          participantsStudent.appendChild(li.cloneNode(true));
        });
        break;

      case 'pending_list':
        pendingList.innerHTML = '';
        if (!msg.pending || !msg.pending.length) pendingList.innerText = 'No pending requests';
        else msg.pending.forEach(it => addPendingItem(it.request_id, it.name, it.page, it.note));
        break;

      case 'pending_new':
        if (myRole === 'teacher') addPendingItem(msg.request_id, msg.name, msg.page, msg.note);
        break;

      case 'request_result':
        if (myRole === 'student') {
          if (msg.result === 'approved') {
            annotateStatus.innerText = 'Approved to annotate page ' + msg.page;
          } else {
            annotateStatus.innerText = 'Request denied by teacher';
          }
        }
        break;

      case 'annotator_update':
        currentAnnotator = msg.current_annotator;
        annotatorNameEl.textContent = msg.annotator_name || '—';
        updateAnnotatorUI();
        break;

      case 'init_strokes':
        appliedStrokes = msg.strokes || {};
        // redraw pages already rendered
        Object.keys(pageCanvases).forEach(p => redrawPage(parseInt(p)));
        break;

      case 'apply_stroke':
        const st = msg.stroke;
        appliedStrokes[st.page] = appliedStrokes[st.page] || [];
        appliedStrokes[st.page].push({
          author: st.author,
          color: st.color,
          width: st.width,
          points: st.points
        });
        if (pageCanvases[st.page]) redrawPage(parseInt(st.page));
        break;

      case 'clear_annotations':
        appliedStrokes = {};
        Object.keys(pageCanvases).forEach(p => redrawPage(parseInt(p)));
        break;

      case 'goto_page':
        scrollToPage(msg.page);
        break;

      case 'info':
        console.info(msg.message);
        break;

      default:
        console.debug('unhandled', msg);
    }
  }

  // Teacher upload & join
  uploadForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!pdfFile.files || pdfFile.files.length === 0) { alert('Choose a PDF'); return; }
    const f = pdfFile.files[0];
    const fd = new FormData(); fd.append('pdf', f);
    statusEl.textContent = 'Uploading PDF...';
    const res = await fetch('/upload', {method:'POST', body: fd});
    const j = await res.json();
    if (!j.ok) { alert('Upload failed: ' + (j.error || 'unknown')); statusEl.textContent = ''; return; }
    connectAndSendJoin({type:'join', role:'teacher', class_id: j.class_id, key: j.teacher_key, name: 'Teacher'});
    classInfo.innerHTML = `<div>Class ID: <b>${j.class_id}</b></div><div>Teacher Key: <b>${j.teacher_key}</b></div>`;
  });

  // Student join
  joinBtn.addEventListener('click', () => {
    const cls = joinClassId.value.trim();
    const name = studentName.value.trim() || ('Student-' + Math.random().toString(36).slice(2,6));
    if (!cls) { joinInfo.innerText = 'Enter class id'; return; }
    connectAndSendJoin({type:'join', role:'student', class_id: cls, name});
  });

  // Request annotate
  requestAnnotateBtn.addEventListener('click', () => {
    if (!socket) { joinInfo.innerText = 'Join first'; return; }
    const page = visibleTopPage() || 1;
    socket.send(JSON.stringify({type:'request_annotate', page: page, note: ''}));
    annotateStatus.innerText = 'Requested — waiting for teacher approval...';
  });

  // Pending UI (teacher)
  function addPendingItem(reqid, name, page, note) {
    const el = document.createElement('div'); el.className = 'pending-entry';
    el.innerHTML = `<div><b>${name}</b> requested page ${page}</div>`;
    const row = document.createElement('div'); row.className='row';
    const approve = document.createElement('button'); approve.textContent = 'Approve';
    approve.onclick = () => { socket.send(JSON.stringify({type:'approve', request_id: reqid})); el.remove(); };
    const deny = document.createElement('button'); deny.textContent = 'Deny'; deny.style.marginLeft='8px';
    deny.onclick = () => { socket.send(JSON.stringify({type:'deny', request_id: reqid})); el.remove(); };
    row.appendChild(approve); row.appendChild(deny); el.appendChild(row);
    if (pendingList.innerText.trim() === 'No pending requests') pendingList.innerHTML = '';
    pendingList.appendChild(el);
  }

  // Teacher controls
  gotoPageBtn.addEventListener('click', () => {
    const p = parseInt(teacherPageInput.value);
    if (!socket || isNaN(p) || p<1) return;
    socket.send(JSON.stringify({type:'goto_page', page: p}));
    scrollToPage(p);
  });

  // NEW: clear student annotations only
  clearBtn.addEventListener('click', () => {
    if (!socket) return;
    socket.send(JSON.stringify({type:'clear_student_annotations'}));
  });

  stopAnnotateBtn.addEventListener('click', () => {
    if (!socket) return;
    socket.send(JSON.stringify({type:'revoke'}));
  });

  function updateAnnotatorUI() {
    if (currentAnnotator) annotatorBadge.style.display = 'flex'; else annotatorBadge.style.display = 'none';
    stopAnnotateBtn.style.display = (myRole === 'teacher') ? 'inline-block' : 'none';
  }

  // ---------------- PDF rendering SCROLLABLE ----------------
  async function loadPdf(url) {
    statusEl.textContent = 'Loading PDF...';
    pdfDoc = await pdfjsLib.getDocument(url).promise;
    pdfContainer.innerHTML = ''; pageCanvases = {};
    for (let p = 1; p <= pdfDoc.numPages; ++p) {
      const page = await pdfDoc.getPage(p);
      const viewport = page.getViewport({scale: 1.5});
      const pageWrap = document.createElement('div');
      pageWrap.className = 'page-wrap';
      pageWrap.dataset.page = p;
      const pdfCanvas = document.createElement('canvas');
      pdfCanvas.className = 'pdf-page';
      pdfCanvas.width = Math.floor(viewport.width);
      pdfCanvas.height = Math.floor(viewport.height);
      pdfCanvas.style.width = Math.floor(viewport.width) + 'px';
      pdfCanvas.style.height = Math.floor(viewport.height) + 'px';
      const ctx = pdfCanvas.getContext('2d');
      await page.render({canvasContext: ctx, viewport}).promise;
      const annoCanvas = document.createElement('canvas');
      annoCanvas.className = 'anno-page';
      annoCanvas.width = pdfCanvas.width; annoCanvas.height = pdfCanvas.height;
      annoCanvas.style.width = pdfCanvas.style.width; annoCanvas.style.height = pdfCanvas.style.height;
      annoCanvas.dataset.page = p;
      attachDrawingHandlers(annoCanvas, p);
      pageWrap.appendChild(pdfCanvas);
      pageWrap.appendChild(annoCanvas);
      pdfContainer.appendChild(pageWrap);
      pageCanvases[p] = {pdfCanvas, annoCanvas, width: pdfCanvas.width, height: pdfCanvas.height};
    }
    // draw persisted strokes
    Object.keys(appliedStrokes).forEach(page => redrawPage(parseInt(page)));
    statusEl.textContent = `PDF loaded (${pdfDoc.numPages} pages)`;
  }

  function visibleTopPage(){
    const wraps = Array.from(document.querySelectorAll('.page-wrap'));
    for (const w of wraps) {
      const r = w.getBoundingClientRect();
      if (r.top >= 0 && r.top < (window.innerHeight || document.documentElement.clientHeight)/2) {
        return parseInt(w.dataset.page);
      }
    }
    return wraps.length ? parseInt(wraps[0].dataset.page) : 1;
  }

  function scrollToPage(page) {
    const el = document.querySelector(`.page-wrap[data-page='${page}']`);
    if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  // ---------------- Drawing helpers ----------------
  function attachDrawingHandlers(canvas, page) {
    canvas.addEventListener('pointerdown', (e) => {
      if (!(myRole === 'teacher' || currentAnnotator === myId)) return;
      isDrawing = true;
      const pt = pointerToNormalized(e, canvas);
      currentStroke = {page: page, points: [pt], color: colorPicker ? colorPicker.value : '#ff0000', width: parseInt(widthPicker ? widthPicker.value : 3, 10)};
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', (e) => {
      if (!isDrawing || !currentStroke) return;
      const pt = pointerToNormalized(e, canvas);
      currentStroke.points.push(pt);
      redrawPage(page);
      drawStrokeOnCanvas(currentStroke, page);
    });
    canvas.addEventListener('pointerup', (e) => {
      if (!isDrawing) return;
      isDrawing = false;
      if (currentStroke && currentStroke.points.length > 0) {
        appliedStrokes[page] = appliedStrokes[page] || [];
        appliedStrokes[page].push({
          author: myId,
          color: currentStroke.color,
          width: currentStroke.width,
          points: currentStroke.points
        });
        if (socket) socket.send(JSON.stringify({type:'stroke', stroke: {page: page.toString(), color: currentStroke.color, width: currentStroke.width, points: currentStroke.points}}));
        currentStroke = null;
      }
    });
  }

  function pointerToNormalized(evt, canvas) {
    const r = canvas.getBoundingClientRect();
    const x = (evt.clientX - r.left) / r.width;
    const y = (evt.clientY - r.top) / r.height;
    return {x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y))};
  }

  function denormalizePoint(pt, page) {
    const meta = pageCanvases[page];
    if (!meta) return {x:0,y:0};
    return {x: pt.x * meta.width, y: pt.y * meta.height};
  }

  function drawStrokeOnCanvas(stroke, page) {
    const meta = pageCanvases[page];
    if (!meta) return;
    const ctx = meta.annoCanvas.getContext('2d');
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.beginPath();
    const p0 = denormalizePoint(stroke.points[0], page);
    ctx.moveTo(p0.x, p0.y);
    for (let i=1;i<stroke.points.length;i++){
      const p = denormalizePoint(stroke.points[i], page);
      ctx.lineTo(p.x, p.y);
    }
    ctx.strokeStyle = stroke.color || '#ff0000';
    ctx.lineWidth = stroke.width || 3;
    ctx.stroke();
  }

  function redrawPage(page) {
    const meta = pageCanvases[page];
    if (!meta) return;
    const ctx = meta.annoCanvas.getContext('2d');
    ctx.clearRect(0,0,meta.annoCanvas.width, meta.annoCanvas.height);
    (appliedStrokes[page] || []).forEach(s => drawStrokeOnCanvas(s, page));
    if (currentStroke && currentStroke.page === page) drawStrokeOnCanvas(currentStroke, page);
  }

})();
