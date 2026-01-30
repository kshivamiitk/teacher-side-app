# app.py
"""
Persistent, scrollable PDF class annotator with teacher-approved annotations.
Clear now removes only the last student's annotations (not teacher's).
Run:
    pip install aiohttp
    python app.py
Open:
    http://localhost:8080
"""
import asyncio
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
# transient clients and annotator tracking
clients = {}
current_annotator = {}  # class_id -> client_id (student) or None

# ----------------------
# Persistence helpers
# ----------------------
def load_state():
    global classes
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                classes = json.load(f)
        except Exception as e:
            print("Failed to load state.json:", e)
            classes = {}
    else:
        classes = {}

def save_state():
    try:
        with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(classes, f, indent=2)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except Exception as e:
        print("Failed to save state.json:", e)

# ----------------------
# Utilities
# ----------------------
def new_class_id():
    return secrets.token_urlsafe(6)

def new_teacher_key():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))

async def broadcast_class(class_id, message, only=None):
    data = json.dumps(message)
    if only is None:
        targets = [ (cid, info) for cid, info in clients.items() if info.get("class_id") == class_id ]
    else:
        targets = [(cid, clients[cid]) for cid in only if cid in clients]
    for cid, info in targets:
        try:
            await info["ws"].send_str(data)
        except Exception:
            pass

# ----------------------
# HTTP endpoints
# ----------------------
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
        "pending": {},
        "strokes": {},  # page -> [ {author,color,width,points} ... ]
        "last_student_annotator": None
    }
    save_state()
    return web.json_response({"ok": True, "class_id": class_id, "teacher_key": teacher_key, "pdf_url": f"/files/{filename}"})

async def serve_file(request):
    fname = request.match_info["filename"]
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path)

