# app.py
"""
Persistent PDF annotator with reconnect-safe student tokens and teacher key.
Added endpoints:
 - clear_my_annotations (student): removes strokes authored by that student
 - clear_teacher_annotations (teacher): removes strokes authored by teacher
Run:
    pip install aiohttp
    python app.py
"""
import json
import os
import secrets
import string
import uuid
from aiohttp import web, WSMsgType

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Persistent classes state
classes = {}
# Transient clients map
clients = {}

# ---------------- Persistence helpers ----------------
def load_state():
    global classes
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                classes = json.load(f)
        except Exception as e:
            print("Failed to load state:", e)
            classes = {}
    else:
        classes = {}

def save_state():
    try:
        with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(classes, f, indent=2)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except Exception as e:
        print("Failed to save state:", e)

# ---------------- Utilities ----------------
def new_class_id():
    return secrets.token_urlsafe(6)

def new_teacher_key():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))

def new_student_token():
    return secrets.token_urlsafe(8)

async def send_json(ws, payload):
    try:
        await ws.send_str(json.dumps(payload))
    except Exception:
        pass

async def broadcast_class(class_id, payload):
    data = json.dumps(payload)
    for cid, info in list(clients.items()):
        if info.get("class_id") == class_id:
            try:
                await info["ws"].send_str(data)
            except Exception:
                pass

# ---------------- HTTP endpoints ----------------
INDEX_HTML = os.path.join(BASE_DIR, "static", "index.html")

async def index(request):
    return web.FileResponse(INDEX_HTML)

async def upload_pdf(request):
    data = await request.post()
    pdf = data.get("pdf")
    if not pdf:
        return web.json_response({"ok": False, "error": "no-file"})
    filename = f"{uuid.uuid4().hex}.pdf"
    outpath = os.path.join(UPLOAD_DIR, filename)
    with open(outpath, "wb") as fout:
        fout.write(pdf.file.read())
    class_id = new_class_id()
    teacher_key = new_teacher_key()
    classes[class_id] = {
        "teacher_key": teacher_key,
        "pdf_filename": filename,
        "students": {},
        "pending": {},
        "strokes": {},
        "last_student_annotator": None,
        "current_annotator": None
    }
    save_state()
    return web.json_response({"ok": True, "class_id": class_id, "teacher_key": teacher_key, "pdf_url": f"/files/{filename}"})

async def serve_file(request):
    fname = request.match_info["filename"]
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path)

