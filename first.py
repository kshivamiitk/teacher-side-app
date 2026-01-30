# class_pdf_annotator_role_select.py
"""
Collaborative PDF annotation (Teacher-approved) with initial role selection.
Run: pip install aiohttp
      python class_pdf_annotator_role_select.py
Open: http://localhost:8080
Notes:
 - No DB, all state in-memory.
 - Uploaded PDFs stored in ./uploads/
 - Browser renders PDFs via PDF.js (CDN).
"""

import asyncio
import json
import os
import secrets
import string
import uuid
from aiohttp import web, WSMsgType

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# In-memory classes: see below for structure
classes = {}

# helpers
def new_class_id():
    return secrets.token_urlsafe(6)

def new_teacher_key():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

async def broadcast_class(class_id, message, only=None):
    room = classes.get(class_id)
    if not room:
        return
    data = json.dumps(message)
    if only is None:
        targets = list(room["clients"].items())
    else:
        targets = [(cid, room["clients"][cid]) for cid in only if cid in room["clients"]]
    for cid, info in targets:
        try:
            await info["ws"].send_str(data)
        except Exception:
            pass

# -----------------------
# HTTP + Websocket handlers
# -----------------------
INDEX_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Class PDF Annotator — Role Select</title>
  <style>
    html,body{height:100%;margin:0;font-family:Arial,Helvetica,sans-serif}
    .top{padding:10px;background:#1f6feb;color:white;display:flex;align-items:center;gap:12px}
    .container{display:flex;height:calc(100% - 52px)}
    .left{flex:1;display:flex;flex-direction:column;align-items:stretch;position:relative;background:#e9eefc}
    #pdfCanvas{flex:1;display:block;background:white;border-right:1px solid #ddd}
    #annoCanvas{position:absolute;left:0;top:52px;width:100%;height:calc(100% - 52px);touch-action:none}
    .right{width:360px;border-left:1px solid #ddd;background:#fff;padding:12px;overflow:auto}
    button{padding:8px 12px;border-radius:6px;border:0;background:#1f6feb;color:white;cursor:pointer}
    .muted{color:#666;font-size:13px}
    .panel{display:none}
    .panel.active{display:block}
    .role-chooser{display:flex;gap:12px;align-items:center}
    .role-chooser button{background:#fff;color:#1f6feb;border:2px solid #1f6feb;padding:10px 16px}
    .pending-item{border:1px solid #eee;padding:8px;margin-bottom:8px;background:#fafafa}
    label{font-size:13px;display:block;margin-top:6px}
    input[type=file]{display:block}
    #status{margin-left:auto;color:white}
  </style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js"></script>
</head>
<body>
  <div class="top">
    <div style="font-weight:700">PDF Class — Teacher-approved Annotation</div>
    <div id="status">Not connected</div>
  </div>

  <div style="padding:14px;background:#f6f9ff;border-bottom:1px solid #e6eefc">
    <!-- Role selection modal / first step -->
    <div id="roleSelect" style="display:flex;justify-content:space-between;align-items:center;gap:16px">
      <div>
        <div style="font-size:18px;font-weight:600">Welcome — choose your role</div>
        <div style="margin-top:8px" class="muted">Select Teacher to start a class and upload a PDF. Select Student to join a class and request annotation.</div>
      </div>
      <div class="role-chooser">
        <button id="chooseTeacher">I am a Teacher</button>
        <button id="chooseStudent">I am a Student</button>
      </div>
    </div>
  </div>

  <div class="container">
    <div class="left">
      <canvas id="pdfCanvas"></canvas>
      <canvas id="annoCanvas"></canvas>
    </div>

    <div class="right">
      <!-- Teacher panel -->
      <div id="teacherPanel" class="panel">
        <h3>Teacher — Start Class</h3>
        <form id="uploadForm">
          <label>Upload PDF file</label>
          <input id="pdfFile" type="file" accept="application/pdf" required />
          <div style="margin-top:8px">
            <button id="startClassBtn" type="submit">Start Class & Upload PDF</button>
          </div>
        </form>
        <div id="classInfo" style="margin-top:12px"></div>
        <hr>
        <h4>Teacher controls</h4>
        <div>
          <label>Go to page: <input id="teacherPage" type="number" min="1" value="1" style="width:80px" /></label>
          <button id="gotoPageBtn">Go</button>
          <button id="clearAnnotationsBtn">Clear</button>
        </div>
        <h4 style="margin-top:10px">Pending requests</h4>
        <div id="pendingList">No pending requests</div>
        <h4>Participants</h4>
        <ul id="participants"></ul>
      </div>

      <!-- Student panel -->
      <div id="studentPanel" class="panel">
        <h3>Student — Join Class</h3>
        <label>Class ID:
          <input id="joinClassId" placeholder="paste Class ID" />
        </label>
        <label>Your name:
          <input id="studentName" placeholder="Your name" />
        </label>
        <div style="margin-top:8px">
          <button id="joinBtn">Join Class</button>
        </div>
        <div id="joinInfo" style="margin-top:10px" class="muted"></div>
        <hr>
        <div>
          <button id="requestAnnotateBtn" disabled>Request to Annotate (current page)</button>
          <div id="annotateStatus" style="margin-top:8px" class="muted"></div>
        </div>
        <h4 style="margin-top:12px">Participants</h4>
        <ul id="participants_student"></ul>
      </div>

      <div style="margin-top:16px" class="muted">
        Everything runs in-memory on the server. Use on a trusted LAN. No authentication beyond the teacher key.
      </div>
    </div>
  </div>

<script>
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';

(() => {
  // Elements
  const roleSelect = document.getElementById('roleSelect');
  const chooseTeacher = document.getElementById('chooseTeacher');
  const chooseStudent = document.getElementById('chooseStudent');

  const teacherPanel = document.getElementById('teacherPanel');
  const studentPanel = document.getElementById('studentPanel');

  const statusEl = document.getElementById('status');
  const classInfo = document.getElementById('classInfo');
  const uploadForm = document.getElementById('uploadForm');
  const pdfFile = document.getElementById('pdfFile');

  const pendingList = document.getElementById('pendingList');
  const participants = document.getElementById('participants');
  const participantsStudent = document.getElementById('participants_student');

  const joinClassId = document.getElementById('joinClassId');
  const studentName = document.getElementById('studentName');
  const joinBtn = document.getElementById('joinBtn');
  const joinInfo = document.getElementById('joinInfo');

  const requestAnnotateBtn = document.getElementById('requestAnnotateBtn');
  const annotateStatus = document.getElementById('annotateStatus');

  const teacherPageInput = document.getElementById('teacherPage');
  const gotoPageBtn = document.getElementById('gotoPageBtn');
  const clearAnnotationsBtn = document.getElementById('clearAnnotationsBtn');

  const pdfCanvas = document.getElementById('pdfCanvas');
  const annoCanvas = document.getElementById('annoCanvas');
  const ctxPdf = pdfCanvas.getContext('2d');
  const ctxAnno = annoCanvas.getContext('2d');

  // client state
  let socket = null;
  let myId = null;
  let myRole = null; // 'teacher' or 'student'
  let currentClassId = null;
  let pdfDoc = null;
  let currentPage = 1;
  let appliedStrokes = [];
  let allowedToAnnotate = false;
  let isDrawing = false;
  let currentStroke = null;

  // resizing canvases
  function fitCanvases(){
    const viewerRect = document.querySelector('.left').getBoundingClientRect();
    const w = Math.floor(viewerRect.width * devicePixelRatio);
    const h = Math.floor((viewerRect.height) * devicePixelRatio);
    pdfCanvas.width = w; pdfCanvas.height = h;
    pdfCanvas.style.width = viewerRect.width + 'px'; pdfCanvas.style.height = viewerRect.height + 'px';
    pdfCanvas.getContext('2d').setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);

    annoCanvas.width = w; annoCanvas.height = h;
    annoCanvas.style.width = viewerRect.width + 'px'; annoCanvas.style.height = viewerRect.height + 'px';
    annoCanvas.getContext('2d').setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
    redrawAnnotations();
  }
  window.addEventListener('resize', fitCanvases);
  setTimeout(fitCanvases, 100);

  // ---------- Role selection handlers ----------
  chooseTeacher.addEventListener('click', () => {
    roleSelect.style.display = 'none';
    teacherPanel.classList.add('active');
    studentPanel.classList.remove('active');
    myRole = 'teacher';
    statusEl.textContent = 'You selected: Teacher (not connected)';
  });

  chooseStudent.addEventListener('click', () => {
    roleSelect.style.display = 'none';
    studentPanel.classList.add('active');
    teacherPanel.classList.remove('active');
    myRole = 'student';
    statusEl.textContent = 'You selected: Student (not connected)';
  });

  // ---------- WebSocket connect + join ----------
  function connectAndJoin(roleInfo){
    if(socket){
      try { socket.close(); } catch(e) {}
      socket = null;
    }
    const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    socket = new WebSocket(proto + '://' + location.host + '/ws');

    socket.onopen = () => {
      socket.send(JSON.stringify(roleInfo));
      statusEl.textContent = 'Connected (joining)...';
    };

    socket.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      handleSocketMessage(msg);
    };

    socket.onclose = () => {
      statusEl.textContent = 'Disconnected';
    };
    socket.onerror = (e) => {
      console.error('ws error', e);
      statusEl.textContent = 'WebSocket error';
    };
  }

  function handleSocketMessage(msg){
    if(msg.type === 'error'){
      console.error('Server error:', msg.error || msg);
      // surface to UI
      if(!myId){
        // likely join error
        joinInfo.innerText = 'Error: ' + (msg.error || JSON.stringify(msg));
      } else {
        statusEl.textContent = 'Error: ' + (msg.error || JSON.stringify(msg));
      }
      return;
    }

    switch(msg.type){
      case 'joined':
        myId = msg.id;
        myRole = msg.role;
        currentClassId = msg.class_id;
        statusEl.textContent = `Connected as ${myRole} (Class ${currentClassId})`;
        if(myRole === 'teacher'){
          teacherPanel.classList.add('active');
          studentPanel.classList.remove('active');
          classInfo.innerHTML = `<div>Class ID: <b>${currentClassId}</b></div>
                                 <div>Teacher Key: <b>${msg.teacher_key || ''}</b></div>
                                 <div class="muted">Share the Class ID with students.</div>`;
        } else {
          studentPanel.classList.add('active');
          teacherPanel.classList.remove('active');
          joinInfo.innerText = `Joined class ${currentClassId} as ${msg.name || 'Student'}`;
          requestAnnotateBtn.disabled = false;
        }
        // load pdf if provided
        if(msg.pdf_url){
          loadPdf(msg.pdf_url).catch(console.error);
        }
        break;

      case 'presence':
        // update participants lists
        const list = msg.clients || [];
        participants.innerHTML = '';
        participantsStudent.innerHTML = '';
        for(const p of list){
          const li = document.createElement('li');
          li.textContent = p.name + (p.role === 'teacher' ? ' (Teacher)' : '');
          participants.appendChild(li);
          const li2 = li.cloneNode(true);
          participantsStudent.appendChild(li2);
        }
        break;

      case 'pending_list':
        if(myRole !== 'teacher') break;
        pendingList.innerHTML = '';
        if(!msg.pending || msg.pending.length === 0){
          pendingList.innerText = 'No pending requests';
        } else {
          for(const it of msg.pending) addPendingItem(it.request_id, it.name, it.page, it.note);
        }
        break;

      case 'pending_new':
        if(myRole === 'teacher'){
          addPendingItem(msg.request_id, msg.name, msg.page, msg.note);
        }
        break;

      case 'request_result':
        if(myRole === 'student'){
          if(msg.result === 'approved'){
            annotateStatus.innerText = 'Approved to annotate on page ' + msg.page;
            allowedToAnnotate = true;
          } else {
            annotateStatus.innerText = 'Request denied by teacher';
            allowedToAnnotate = false;
          }
        }
        break;

      case 'apply_stroke':
        appliedStrokes.push(msg.stroke);
        redrawAnnotations();
        break;

      case 'clear_annotations':
        appliedStrokes = [];
        allowedToAnnotate = false;
        redrawAnnotations();
        break;

      case 'goto_page':
        currentPage = msg.page;
        teacherPageInput.value = currentPage;
        renderPage(currentPage);
        break;

      case 'info':
        console.info('info:', msg.message);
        break;

      default:
        console.debug('Unhandled message', msg);
    }
  }

  // ---------- Teacher: upload & start class ----------
  uploadForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if(!pdfFile.files || pdfFile.files.length === 0){ alert('Choose a PDF'); return; }
    const f = pdfFile.files[0];
    const fd = new FormData();
    fd.append('pdf', f);
    statusEl.textContent = 'Uploading...';
    const resp = await fetch('/upload', {method:'POST', body: fd});
    const j = await resp.json();
    if(!j.ok){ alert('Upload failed: ' + (j.error || 'unknown')); statusEl.textContent = ''; return; }
    // join as teacher immediately using teacher_key
    const joinMsg = {type:'join', role:'teacher', class_id: j.class_id, key: j.teacher_key, name: 'Teacher'};
    connectAndJoin(joinMsg);
    // show class info
    classInfo.innerHTML = `<div>Class ID: <b>${j.class_id}</b></div>
                           <div>Teacher Key: <b>${j.teacher_key}</b></div>
                           <div class="muted">Share the Class ID with students.</div>`;
  });

  // ---------- Student: join ----------
  joinBtn.addEventListener('click', () => {
    const cls = joinClassId.value.trim();
    const name = studentName.value.trim() || ('Student-' + Math.random().toString(36).slice(2,6));
    if(!cls){ joinInfo.innerText = 'Enter Class ID'; return; }
    const joinMsg = {type:'join', role:'student', class_id: cls, name};
    connectAndJoin(joinMsg);
  });

  // ---------- Student: request annotate ----------
  requestAnnotateBtn.addEventListener('click', () => {
    if(!socket){ joinInfo.innerText = 'Join first'; return; }
    socket.send(JSON.stringify({type:'request_annotate', page: currentPage, note: ''}));
    annotateStatus.innerText = 'Requested annotation — waiting for teacher approval...';
  });

  // ---------- Teacher pending list UI ----------
  function addPendingItem(request_id, name, page, note){
    const el = document.createElement('div');
    el.className = 'pending-item';
    el.id = 'pending-' + request_id;
    el.innerHTML = `<div><b>${name}</b> requested annotation on page <b>${page}</b></div>
                    <div class="muted">${note || ''}</div>`;
    const approveBtn = document.createElement('button');
    approveBtn.textContent = 'Approve';
    approveBtn.onclick = () => {
      if(!socket) return;
      socket.send(JSON.stringify({type:'approve', request_id}));
      el.remove();
    };
    const denyBtn = document.createElement('button');
    denyBtn.textContent = 'Deny';
    denyBtn.style.marginLeft = '8px';
    denyBtn.onclick = () => {
      if(!socket) return;
      socket.send(JSON.stringify({type:'deny', request_id}));
      el.remove();
    };
    el.appendChild(approveBtn);
    el.appendChild(denyBtn);
    if(pendingList.innerText.trim() === 'No pending requests') pendingList.innerHTML = '';
    pendingList.appendChild(el);
  }

  // ---------- Teacher controls ----------
  gotoPageBtn.addEventListener('click', () => {
    const p = parseInt(teacherPageInput.value);
    if(!socket || isNaN(p) || p < 1) return;
    socket.send(JSON.stringify({type:'goto_page', page: p}));
  });

  clearAnnotationsBtn.addEventListener('click', () => {
    if(!socket) return;
    socket.send(JSON.stringify({type:'clear_annotations'}));
  });

  // ---------- Annotation drawing ----------
  function getCanvasPoint(e){
    const rect = annoCanvas.getBoundingClientRect();
    return {x: (e.clientX - rect.left), y: (e.clientY - rect.top)};
  }

  annoCanvas.addEventListener('pointerdown', (e) => {
    if(!allowedToAnnotate) return;
    isDrawing = true;
    currentStroke = {id: Math.random().toString(36).slice(2,9), page: currentPage, points: [getCanvasPoint(e)], color: '#ff0000', width: 3, author: 'me'};
    e.target.setPointerCapture(e.pointerId);
  });

  annoCanvas.addEventListener('pointermove', (e) => {
    if(!isDrawing || !currentStroke) return;
    currentStroke.points.push(getCanvasPoint(e));
    redrawAnnotations();
    drawStrokeOnCtx(ctxAnno, currentStroke);
  });

  annoCanvas.addEventListener('pointerup', (e) => {
    if(!isDrawing) return;
    isDrawing = false;
    if(currentStroke && currentStroke.points.length > 0){
      appliedStrokes.push(currentStroke);
      if(socket) socket.send(JSON.stringify({type:'stroke', stroke: currentStroke}));
      currentStroke = null;
      redrawAnnotations();
    }
  });

  function drawStrokeOnCtx(ctx, s){
    if(!s || !s.points || s.points.length === 0) return;
    ctx.strokeStyle = s.color || '#ff0000';
    ctx.lineWidth = s.width || 3;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(s.points[0].x, s.points[0].y);
    for(let i=1;i<s.points.length;i++) ctx.lineTo(s.points[i].x, s.points[i].y);
    ctx.stroke();
  }

  function redrawAnnotations(){
    ctxAnno.clearRect(0,0,annoCanvas.width,annoCanvas.height);
    for(const s of appliedStrokes){
      if(s.page !== currentPage) continue;
      drawStrokeOnCtx(ctxAnno, s);
    }
  }

  // ---------- PDF rendering ----------
  async function loadPdf(url){
    statusEl.textContent = 'Loading PDF...';
    pdfDoc = await pdfjsLib.getDocument(url).promise;
    currentPage = 1;
    teacherPageInput.value = 1;
    await renderPage(1);
    statusEl.textContent = 'PDF loaded (' + pdfDoc.numPages + ' pages)';
  }

  async function renderPage(pageNum){
    if(!pdfDoc) return;
    const page = await pdfDoc.getPage(pageNum);
    // choose scale so page fits height
    const viewer = document.querySelector('.left');
    const fitScale = (viewer.clientHeight - 0) / page.getViewport({scale:1}).height;
    const renderScale = Math.max(1.0, 1.2 * fitScale);
    const vp = page.getViewport({scale: renderScale});
    pdfCanvas.width = Math.floor(vp.width * devicePixelRatio);
    pdfCanvas.height = Math.floor(vp.height * devicePixelRatio);
    pdfCanvas.style.width = vp.width + "px";
    pdfCanvas.style.height = vp.height + "px";
    pdfCanvas.getContext('2d').setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
    await page.render({canvasContext: ctxPdf, viewport: vp}).promise;

    // match anno canvas
    annoCanvas.width = pdfCanvas.width;
    annoCanvas.height = pdfCanvas.height;
    annoCanvas.style.width = pdfCanvas.style.width;
    annoCanvas.style.height = pdfCanvas.style.height;
    annoCanvas.getContext('2d').setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);

    redrawAnnotations();
  }

  // Initialize: show role selection panel (already visible)
})();
</script>
</body>
</html>
"""

async def index(request):
    return web.Response(text=INDEX_HTML, content_type='text/html')

# Upload handler
async def upload_pdf(request):
    data = await request.post()
    pdf = data.get('pdf')
    if not pdf:
        return web.json_response({'ok': False, 'error': 'no-file'})
    filename = f"{uuid.uuid4().hex}.pdf"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, 'wb') as fout:
        fout.write(pdf.file.read())
    class_id = new_class_id()
    teacher_key = new_teacher_key()
    classes[class_id] = {
        "teacher_id": None,
        "teacher_key": teacher_key,
        "pdf_filename": filename,
        "clients": {},
        "pending": {},
        "allowed": {}
    }
    return web.json_response({'ok': True, 'class_id': class_id, 'teacher_key': teacher_key, 'pdf_url': f"/files/{filename}"})

# Serve uploaded PDFs
async def serve_file(request):
    fname = request.match_info['filename']
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path)

# WebSocket handler
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_id = str(uuid.uuid4())
    joined_class = None

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    await ws.send_str(json.dumps({'type': 'error', 'error': 'invalid-json'}))
                    continue

                typ = data.get('type')
                if typ == 'join':
                    role = data.get('role')
                    name = data.get('name') or f"User-{client_id[:6]}"
                    class_id = data.get('class_id')
                    key = data.get('key')
                    if not class_id or class_id not in classes:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'invalid-class'}))
                        continue
                    room = classes[class_id]
                    # teacher join validation
                    if role == 'teacher':
                        if key != room['teacher_key']:
                            await ws.send_str(json.dumps({'type': 'error', 'error': 'invalid-teacher-key'}))
                            continue
                        if room['teacher_id'] is not None:
                            await ws.send_str(json.dumps({'type': 'error', 'error': 'teacher-already-connected'}))
                            continue
                        room['teacher_id'] = client_id
                    # register client
                    room['clients'][client_id] = {'ws': ws, 'name': name, 'role': role}
                    joined_class = class_id
                    # ack
                    await ws.send_str(json.dumps({'type': 'joined', 'id': client_id, 'role': role, 'class_id': class_id, 'pdf_url': f"/files/{room['pdf_filename']}", 'name': name, 'teacher_key': room.get('teacher_key') if role=='teacher' else None}))
                    # broadcast presence
                    clients_list = [{'id': cid, 'name': info['name'], 'role': info['role']} for cid, info in room['clients'].items()]
                    await broadcast_class(class_id, {'type': 'presence', 'clients': clients_list})
                    # send pending list to teacher
                    if role == 'teacher':
                        pending_list = [{'request_id': rid, 'name': v['name'], 'page': v['page'], 'note': v['note']} for rid, v in room['pending'].items()]
                        await ws.send_str(json.dumps({'type': 'pending_list', 'pending': pending_list}))

                elif typ == 'request_annotate':
                    # student requests permission
                    class_id = None
                    for cid, cls in classes.items():
                        if client_id in cls['clients']:
                            class_id = cid; break
                    if not class_id:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-in-class'})); continue
                    room = classes[class_id]
                    page = int(data.get('page', 1))
                    note = data.get('note', '')
                    reqid = str(uuid.uuid4())
                    room['pending'][reqid] = {'student_id': client_id, 'page': page, 'note': note, 'name': room['clients'][client_id]['name']}
                    # notify teacher if present
                    if room['teacher_id'] and room['teacher_id'] in room['clients']:
                        try:
                            await room['clients'][room['teacher_id']]['ws'].send_str(json.dumps({'type': 'pending_new', 'request_id': reqid, 'name': room['clients'][client_id]['name'], 'page': page, 'note': note}))
                        except Exception:
                            pass
                    # ack to student
                    await ws.send_str(json.dumps({'type': 'info', 'message': 'request_created'}))

                elif typ == 'approve':
                    # teacher approves
                    class_id = None
                    for cid, cls in classes.items():
                        if client_id in cls['clients']:
                            class_id = cid; break
                    if not class_id:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-in-class'})); continue
                    room = classes[class_id]
                    if room['clients'][client_id]['role'] != 'teacher':
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-teacher'})); continue
                    reqid = data.get('request_id')
                    req = room['pending'].pop(reqid, None)
                    if not req:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'unknown-request'})); continue
                    student_id = req['student_id']
                    room['allowed'][student_id] = True
                    # notify student
                    if student_id in room['clients']:
                        try:
                            await room['clients'][student_id]['ws'].send_str(json.dumps({'type': 'request_result', 'result': 'approved', 'page': req['page']}))
                        except Exception:
                            pass
                    await broadcast_class(class_id, {'type': 'info', 'message': f"{req['name']} approved to annotate page {req['page']}."})

                elif typ == 'deny':
                    # teacher denies
                    class_id = None
                    for cid, cls in classes.items():
                        if client_id in cls['clients']:
                            class_id = cid; break
                    if not class_id:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-in-class'})); continue
                    room = classes[class_id]
                    if room['clients'][client_id]['role'] != 'teacher':
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-teacher'})); continue
                    reqid = data.get('request_id')
                    req = room['pending'].pop(reqid, None)
                    if not req:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'unknown-request'})); continue
                    student_id = req['student_id']
                    if student_id in room['clients']:
                        try:
                            await room['clients'][student_id]['ws'].send_str(json.dumps({'type': 'request_result', 'result': 'denied', 'page': req['page']}))
                        except Exception:
                            pass

                elif typ == 'stroke':
                    # a student (allowed) sends stroke
                    class_id = None
                    for cid, cls in classes.items():
                        if client_id in cls['clients']:
                            class_id = cid; break
                    if not class_id:
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-in-class'})); continue
                    room = classes[class_id]
                    if not room['allowed'].get(client_id, False):
                        await ws.send_str(json.dumps({'type': 'error', 'error': 'not-allowed-to-annotate'})); continue
                    stroke = data.get('stroke')
                    await broadcast_class(class_id, {'type': 'apply_stroke', 'stroke': stroke})

                elif typ == 'clear_annotations':
                    # teacher clears
                    class_id = None
                    for cid, cls in classes.items():
                        if client_id in cls['clients']:
                            class_id = cid; break
                    if not class_id: continue
                    room = classes[class_id]
                    if room['clients'][client_id]['role'] != 'teacher':
                        await ws.send_str(json.dumps({'type':'error','error':'not-teacher'})); continue
                    room['allowed'].clear()
                    await broadcast_class(class_id, {'type': 'clear_annotations'})

                elif typ == 'goto_page':
                    # teacher instructs page change
                    class_id = None
                    for cid, cls in classes.items():
                        if client_id in cls['clients']:
                            class_id = cid; break
                    if not class_id: continue
                    room = classes[class_id]
                    if room['clients'][client_id]['role'] != 'teacher':
                        await ws.send_str(json.dumps({'type':'error','error':'not-teacher'})); continue
                    page = int(data.get('page', 1))
                    await broadcast_class(class_id, {'type':'goto_page', 'page': page})

                else:
                    await ws.send_str(json.dumps({'type':'error','error':'unknown-type'}))

            elif msg.type == WSMsgType.ERROR:
                print('ws error', ws.exception())
    finally:
        # cleanup
        if joined_class and client_id in classes.get(joined_class, {}).get('clients', {}):
            room = classes[joined_class]
            role = room['clients'][client_id]['role']
            room['clients'].pop(client_id, None)
            if role == 'teacher':
                room['teacher_id'] = None
                await broadcast_class(joined_class, {'type':'info', 'message':'Teacher disconnected; pending requests will remain.'})
            clients_list = [{'id': cid, 'name': info['name'], 'role': info['role']} for cid, info in room['clients'].items()]
            await broadcast_class(joined_class, {'type':'presence', 'clients': clients_list})
    return ws

# App setup
app = web.Application()
app.router.add_get('/', index)
app.router.add_post('/upload', upload_pdf)
app.router.add_get('/ws', websocket_handler)
app.router.add_get('/files/{filename}', serve_file)
app.router.add_static('/files/', UPLOAD_DIR, show_index=False)

if __name__ == '__main__':
    print("Starting server on http://0.0.0.0:8080")
    web.run_app(app, host='0.0.0.0', port=8080)