# ----------------------
# WebSocket handler
# ----------------------
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_id = str(uuid.uuid4())
    client_info = {"ws": ws, "name": None, "role": None, "class_id": None}
    clients[client_id] = client_info

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    await ws.send_str(json.dumps({"type":"error","error":"invalid-json"}))
                    continue

                typ = data.get("type")

                # -------- JOIN --------
                if typ == "join":
                    role = data.get("role")
                    name = data.get("name") or f"User-{client_id[:6]}"
                    class_id = data.get("class_id")
                    key = data.get("key")
                    if not class_id or class_id not in classes:
                        await ws.send_str(json.dumps({"type":"error","error":"invalid-class"})); continue
                    room = classes[class_id]
                    if role == "teacher":
                        if key != room["teacher_key"]:
                            await ws.send_str(json.dumps({"type":"error","error":"invalid-teacher-key"})); continue
                    client_info.update({"name": name, "role": role, "class_id": class_id})
                    # ack with persistent strokes
                    await ws.send_str(json.dumps({
                        "type":"joined",
                        "id": client_id,
                        "role": role,
                        "class_id": class_id,
                        "pdf_url": f"/files/{room['pdf_filename']}",
                        "name": name,
                        "teacher_key": room["teacher_key"] if role == "teacher" else None
                    }))
                    # presence
                    clients_list = [
                        {"id": cid, "name": info["name"], "role": info["role"]}
                        for cid, info in clients.items() if info.get("class_id") == class_id and info.get("name")
                    ]
                    await broadcast_class(class_id, {"type":"presence","clients":clients_list})
                    # send pending to teacher
                    if role == "teacher":
                        pend = [{"request_id": rid, "name": v["name"], "page": v["page"], "note": v["note"]} for rid, v in room.get("pending", {}).items()]
                        await ws.send_str(json.dumps({"type":"pending_list","pending": pend}))
                    # send persisted strokes
                    await ws.send_str(json.dumps({"type":"init_strokes","strokes": room.get("strokes", {})}))
                    # send annotator_update
                    annot = current_annotator.get(class_id)
                    annot_name = clients.get(annot, {}).get("name") if annot else None
                    await ws.send_str(json.dumps({"type":"annotator_update","current_annotator": annot, "annotator_name": annot_name}))
                    continue

                # -------- REQUEST ANNOTATE --------
                if typ == "request_annotate":
                    if not client_info.get("class_id"):
                        await ws.send_str(json.dumps({"type":"error","error":"not-in-class"})); continue
                    class_id = client_info["class_id"]
                    room = classes[class_id]
                    page = int(data.get("page", 1))
                    note = data.get("note", "")
                    reqid = str(uuid.uuid4())
                    room.setdefault("pending", {})[reqid] = {"student_id": client_id, "page": page, "note": note, "name": client_info["name"]}
                    save_state()
                    await broadcast_class(class_id, {"type":"pending_new","request_id": reqid, "name": client_info["name"], "page": page, "note": note})
                    await ws.send_str(json.dumps({"type":"info","message":"request_created"}))
                    continue

                # -------- APPROVE --------
                if typ == "approve":
                    if not client_info.get("class_id"):
                        await ws.send_str(json.dumps({"type":"error","error":"not-in-class"})); continue
                    class_id = client_info["class_id"]
                    if client_info.get("role") != "teacher":
                        await ws.send_str(json.dumps({"type":"error","error":"not-teacher"})); continue
                    room = classes[class_id]
                    reqid = data.get("request_id")
                    req = room.get("pending", {}).pop(reqid, None)
                    if not req:
                        await ws.send_str(json.dumps({"type":"error","error":"unknown-request"})); continue
                    student_id = req["student_id"]
                    current_annotator[class_id] = student_id
                    # notify approved student
                    if student_id in clients:
                        try:
                            await clients[student_id]["ws"].send_str(json.dumps({"type":"request_result","result":"approved","page": req["page"]}))
                        except Exception:
                            pass
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": student_id, "annotator_name": clients.get(student_id, {}).get("name")})
                    save_state()
                    continue

                # -------- DENY --------
                if typ == "deny":
                    if not client_info.get("class_id"):
                        await ws.send_str(json.dumps({"type":"error","error":"not-in-class"})); continue
                    if client_info.get("role") != "teacher":
                        await ws.send_str(json.dumps({"type":"error","error":"not-teacher"})); continue
                    class_id = client_info["class_id"]
                    room = classes[class_id]
                    reqid = data.get("request_id")
                    req = room.get("pending", {}).pop(reqid, None)
                    if not req:
                        await ws.send_str(json.dumps({"type":"error","error":"unknown-request"})); continue
                    student_id = req["student_id"]
                    if student_id in clients:
                        try:
                            await clients[student_id]["ws"].send_str(json.dumps({"type":"request_result","result":"denied","page":req["page"]}))
                        except Exception:
                            pass
                    save_state()
                    continue

                # -------- REVOKE --------
                if typ == "revoke":
                    if not client_info.get("class_id"):
                        await ws.send_str(json.dumps({"type":"error","error":"not-in-class"})); continue
                    if client_info.get("role") != "teacher":
                        await ws.send_str(json.dumps({"type":"error","error":"not-teacher"})); continue
                    class_id = client_info["class_id"]
                    sid = data.get("student_id")
                    if sid:
                        if current_annotator.get(class_id) == sid:
                            current_annotator[class_id] = None
                    else:
                        current_annotator[class_id] = None
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": current_annotator.get(class_id), "annotator_name": clients.get(current_annotator.get(class_id), {}).get("name") if current_annotator.get(class_id) else None})
                    await broadcast_class(class_id, {"type":"info","message":"Annotation stopped by teacher."})
                    save_state()
                    continue

                # -------- STROKE --------
                if typ == "stroke":
                    if not client_info.get("class_id"):
                        await ws.send_str(json.dumps({"type":"error","error":"not-in-class"})); continue
                    class_id = client_info["class_id"]
                    room = classes[class_id]
                    # teacher can always draw
                    if client_info.get("role") != "teacher":
                        if current_annotator.get(class_id) != client_id:
                            await ws.send_str(json.dumps({"type":"error","error":"not-current-annotator"})); continue
                    stroke = data.get("stroke")
                    if not stroke:
                        await ws.send_str(json.dumps({"type":"error","error":"missing-stroke"})); continue
                    page = str(stroke.get("page", 1))
                    entry = {
                        "author": client_id,
                        "color": stroke.get("color", "#ff0000"),
                        "width": stroke.get("width", 3),
                        "points": stroke.get("points", [])
                    }
                    room.setdefault("strokes", {}).setdefault(page, []).append(entry)
                    # Track last student who drew (persistent)
                    if client_info.get("role") != "teacher":
                        room["last_student_annotator"] = client_id
                    save_state()
                    await broadcast_class(class_id, {"type":"apply_stroke","stroke": {"page": page, "author": client_id, "color": entry["color"], "width": entry["width"], "points": entry["points"]}})
                    continue

                # -------- CLEAR STUDENT ANNOTATIONS (teacher) --------
                if typ == "clear_student_annotations":
                    if not client_info.get("class_id"):
                        await ws.send_str(json.dumps({"type":"error","error":"not-in-class"})); continue
                    if client_info.get("role") != "teacher":
                        await ws.send_str(json.dumps({"type":"error","error":"not-teacher"})); continue
                    class_id = client_info["class_id"]
                    room = classes[class_id]
                    target = room.get("last_student_annotator")
                    if not target:
                        # nothing to clear
                        await ws.send_str(json.dumps({"type":"info","message":"No student annotations to clear."})); continue
                    # remove strokes authored by target, keep teacher strokes and others
                    new_strokes = {}
                    for page, lst in room.get("strokes", {}).items():
                        filtered = [s for s in lst if s.get("author") != target]
                        if filtered:
                            new_strokes[page] = filtered
                    room["strokes"] = new_strokes
                    # clear last_student_annotator (we removed their strokes)
                    room["last_student_annotator"] = None
                    # if current_annotator equals target, remove it
                    if current_annotator.get(class_id) == target:
                        current_annotator[class_id] = None
                    save_state()
                    # broadcast updated strokes to clients (init_strokes will replace client's appliedStrokes)
                    await broadcast_class(class_id, {"type":"init_strokes","strokes": room.get("strokes", {})})
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": current_annotator.get(class_id), "annotator_name": clients.get(current_annotator.get(class_id), {}).get("name") if current_annotator.get(class_id) else None})
                    await broadcast_class(class_id, {"type":"info","message":"Cleared annotations made by the last student (teacher annotations preserved)."})
                    continue

                # -------- CLEAR ALL (kept for backward compatibility) --------
                if typ == "clear_annotations":
                    if not client_info.get("class_id"):
                        continue
                    if client_info.get("role") != "teacher":
                        await ws.send_str(json.dumps({"type":"error","error":"not-teacher"})); continue
                    class_id = client_info["class_id"]
                    classes[class_id]["strokes"] = {}
                    classes[class_id]["last_student_annotator"] = None
                    current_annotator[class_id] = None
                    save_state()
                    await broadcast_class(class_id, {"type":"clear_annotations"})
                    await broadcast_class(class_id, {"type":"annotator_update","current_annotator": None, "annotator_name": None})
                    continue

                # -------- GOTO PAGE --------
                if typ == "goto_page":
                    if not client_info.get("class_id"):
                        continue
                    if client_info.get("role") != "teacher":
                        await ws.send_str(json.dumps({"type":"error","error":"not-teacher"})); continue
                    page = int(data.get("page",1))
                    await broadcast_class(client_info["class_id"], {"type":"goto_page","page": page})
                    continue

                await ws.send_str(json.dumps({"type":"error","error":"unknown-type"}))

            elif msg.type == WSMsgType.ERROR:
                print("WS error:", ws.exception())

    finally:
        # cleanup on disconnect
        info = clients.pop(client_id, None)
        if info and info.get("class_id"):
            cid = info["class_id"]
            if current_annotator.get(cid) == client_id:
                current_annotator[cid] = None
                await broadcast_class(cid, {"type":"annotator_update","current_annotator": None, "annotator_name": None})
            clients_list = [
                {"id": ccid, "name": cinfo["name"], "role": cinfo["role"]}
                for ccid, cinfo in clients.items() if cinfo.get("class_id") == cid
            ]
            await broadcast_class(cid, {"type":"presence","clients":clients_list})
    return ws

# ----------------------
# App setup
# ----------------------
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
