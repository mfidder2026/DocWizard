"""DocWizard engine HTTP API (see docs/CONTRACTS.md)."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..config import Connection, Settings, app_paths, load_settings, save_settings
from ..llm.client import LLMClient
from ..platform import open_with_default_app, reveal_in_finder
from ..store import db, repo
from ..workflows import bus, runner
from ..workflows.common import file_hash, project_dir, safe_name
from ..workflows.diagram import diagram_dir
from ..wordengine.preview import to_html
from ..wordengine.profile import analyze_template
from ..wordengine.reader import DocModel

WEB = Path(__file__).resolve().parent.parent / "web"
TOKEN = os.environ.get("DOCWIZARD_TOKEN") or secrets.token_urlsafe(24)
ACTIONS = {"auto", "chat", "new_document", "add_chapter", "translate", "correct", "review", "enrich", "diagram",
           "presentation"}
MIME = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.set_db_path(app_paths().db)
    runner.start()
    yield
    await runner.stop()


app = FastAPI(title="DocWizard", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        token = request.headers.get("X-DocWizard-Token") or request.query_params.get("token")
        if token != TOKEN:
            return JSONResponse({"detail": "Niet geautoriseerd"}, status_code=401)
    return await call_next(request)


@app.exception_handler(KeyError)
async def not_found(_: Request, exc: KeyError):
    return JSONResponse({"detail": f"Niet gevonden: {exc}"}, status_code=404)


def _need(obj, what="item"):
    if not obj:
        raise HTTPException(404, f"{what} niet gevonden")
    return obj


# ---- UI ------------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__DOCWIZARD_TOKEN__", TOKEN), headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}


# ---- settings & connections ------------------------------------------------------------------

@app.get("/api/settings")
async def get_settings():
    return load_settings().model_dump()


@app.put("/api/settings")
async def put_settings(body: dict):
    s = Settings.model_validate(body)
    save_settings(s)
    return s.model_dump()


@app.post("/api/connections/test")
async def test_connection(body: dict):
    try:
        conn = Connection.model_validate({"id": body.get("id") or "test", "name": body.get("name") or "test", **body})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "latency_ms": 0, "context": None, "error": str(e)}
    return await LLMClient(conn).test()


# ---- projects & templates ---------------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str
    client: str = ""
    template_id: str | None = None
    privacy_default: str = "project"
    brief: str = ""
    confidential: bool = False


@app.get("/api/projects")
async def projects():
    return repo.list_projects()


@app.post("/api/projects")
async def create_project(p: ProjectIn):
    return repo.create_project(**p.model_dump())


@app.patch("/api/projects/{pid}")
async def patch_project(pid: str, body: dict):
    return _need(repo.update_project(pid, **body), "project")


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    repo.delete_project(pid)
    return {"ok": True}


def _template_out(t: dict, full: bool = False) -> dict:
    prof = t.get("profile") or {}
    out = {k: v for k, v in t.items() if k != "profile"}
    out["profile_summary"] = {
        "sections": len(prof.get("outline", [])), "fields": len(prof.get("fields", [])),
        "has_toc": prof.get("has_toc"), "house_style": prof.get("house_style"),
    }
    if full:
        out["profile"] = prof
    return out


@app.get("/api/templates")
async def templates():
    return [_template_out(t) for t in repo.list_templates()]


@app.post("/api/templates")
async def upload_template(file: UploadFile = File(...), name: str = Form("")):
    if not file.filename.lower().endswith((".docx", ".dotx", ".docm", ".pptx", ".potx")):
        raise HTTPException(400, "Alleen Word- (.docx/.dotx) of PowerPoint-sjablonen (.pptx/.potx)")
    tid = db.new_id()
    dest = app_paths().templates / f"{tid}{Path(file.filename).suffix.lower()}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        if dest.suffix in (".pptx", ".potx"):
            from ..pptengine import analyze_pptx_template
            profile = {"kind": "pptx", **(await asyncio.to_thread(analyze_pptx_template, dest))}
        else:
            profile = {"kind": "docx", **(await asyncio.to_thread(analyze_template, dest))}
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Kon het sjabloon niet lezen: {e}") from e
    t = repo.add_template(name or Path(file.filename).stem, str(dest), profile, file_hash(dest))
    return _template_out(t, full=True)


@app.get("/api/templates/{tid}")
async def template(tid: str):
    return _template_out(_need(repo.get_template(tid), "sjabloon"), full=True)


@app.delete("/api/templates/{tid}")
async def delete_template(tid: str):
    repo.delete_template(tid)
    return {"ok": True}


# ---- chats & messages -------------------------------------------------------------------------

@app.get("/api/projects/{pid}/chats")
async def chats(pid: str):
    return repo.list_chats(pid)


@app.post("/api/projects/{pid}/chats")
async def create_chat(pid: str, body: dict | None = None):
    body = body or {}
    project = _need(repo.get_project(pid), "project")
    privacy = body.get("privacy") or project.get("privacy_default") or "project"
    return repo.create_chat(pid, body.get("title") or "Nieuwe chat", privacy, bool(body.get("internet")))


@app.patch("/api/chats/{cid}")
async def patch_chat(cid: str, body: dict):
    return _need(repo.update_chat(cid, **body), "chat")


@app.delete("/api/chats/{cid}")
async def delete_chat(cid: str):
    repo.delete_chat(cid)
    return {"ok": True}


@app.get("/api/chats/{cid}/messages")
async def messages(cid: str):
    return repo.list_messages(cid)


@app.get("/api/chats/{cid}/jobs")
async def chat_jobs(cid: str):
    return [_job_out(j) for j in repo.list_jobs(cid)]


class MessageIn(BaseModel):
    text: str = ""
    file_ids: list[str] = []
    action: str = "auto"
    params: dict = {}


@app.post("/api/chats/{cid}/messages")
async def post_message(cid: str, m: MessageIn):
    chat = _need(repo.get_chat(cid), "chat")
    action = m.action if m.action in ACTIONS else "auto"
    files = [f for f in (repo.get_file(fid) for fid in m.file_ids) if f]
    msg = repo.add_message(cid, "user", m.text, {
        "file_ids": m.file_ids, "action": action,
        "files": [{"id": f["id"], "name": f["name"], "size": f["size"]} for f in files]})
    if chat.get("title") in ("", "Nieuwe chat") and m.text.strip():
        repo.update_chat(cid, title=m.text.strip().splitlines()[0][:60])
    params = {**m.params, "text": m.text, "file_ids": m.file_ids, "project_id": chat.get("project_id")}
    job = runner.submit(cid, action, params)
    repo.update_message(msg["id"], meta={**(msg.get("meta") or {}), "job_id": job["id"]})
    return {"message": repo.get_message(msg["id"]), "job_id": job["id"]}


# ---- files -------------------------------------------------------------------------------------

@app.post("/api/files")
async def upload_file(file: UploadFile = File(...), chat_id: str = Form(""), project_id: str = Form("")):
    if chat_id:
        chat = _need(repo.get_chat(chat_id), "chat")
        owner_kind, owner_id, pid = "chat", chat_id, chat.get("project_id")
    elif project_id:
        owner_kind, owner_id, pid = "project", project_id, project_id
    else:
        raise HTTPException(400, "chat_id of project_id vereist")
    d = project_dir(pid) / "files" / db.new_id()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / safe_name(Path(file.filename).name, "bestand")
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    f = repo.add_file(owner_kind, owner_id, file.filename, str(dest), file.content_type or "",
                      hash=file_hash(dest))
    return {"id": f["id"], "name": f["name"], "size": f["size"]}


# ---- documents ----------------------------------------------------------------------------------

@app.get("/api/projects/{pid}/documents")
async def documents(pid: str):
    out = []
    for d in repo.list_documents(pid):
        d["versions"] = [{k: v[k] for k in ("id", "number", "created_at", "changes", "chat_id")}
                         for v in d["versions"]]
        out.append(d)
    return out


def _version(vid: str) -> dict:
    v = _need(repo.get_version(vid), "versie")
    if not Path(v["path"]).exists():
        raise HTTPException(410, "Bestand bestaat niet meer")
    return v


@app.get("/api/versions/{vid}/download")
async def download(vid: str):
    v = _version(vid)
    return FileResponse(v["path"], filename=Path(v["path"]).name,
                        media_type=MIME.get(Path(v["path"]).suffix.lower(), "application/octet-stream"))


@app.get("/api/versions/{vid}/preview")
async def preview(vid: str):
    v = _version(vid)
    if v["path"].lower().endswith(".pptx"):
        return HTMLResponse(await asyncio.to_thread(_slides_html, Path(v["path"])))
    cache = app_paths().cache / "previews" / f"{vid}.html"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(await asyncio.to_thread(to_html, v["path"]), encoding="utf-8")
    return HTMLResponse(cache.read_text(encoding="utf-8"))


@app.get("/api/versions/{vid}/outline")
async def outline(vid: str):
    v = _version(vid)
    if v["path"].lower().endswith(".pptx"):
        deck = Path(v["path"]).with_suffix(".deck.json")
        slides = json.loads(deck.read_text(encoding="utf-8"))["deck"]["slides"] if deck.exists() else []
        return [{"number": str(i), "title": s.get("title", ""), "level": 1} for i, s in enumerate(slides, 1)]
    m = await asyncio.to_thread(DocModel.open, v["path"])
    return [{"number": n.number, "title": n.title, "level": n.level} for n in m.outline()]


@app.post("/api/versions/{vid}/open")
async def open_version(vid: str, body: dict | None = None):
    v = _version(vid)
    if (body or {}).get("reveal"):
        reveal_in_finder(v["path"])
    else:
        open_with_default_app(v["path"])
    return {"ok": True}


def _slides_html(pptx_path: Path) -> str:
    """Slide previews (rendered by the pptengine at build time) as one scrollable HTML fragment."""
    import base64
    prev = pptx_path.with_suffix(".previews")
    imgs = sorted(prev.glob("*.png")) if prev.exists() else []
    if not imgs:
        return "<p>Geen voorvertoning beschikbaar. Open de presentatie in PowerPoint.</p>"
    parts = ['<div class="dw-slides" style="display:flex;flex-direction:column;gap:14px">']
    for i, p in enumerate(imgs, 1):
        b64 = base64.b64encode(p.read_bytes()).decode()
        parts.append(f'<figure style="margin:0"><img alt="Dia {i}" style="width:100%;border-radius:6px;'
                     f'box-shadow:0 1px 4px rgba(0,0,0,.25)" src="data:image/png;base64,{b64}">'
                     f'<figcaption style="font:12px sans-serif;color:#666;margin-top:4px">Dia {i}</figcaption></figure>')
    parts.append("</div>")
    return "".join(parts)


# ---- jobs ------------------------------------------------------------------------------------------

def _job_out(j: dict) -> dict:
    out = {k: j.get(k) for k in ("id", "chat_id", "pipeline", "status", "progress", "steps", "error", "result",
                                 "created_at", "started_at", "ended_at")}
    pending = [qid for qid, (jid, fut) in runner.questions.items() if jid == j["id"] and not fut.done()]
    if pending:
        for ev in reversed(repo.list_job_events(j["id"])):
            e = ev["event"] if isinstance(ev["event"], dict) else json.loads(ev["event"])
            if e.get("type") == "question" and e.get("question_id") in pending:
                out["pending_question"] = e
                break
    return out


@app.get("/api/jobs/{jid}")
async def job(jid: str):
    return _job_out(_need(repo.get_job(jid), "job"))


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str, after: int = 0):
    return [{"id": e["id"], **(e["event"] if isinstance(e["event"], dict) else json.loads(e["event"]))}
            for e in repo.list_job_events(jid, after)]


@app.post("/api/jobs/{jid}/retry")
async def retry(jid: str):
    job = runner.retry(jid)
    return {"job_id": job["id"]}


@app.post("/api/jobs/{jid}/cancel")
async def cancel(jid: str):
    return {"ok": runner.cancel(jid)}


@app.post("/api/jobs/{jid}/answer")
async def answer(jid: str, body: dict):
    ok = runner.answer(body.get("question_id", ""), {"value": body.get("answer", body.get("value")),
                                                    "edited": body.get("edited")})
    if not ok:
        raise HTTPException(404, "Vraag niet (meer) open")
    return {"ok": True}


# ---- diagrams --------------------------------------------------------------------------------------

@app.get("/api/diagrams/{name}")
async def diagram_file(name: str):
    stem, _, ext = name.rpartition(".")
    if ext not in ("svg", "png", "drawio", "json") or not stem.isalnum():
        raise HTTPException(400, "Ongeldig bestand")
    for pdir in app_paths().projects.iterdir():
        p = pdir / "diagrams" / f"{stem}.{ext}"
        if p.exists():
            meta = pdir / "diagrams" / f"{stem}.json"
            nice = json.loads(meta.read_text(encoding="utf-8")).get("name", stem) if meta.exists() else stem
            media = {"svg": "image/svg+xml", "png": "image/png", "drawio": "application/xml",
                     "json": "application/json"}[ext]
            headers = {"Content-Disposition": f'attachment; filename="{safe_name(nice)}.{ext}"'} \
                if ext in ("drawio", "png") else {}
            return Response(p.read_bytes(), media_type=media, headers=headers)
    raise HTTPException(404, "Plaat niet gevonden")


# ---- events (SSE) ------------------------------------------------------------------------------------

@app.get("/api/events")
async def events(request: Request, chat_id: str):
    q = bus.subscribe(chat_id)

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(chat_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


mimetypes.add_type("application/javascript", ".js")
app.mount("/", StaticFiles(directory=WEB), name="web")
