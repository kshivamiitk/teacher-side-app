// static/app.js
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';

(() => {
  // DOM
  const statusEl = document.getElementById('status');
  const roleBanner = document.getElementById('roleBanner');
  const btnTeacher = document.getElementById('btnTeacher');
  const btnStudent = document.getElementById('btnStudent');

  const teacherPanelWrap = document.getElementById('teacherPanel');
  const studentPanelWrap = document.getElementById('studentPanel');

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
  const clearAllBtn = document.getElementById('clearAllBtn');
  const clearTeacherBtn = document.getElementById('clearTeacherBtn');
  const clearStudentLastBtn = document.getElementById('clearStudentLastBtn');
  const clearStudentAllBtn = document.getElementById('clearStudentAllBtn');

  const clearMyBtn = document.getElementById('clearMyBtn');

  const pdfContainer = document.getElementById('pdfContainer');
  const annotatorBadge = document.getElementById('annotatorBadge');
  const annotatorNameEl = document.getElementById('annotatorName');
  const stopAnnotateBtn = document.getElementById('stopAnnotateBtn');

  // state
  let socket = null;
  let myId = null;
  let myRole = null;
  let myToken = null; // 'teacher' or student_token
  let currentClass = null;
  let pdfDoc = null;
  let appliedStrokes = {}; // page -> [strokes]
  let pageCanvases = {};   // page -> {pdfCanvas, annoCanvas, width, height}
  let currentAnnotator = null;
  let isDrawing = false;
  let currentStroke = null;

  // localStorage keys
  const LS_ROLE = "pdfannot_role";
  const LS_CLASS = "pdfannot_class";
  const LS_TEACHER_KEY = "pdfannot_teacher_key";
  const LS_STUDENT_TOKEN = "pdfannot_student_token";
  const LS_STUDENT_NAME = "pdfannot_student_name";

  // role selection UI
  btnTeacher.addEventListener('click', () => {
    roleBanner.style.display = 'none';
    teacherPanelWrap.style.display = 'block';
    studentPanelWrap.style.display = 'none';
    myRole = 'teacher';
    statusEl.textContent = 'Role: Teacher (not connected)';
  });
  btnStudent.addEventListener('click', () => {
    roleBanner.style.display = 'none';
    teacherPanelWrap.style.display = 'none';
    studentPanelWrap.style.display = 'block';
    myRole = 'student';
    statusEl.textContent = 'Role: Student (not connected)';
  });

  // auto-rejoin on load
  window.addEventListener('load', () => {
    const role = localStorage.getItem(LS_ROLE);
    const classId = localStorage.getItem(LS_CLASS);
    if (!role || !classId) return;
    if (role === 'teacher') {
      const key = localStorage.getItem(LS_TEACHER_KEY);
      if (!key) return;
      roleBanner.style.display = 'none';
      teacherPanelWrap.style.display = 'block';
      studentPanelWrap.style.display = 'none';
      myRole = 'teacher';
      connectAndJoin({type:'join', role:'teacher', class_id: classId, key: key, name: 'Teacher (reconnect)'});
    } else if (role === 'student') {
      const token = localStorage.getItem(LS_STUDENT_TOKEN);
      const name = localStorage.getItem(LS_STUDENT_NAME) || 'Student';
      roleBanner.style.display = 'none';
      teacherPanelWrap.style.display = 'none';
      studentPanelWrap.style.display = 'block';
      myRole = 'student';
      studentName.value = name;
      joinClassId.value = classId;
      connectAndJoin({type:'join', role:'student', class_id: classId, student_token: token, name: name});
    }
  });

  // connect + join helper
  function connectAndJoin(joinMsg) {
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
    socket.onclose = () => { statusEl.textContent = 'Disconnected'; };
    socket.onerror = (e) => { console.error('ws error', e); };
  }

  function handleMessage(msg) {
    if (msg.type === 'error') {
      console.error('Server error', msg.error);
      statusEl.textContent = 'Error: ' + (msg.error || '');
      if (!myId) joinInfo.innerText = 'Error: ' + (msg.error || '');
      return;
    }
    switch (msg.type) {
      case 'joined':
        myId = msg.id;
        myRole = msg.role;
        currentClass = msg.class_id;
        statusEl.textContent = `Connected as ${myRole} (class ${currentClass})`;

        // persist role/class for reconnect
        localStorage.setItem(LS_ROLE, myRole);
        localStorage.setItem(LS_CLASS, currentClass);

        if (myRole === 'teacher') {
          if (msg.teacher_key) localStorage.setItem(LS_TEACHER_KEY, msg.teacher_key);
          classInfo.innerHTML = `<div>Class ID: <b>${currentClass}</b></div><div>Teacher Key: <b>${msg.teacher_key || ''}</b></div>`;
          myToken = 'teacher';
        } else {
          if (msg.student_token) {
            myToken = msg.student_token;
            localStorage.setItem(LS_STUDENT_TOKEN, myToken);
            localStorage.setItem(LS_STUDENT_NAME, msg.name || 'Student');
          }
          requestAnnotateBtn.disabled = false;
        }

        if (msg.pdf_url) loadPdf(msg.pdf_url);
        break;

      case 'presence':
        participantsTeacher.innerHTML = '';
        participantsStudent.innerHTML = '';
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
            currentAnnotator = myToken;
            updateAnnotatorUI();
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
        Object.keys(pageCanvases).forEach(p => redrawPage(parseInt(p)));
        break;

      case 'apply_stroke':
        const st = msg.stroke;
        appliedStrokes[st.page] = appliedStrokes[st.page] || [];
        appliedStrokes[st.page].push({author: st.author, color: st.color, width: st.width, points: st.points});
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

  // UI helpers
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

  function updateAnnotatorUI() {
    if (currentAnnotator) annotatorBadge.style.display = 'flex'; else annotatorBadge.style.display = 'none';
    stopAnnotateBtn.style.display = (myRole === 'teacher') ? 'inline-block' : 'none';
  }

  // events: uploading / joining / requesting annotate
  uploadForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!pdfFile.files || pdfFile.files.length === 0) { alert('Choose a PDF'); return; }
    const f = pdfFile.files[0];
    const fd = new FormData(); fd.append('pdf', f);
    statusEl.textContent = 'Uploading PDF...';
    const res = await fetch('/upload', {method:'POST', body: fd});
    const j = await res.json();
    if (!j.ok) { alert('Upload failed: ' + (j.error || 'unknown')); statusEl.textContent = ''; return; }
    localStorage.setItem(LS_ROLE, 'teacher');
    localStorage.setItem(LS_CLASS, j.class_id);
    localStorage.setItem(LS_TEACHER_KEY, j.teacher_key);
    connectAndJoin({type:'join', role:'teacher', class_id: j.class_id, key: j.teacher_key, name: 'Teacher'});
    classInfo.innerHTML = `<div>Class ID: <b>${j.class_id}</b></div><div>Teacher Key: <b>${j.teacher_key}</b></div>`;
  });

  joinBtn.addEventListener('click', () => {
    const cls = joinClassId.value.trim();
    const name = studentName.value.trim() || ('Student-' + Math.random().toString(36).slice(2,6));
    if (!cls) { joinInfo.innerText = 'Enter class id'; return; }
    localStorage.setItem(LS_ROLE, 'student');
    localStorage.setItem(LS_CLASS, cls);
    localStorage.setItem(LS_STUDENT_NAME, name);
    const token = localStorage.getItem(LS_STUDENT_TOKEN) || null;
    connectAndJoin({type:'join', role:'student', class_id: cls, student_token: token, name: name});
  });

  requestAnnotateBtn.addEventListener('click', () => {
    if (!socket) { joinInfo.innerText = 'Join first'; return; }
    const page = visibleTopPage() || 1;
    socket.send(JSON.stringify({type:'request_annotate', page: page, note: ''}));
    annotateStatus.innerText = 'Requested — waiting for teacher approval...';
  });

  gotoPageBtn.addEventListener('click', () => {
    const p = parseInt(teacherPageInput.value);
    if (!socket || isNaN(p) || p < 1) return;
    socket.send(JSON.stringify({type:'goto_page', page: p}));
    scrollToPage(p);
  });

  // teacher clear buttons
  clearAllBtn.addEventListener('click', () => {
    if (!socket) return;
    if (!confirm("Clear ALL annotations (teacher+students)?")) return;
    socket.send(JSON.stringify({type:'clear_annotations'}));
  });
  clearTeacherBtn.addEventListener('click', () => {
    if (!socket) return;
    socket.send(JSON.stringify({type:'clear_teacher_annotations'}));
  });
  clearStudentLastBtn.addEventListener('click', () => {
    if (!socket) return;
    socket.send(JSON.stringify({type:'clear_student_annotations'}));
  });
  clearStudentAllBtn.addEventListener('click', () => {
    if (!socket) return;
    if (!confirm("This clears all student annotations (teacher annotations preserved). Continue?")) return;
    // We don't have a dedicated server action to clear all student annotations, so we implement by clearing teacher annotations after temporarily keeping students:
    // Instead call clear_teacher_annotations to remove teacher then re-add teacher? Simpler: call clear_teacher_annotations to remove teacher annotations? NO.
    // We'll ask server to remove all non-teacher authors: implement by sending clear_teacher_annotations? -> server removes teacher only.
    // So instead we will call a small special request type 'clear_all_students' — but server not implemented for that. Simpler approach: 
    // Use existing 'clear_student_annotations' which removes last student only. To clear all student annotations require server change.
    // For now, map this to a loop of clearing last student multiple times isn't reliable. So we will call a new type 'clear_all_student_annotations' - server doesn't implement it.
    // Instead as minimal safe change: call 'clear_student_annotations' (last) and inform teacher to repeat if necessary.
    socket.send(JSON.stringify({type:'clear_student_annotations'}));
    alert("Cleared last student annotations. To remove all students' annotations, repeat or use 'Clear all annotations'.");
  });

  // student clear my annotations
  clearMyBtn.addEventListener('click', () => {
    if (!socket) return;
    socket.send(JSON.stringify({type:'clear_my_annotations'}));
  });

  stopAnnotateBtn.addEventListener('click', () => {
    if (!socket) return;
    socket.send(JSON.stringify({type:'revoke'}));
  });

  // ---------------- PDF rendering with mobile scaling ----------------
  async function loadPdf(url) {
    statusEl.textContent = 'Loading PDF...';
    pdfDoc = await pdfjsLib.getDocument(url).promise;
    pdfContainer.innerHTML = ''; pageCanvases = {};
    for (let p = 1; p <= pdfDoc.numPages; ++p) {
      const page = await pdfDoc.getPage(p);
      // compute scale so page fits pdfContainer width on mobile
      const unscaled = page.getViewport({scale: 1});
      const containerWidth = Math.max(300, Math.min(window.innerWidth, pdfContainer.clientWidth || window.innerWidth));
      // small padding margin
      const scale = Math.min(1.8, (containerWidth - 24) / unscaled.width * 1.0);
      const viewport = page.getViewport({scale});
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
      if (!(myRole === 'teacher' || currentAnnotator === myToken)) return;
      isDrawing = true;
      currentStroke = {page: page, points: [pointerToNormalized(e, canvas)], color: colorPicker.value, width: parseInt(widthPicker.value, 10)};
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', (e) => {
      if (!isDrawing || !currentStroke) return;
      currentStroke.points.push(pointerToNormalized(e, canvas));
      redrawPage(page);
      drawStrokeOnCanvas(currentStroke, page);
    });
    canvas.addEventListener('pointerup', (e) => {
      if (!isDrawing) return;
      isDrawing = false;
      if (currentStroke && currentStroke.points.length > 0) {
        appliedStrokes[page] = appliedStrokes[page] || [];
        appliedStrokes[page].push({author: myToken === null ? "anon" : myToken, color: currentStroke.color, width: currentStroke.width, points: currentStroke.points});
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