# ---------------- WebSocket handler ----------------
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_id = str(uuid.uuid4())
    clients[client_id] = {"ws": ws, "class_id": None, "role": None, "name": None, "token": None}

    try:
        async for raw in ws:
            if raw.type == WSMsgType.TEXT:
                try:
                    data = json.loads(raw.data)
                except Exception:
                    await send_json(ws, {"type":"error","error":"invalid-json"})
                    continue

                typ = data.get("type")

                # ---------- JOIN ----------
                if typ == "join":
                    role = data.get("role")
                    class_id = data.get("class_id")
                    if not class_id or class_id not in classes:
                        await send_json(ws, {"type":"error","error":"invalid-class"}); continue
                    room = classes[class_id]

                    if role == "teacher":
                        key = data.get("key")
                        if key != room.get("teacher_key"):
                            await send_json(ws, {"type":"error","error":"invalid-teacher-key"}); continue
                        name = data.get("name") or "Teacher"
                        clients[client_id].update({"class_id": class_id, "role": "teacher", "name": name, "token": "teacher"})
                        await send_json(ws, {"type":"joined","id": client_id, "role":"teacher", "class_id": class_id, "pdf_url": f"/files/{room['pdf_filename']}", "teacher_key": room.get("teacher_key"), "name": name})
                    elif role == "student":
                        name = data.get("name") or f"Student-{client_id[:6]}"
                        provided_token = data.get("student_token")
                        token = None
                        if provided_token and provided_token in room.get("students", {}):
                            token = provided_token
                            room["students"][token]["name"] = name
                        else:
                            token = new_student_token()
                            room.setdefault("students", {})[token] = {"name": name, "allowed": False}
                        clients[client_id].update({"class_id": class_id, "role": "student", "name": name, "token": token})
                        await send_json(ws, {"type":"joined", "id": client_id, "role":"student", "class_id": class_id, "pdf_url": f"/files/{room['pdf_filename']}", "student_token": token, "name": name})
                        save_state()
                    else:
                        await send_json(ws, {"type":"error","error":"unknown-role"}); continue

                    # broadcast presence
                    participants = []
                    for cid, info in clients.items():
                        if info.get("class_id") == class_id and info.get("name"):
                            participants.append({"id": cid, "name": info.get("name"), "role": info.get("role")})
                    await broadcast_class(class_id, {"type":"presence","clients": participants})

                    # send pending to teacher
                    if clients[client_id]["role"] == "teacher":
                        pend = []
                        for rid, r in room.get("pending", {}).items():
                            pend.append({"request_id": rid, "name": room["students"].get(r["student_token"], {}).get("name"), "page": r["page"], "note": r.get("note","")})
                        await send_json(ws, {"type":"pending_list","pending": pend})

                    # send persisted strokes
                    await send_json(ws, {"type":"init_strokes", "strokes": room.get("strokes", {})})

                    # send annotator status
                    annot = room.get("current_annotator")
                    annot_name = None
                    if annot == "teacher":
                        annot_name = "Teacher"
                    elif annot:
                        annot_name = room.get("students", {}).get(annot, {}).get("name")
                    await send_json(ws, {"type":"annotator_update", "current_annotator": annot, "annotator_name": annot_name})
                    continue

                # ---------- REQUEST ANNOTATE ----------
                if typ == "request_annotate":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id:
                        await send_json(ws, {"type":"error","error":"not-in-class"}); continue
                    room = classes[class_id]
                    student_token = info.get("token")
                    page = int(data.get("page", 1))
                    note = data.get("note", "")
                    reqid = str(uuid.uuid4())
                    room.setdefault("pending", {})[reqid] = {"student_token": student_token, "page": page, "note": note}
                    save_state()
                    await broadcast_class(class_id, {"type":"pending_new", "request_id": reqid, "name": room["students"][student_token]["name"], "page": page, "note": note})
                    await send_json(ws, {"type":"info", "message":"request_created"})
                    continue

                # ---------- APPROVE ----------
                if typ == "approve":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    room = classes[class_id]
                    reqid = data.get("request_id")
                    req = room.get("pending", {}).pop(reqid, None)
                    if not req:
                        await send_json(ws, {"type":"error","error":"unknown-request"}); continue
                    student_token = req["student_token"]
                    room.setdefault("students", {}).setdefault(student_token, {"name":"Unknown", "allowed": True})
                    room["students"][student_token]["allowed"] = True
                    room["current_annotator"] = student_token
                    room["last_student_annotator"] = student_token
                    save_state()
                    for cid, cinfo in clients.items():
                        if cinfo.get("class_id") == class_id and cinfo.get("token") == student_token:
                            try:
                                await cinfo["ws"].send_str(json.dumps({"type":"request_result","result":"approved","page": req["page"]}))
                            except Exception:
                                pass
                    await broadcast_class(class_id, {"type":"annotator_update", "current_annotator": student_token, "annotator_name": room["students"][student_token]["name"]})
                    await broadcast_class(class_id, {"type":"info", "message": f"{room['students'][student_token]['name']} approved to annotate page {req['page']}."})
                    continue

                # ---------- DENY ----------
                if typ == "deny":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    room = classes[class_id]
                    reqid = data.get("request_id")
                    req = room.get("pending", {}).pop(reqid, None)
                    if not req:
                        await send_json(ws, {"type":"error","error":"unknown-request"}); continue
                    student_token = req["student_token"]
                    save_state()
                    for cid, cinfo in clients.items():
                        if cinfo.get("class_id") == class_id and cinfo.get("token") == student_token:
                            try:
                                await cinfo["ws"].send_str(json.dumps({"type":"request_result","result":"denied","page": req["page"]}))
                            except Exception:
                                pass
                    continue

                # ---------- REVOKE ----------
                if typ == "revoke":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    room = classes[class_id]
                    sid = data.get("student_token")
                    if sid:
                        if room.get("current_annotator") == sid:
                            room["current_annotator"] = None
                    else:
                        room["current_annotator"] = None
                    save_state()
                    await broadcast_class(class_id, {"type":"annotator_update", "current_annotator": room.get("current_annotator"), "annotator_name": (room["students"].get(room.get("current_annotator"),{}).get("name") if room.get("current_annotator") else None)})
                    await broadcast_class(class_id, {"type":"info","message":"Annotation stopped by teacher."})
                    continue

                # ---------- STROKE ----------
                if typ == "stroke":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id:
                        await send_json(ws, {"type":"error","error":"not-in-class"}); continue
                    room = classes[class_id]
                    role = info.get("role")
                    if role == "teacher":
                        author = "teacher"
                    else:
                        student_token = info.get("token")
                        if room.get("students", {}).get(student_token, {}).get("allowed") != True or room.get("current_annotator") != student_token:
                            await send_json(ws, {"type":"error","error":"not-allowed-to-annotate"}); continue
                        author = student_token
                        room["last_student_annotator"] = student_token
                    stroke = data.get("stroke")
                    if not stroke:
                        await send_json(ws, {"type":"error","error":"missing-stroke"}); continue
                    page = str(stroke.get("page", "1"))
                    entry = {"author": author, "color": stroke.get("color", "#ff0000"), "width": stroke.get("width", 3), "points": stroke.get("points", [])}
                    room.setdefault("strokes", {}).setdefault(page, []).append(entry)
                    save_state()
                    await broadcast_class(class_id, {"type":"apply_stroke", "stroke": {"page": page, "author": author, "color": entry["color"], "width": entry["width"], "points": entry["points"]}})
                    continue

                # ---------- CLEAR MY ANNOTATIONS (student) ----------
                if typ == "clear_my_annotations":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "student":
                        await send_json(ws, {"type":"error","error":"not-student"}); continue
                    room = classes[class_id]
                    my_token = info.get("token")
                    new_strokes = {}
                    for page, lst in room.get("strokes", {}).items():
                        filtered = [s for s in lst if s.get("author") != my_token]
                        if filtered:
                            new_strokes[page] = filtered
                    room["strokes"] = new_strokes
                    # if last/current annotator was this student, clear those references
                    if room.get("last_student_annotator") == my_token:
                        room["last_student_annotator"] = None
                    if room.get("current_annotator") == my_token:
                        room["current_annotator"] = None
                    save_state()
                    await broadcast_class(class_id, {"type":"init_strokes","strokes": room.get("strokes", {})})
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": room.get("current_annotator"), "annotator_name": (room["students"].get(room.get("current_annotator"),{}).get("name") if room.get("current_annotator") else None)})
                    await send_json(ws, {"type":"info","message":"Your annotations cleared."})
                    continue

                # ---------- CLEAR TEACHER ANNOTATIONS (teacher) ----------
                if typ == "clear_teacher_annotations":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    room = classes[class_id]
                    new_strokes = {}
                    for page, lst in room.get("strokes", {}).items():
                        filtered = [s for s in lst if s.get("author") != "teacher"]
                        if filtered:
                            new_strokes[page] = filtered
                    room["strokes"] = new_strokes
                    save_state()
                    await broadcast_class(class_id, {"type":"init_strokes","strokes": room.get("strokes", {})})
                    await broadcast_class(class_id, {"type":"info","message":"Teacher annotations cleared (students preserved)."})
                    continue

                # ---------- CLEAR STUDENT ANNOTATIONS (last student) ----------
                if typ == "clear_student_annotations":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    room = classes[class_id]
                    target = room.get("last_student_annotator")
                    if not target:
                        await send_json(ws, {"type":"info","message":"No student annotations to clear."}); continue
                    new_strokes = {}
                    for page, lst in room.get("strokes", {}).items():
                        filtered = [s for s in lst if s.get("author") != target]
                        if filtered:
                            new_strokes[page] = filtered
                    room["strokes"] = new_strokes
                    room["last_student_annotator"] = None
                    if room.get("current_annotator") == target:
                        room["current_annotator"] = None
                    save_state()
                    await broadcast_class(class_id, {"type":"init_strokes","strokes": room.get("strokes", {})})
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": room.get("current_annotator"), "annotator_name": (room["students"].get(room.get("current_annotator"),{}).get("name") if room.get("current_annotator") else None)})
                    await broadcast_class(class_id, {"type":"info","message":"Cleared annotations made by last student annotator (teacher annotations preserved)."})
                    continue

                # ---------- CLEAR ALL ----------
                if typ == "clear_annotations":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    room = classes[class_id]
                    room["strokes"] = {}
                    room["last_student_annotator"] = None
                    room["current_annotator"] = None
                    save_state()
                    await broadcast_class(class_id, {"type":"clear_annotations"})
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": None, "annotator_name": None})
                    continue

                # ---------- GOTO PAGE ----------
                if typ == "goto_page":
                    info = clients[client_id]
                    class_id = info.get("class_id")
                    if not class_id or info.get("role") != "teacher":
                        await send_json(ws, {"type":"error","error":"not-teacher"}); continue
                    page = int(data.get("page", 1))
                    await broadcast_class(class_id, {"type":"goto_page", "page": page})
                    continue

                await send_json(ws, {"type":"error","error":"unknown-type"})
            elif raw.type == WSMsgType.ERROR:
                print("WS error:", raw)
    finally:
        info = clients.pop(client_id, None)
        if info and info.get("class_id"):
            cid = info["class_id"]
            participants = []
            for ccid, cinfo in clients.items():
                if cinfo.get("class_id") == cid and cinfo.get("name"):
                    participants.append({"id": ccid, "name": cinfo.get("name"), "role": cinfo.get("role")})
            await broadcast_class(cid, {"type":"presence","clients": participants})
    return ws

# ---------------- App setup ----------------
load_state()
app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/upload", upload_pdf)
app.router.add_get("/ws", websocket_handler)
app.router.add_get("/files/{filename}", serve_file)
app.router.add_static("/static/", path=os.path.join(BASE_DIR, "static"), show_index=False)
app.router.add_static("/", os.path.join(BASE_DIR, "static"), show_index=False)

if __name__ == "__main__":
    print("Server running on http://0.0.0.0:8080")
    web.run_app(app, host="0.0.0.0", port=8080)
