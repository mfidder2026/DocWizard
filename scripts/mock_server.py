"""DocWizard mock-engine voor UI-ontwikkeling.

Implementeert de HTTP-API en SSE-events uit docs/CONTRACTS.md met in-memory
nepdata en een gesimuleerde job die realistische events streamt.

Gebruik:
    uv run python scripts/mock_server.py --port 8765 [--seed] [--speed 1.0]

De statische UI (src/docwizard/web) wordt geserveerd vanaf `/`; in index.html
wordt `__DOCWIZARD_TOKEN__` vervangen door het sessietoken.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import itertools
import json
import os
import random
import re
import time
import uuid
import zipfile
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "docwizard" / "web"
TOKEN = os.environ.get("DOCWIZARD_TOKEN", "mock-token")
SPEED = float(os.environ.get("MOCK_SPEED", "1.0"))  # >1 = sneller

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_ids = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}_{next(_ids):04d}"


def now() -> float:
    return time.time()


def utc_now() -> str:
    """Tijdstempel zoals de engine: ISO-8601 in UTC met microseconden en 'Z'."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts or now()))


DEFAULT_SETTINGS: dict[str, Any] = {
    "ui_language": "nl",
    "doc_language": "nl",
    "author_name": "",
    "connections": [
        {"id": "lmstudio", "name": "LM Studio", "kind": "lmstudio", "base_url": "http://localhost:1234/v1",
         "api_key": "", "model": "qwen3-30b-a3b", "context_limit": None, "enabled": True},
        {"id": "ollama", "name": "Ollama", "kind": "ollama", "base_url": "http://localhost:11434/v1",
         "api_key": "", "model": "gemma3:12b", "context_limit": None, "enabled": True},
        {"id": "mtplx", "name": "MTPLX", "kind": "mtplx", "base_url": "http://localhost:8000/v1",
         "api_key": "", "model": "", "context_limit": None, "enabled": False},
    ],
    "roles": {"writer": "lmstudio", "fast": "ollama", "vision": "", "embedding": ""},
    "quality_context_tokens": 32000,
    "max_output_tokens": 4096,
    "web": {"provider": "duckduckgo", "url": "", "api_key": "", "confirm_queries": True, "allow_domains": []},
    "data_dir_override": "",
}


class Store:
    def __init__(self) -> None:
        self.settings: dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.projects: dict[str, dict] = {}
        self.templates: dict[str, dict] = {}
        self.chats: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}
        self.files: dict[str, dict] = {}
        self.documents: dict[str, dict] = {}
        self.versions: dict[str, dict] = {}
        self.diagrams: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.job_events: dict[str, list[dict]] = {}
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.event_seq = itertools.count(1)


S = Store()

# ---------------------------------------------------------------------------
# Nepinhoud
# ---------------------------------------------------------------------------

TEMPLATE_PROFILE = {
    "doc_type": "AQS",
    "language": "nl",
    "outline": [
        {"number": "1", "title": "Inleiding", "level": 1, "guidance": "", "children": [
            {"number": "1.1", "title": "Aanleiding", "level": 2,
             "guidance": "Beschrijf de aanleiding voor deze quick scan en de vraag van de opdrachtgever.",
             "length": "150-300 woorden"},
            {"number": "1.2", "title": "Doel en scope", "level": 2,
             "guidance": "Beschrijf doel, scope en afbakening. Wat valt buiten scope?",
             "fixed_text": "Deze quick scan is uitgevoerd op basis van interviews en documentstudie."},
        ]},
        {"number": "2", "title": "Huidige situatie", "level": 1,
         "guidance": "Schets het huidige applicatielandschap en de belangrijkste knelpunten."},
        {"number": "3", "title": "Bevindingen", "level": 1,
         "guidance": "Vat per architectuurthema de bevindingen samen.",
         "table": {"columns": ["Thema", "Bevinding", "Risico", "Advies"]}},
        {"number": "4", "title": "Wet- en regelgeving", "level": 1,
         "guidance": "Benoem relevante wet- en regelgeving (AVG, BIO, Archiefwet).",
         "research": ["internet", "kennispakket:wetgeving"]},
        {"number": "5", "title": "Advies", "level": 1,
         "guidance": "Geef een concreet, geprioriteerd advies met vervolgstappen."},
    ],
    "fields": [
        {"key": "klant", "label": "Klant", "source": "content control"},
        {"key": "versie", "label": "Versie", "source": "{{versie}}"},
        {"key": "datum", "label": "Datum", "source": "{{datum}}", "default": "vandaag"},
    ],
    "house_style": {
        "headings": {"1": "Kop 1", "2": "Kop 2", "3": "Kop 3"},
        "body": "AQSBody",
        "bullet": "Lijstopsommingsteken",
        "table": "Sjabloontabel 'Bevindingen' (koprij #004B87, witte vette tekst)",
        "numbered_headings": True,
        "fonts": {"body": "Calibri 10,5 pt", "headings": "Calibri Light"},
    },
    "writing_guide": {"tone": "zakelijk, adviserend, actief, B1–B2",
                      "perspective": "wij (het architectuurteam)",
                      "forbidden": ["synergie", "naadloos"]},
}

SECTION_TEXT = {
    "Inleiding": "Deze quick scan brengt in korte tijd de architectuur van het zaaksysteemlandschap in kaart. "
                 "Wij hebben gesproken met acht stakeholders en ruim dertig documenten bestudeerd. "
                 "Het doel is een onderbouwd beeld van de knelpunten en een advies voor de komende twee jaar.",
    "Huidige situatie": "De organisatie gebruikt momenteel een zaaksysteem dat in 2014 is ingericht en sindsdien "
                        "sterk is uitgebreid met maatwerk. Koppelingen lopen grotendeels via point-to-point "
                        "interfaces; een centrale integratielaag ontbreekt. Het identiteits- en toegangsbeheer "
                        "is verdeeld over drie omgevingen.",
    "Bevindingen": "Op het thema **identiteits- en toegangsbeheer (IAM)** zien wij dat rollen handmatig worden "
                   "toegekend en niet periodiek worden herzien. Dit vormt een risico voor de naleving van de BIO. "
                   "Op het thema **integratie** constateren wij een hoge afhankelijkheid van één leverancier.",
    "Wet- en regelgeving": "Relevant zijn de Algemene verordening gegevensbescherming (AVG), de Baseline "
                           "Informatiebeveiliging Overheid (BIO) en de Archiefwet 1995. De nieuwe Archiefwet "
                           "verkort de overbrengingstermijn naar tien jaar.",
    "Advies": "Wij adviseren om eerst een centrale integratievoorziening in te richten en vervolgens het "
              "zaaksysteem gefaseerd te migreren. Begin met een pilot voor één werkproces en borg IAM via "
              "een centrale rolhiërarchie.",
}
GENERIC_TEXT = ("Wij hebben deze sectie opgesteld op basis van de aangeleverde stukken en de interviews. "
                "De belangrijkste conclusie is dat de huidige inrichting voldoet voor de korte termijn, "
                "maar dat schaalbaarheid en beheerbaarheid aandacht vragen.")

REASONING = [
    "De gebruiker vraagt om een nieuwe AQS. Ik controleer eerst welk sjabloon bij het project hoort. ",
    "Het sjabloon heeft vijf hoofdstukken; hoofdstuk 3 verwacht een tabel met thema's. ",
    "In de aangeleverde samenvatting staan IAM, integratie en archivering als thema's. ",
    "Ik houd de toon zakelijk en adviserend, perspectief 'wij'. ",
]

CHAT_REPLIES = [
    "Goede vraag. Op basis van de projectdocumenten zie ik drie aandachtspunten:\n\n"
    "1. **Integratie** — er is geen centrale integratielaag.\n"
    "2. **IAM** — rollen worden handmatig toegekend.\n"
    "3. **Archivering** — de nieuwe Archiefwet verkort termijnen.\n\n"
    "| Thema | Prioriteit |\n|---|---|\n| Integratie | Hoog |\n| IAM | Hoog |\n| Archivering | Middel |\n\n"
    "Zal ik hier een hoofdstuk *Bevindingen* van maken? Gebruik dan `/hoofdstuk`.",
    "Dat kan. Ik stel voor om het advies op te bouwen in twee fasen:\n\n"
    "- **Fase 1:** centrale integratievoorziening en IAM op orde.\n"
    "- **Fase 2:** gefaseerde migratie van het zaaksysteem.\n\n"
    "Zie ook de [NORA-principes](https://www.noraonline.nl) voor onderbouwing.",
]

PIPELINES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "new_document": ("Nieuw document", [
        ("analyse", "Invoer analyseren"), ("sources", "Bronnen zoeken"), ("outline", "Outline opstellen"),
        ("approve", "Outline goedkeuren"), ("write", "Secties schrijven"), ("review", "Review & consistentie"),
        ("diagram", "Architectuurplaat maken"), ("build", "Opbouwen in huisstijl")]),
    "add_chapter": ("Hoofdstuk toevoegen", [
        ("analyse", "Document lezen"), ("sources", "Bronnen zoeken"), ("outline", "Opzet hoofdstuk"),
        ("approve", "Opzet goedkeuren"), ("write", "Hoofdstuk schrijven"), ("build", "Nieuwe versie opbouwen")]),
    "translate": ("Vertalen", [
        ("analyse", "Document lezen"), ("write", "Secties vertalen"), ("review", "Termenlijst controleren"),
        ("build", "Vertaalde versie opbouwen")]),
    "correct": ("Corrigeren", [
        ("analyse", "Document lezen"), ("write", "Taal en stijl corrigeren"), ("build", "Versie met wijzigingen")]),
    "review": ("Reviewen", [
        ("analyse", "Document lezen"), ("review", "Inhoudelijke review"), ("write", "Reviewnotities schrijven"),
        ("build", "Opmerkingen toevoegen")]),
    "enrich": ("Verrijken", [
        ("analyse", "Document lezen"), ("sources", "Bronnen zoeken"), ("write", "Secties verrijken"),
        ("build", "Nieuwe versie opbouwen")]),
    "diagram": ("Plaat maken", [
        ("analyse", "Beschrijving analyseren"), ("outline", "Elementen en relaties bepalen"),
        ("diagram", "Plaat tekenen")]),
    "chat": ("Gesprek", [("answer", "Antwoord formuleren")]),
}

DEFAULT_OUTLINE = [
    {"number": "1", "title": "Inleiding", "level": 1},
    {"number": "2", "title": "Huidige situatie", "level": 1},
    {"number": "3", "title": "Bevindingen", "level": 1},
    {"number": "4", "title": "Wet- en regelgeving", "level": 1},
    {"number": "5", "title": "Advies", "level": 1},
]

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="DocWizard mock")


@app.middleware("http")
async def token_check(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health":
        tok = request.headers.get("X-DocWizard-Token") or request.query_params.get("token")
        if tok != TOKEN:
            return JSONResponse({"detail": "invalid token"}, status_code=401)
    return await call_next(request)


def nf(what: str) -> HTTPException:
    return HTTPException(404, f"{what} niet gevonden")


# --- health / settings -------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"ok": True, "version": "0.1.0-mock"}


@app.get("/api/settings")
async def get_settings():
    return S.settings


@app.put("/api/settings")
async def put_settings(body: dict):
    conns = body.get("connections", [])
    ids = [c.get("id") for c in conns]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Verbinding-id's moeten uniek zijn")
    for c in conns:
        if not str(c.get("base_url", "")).startswith(("http://", "https://")):
            raise HTTPException(422, f"Ongeldige basis-URL voor '{c.get('name')}'")
    merged = {**S.settings, **body}
    S.settings = merged
    return S.settings


@app.post("/api/connections/test")
async def test_connection(conn: dict):
    await asyncio.sleep(0.4 / SPEED)
    url = str(conn.get("base_url", ""))
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "models": [], "latency_ms": 0, "context": None,
                "error": "Ongeldige URL: moet met http:// of https:// beginnen"}
    if "9999" in url or "fail" in url:
        return {"ok": False, "models": [], "latency_ms": 0, "context": None,
                "error": f"Verbinding geweigerd ({url})"}
    kind = conn.get("kind", "openai")
    models = {
        "lmstudio": [("qwen3-30b-a3b", 32768), ("mistral-small-3.2-24b", 131072), ("text-embedding-nomic", 2048)],
        "ollama": [("gemma3:12b", 8192), ("llama3.1:8b", 131072), ("nomic-embed-text", 2048)],
        "mtplx": [("mlx-community/Qwen3-32B-4bit", 40960)],
    }.get(kind, [("gpt-oss-20b", 131072), ("local-model", 8192)])
    chosen = conn.get("model") or models[0][0]
    ctx = dict(models).get(chosen, models[0][1])
    return {"ok": True, "models": [{"id": m, "context_length": c} for m, c in models],
            "latency_ms": random.randint(18, 140), "context": ctx, "error": None}


# --- projects ------------------------------------------------------------------

@app.get("/api/projects")
async def list_projects():
    return sorted(S.projects.values(), key=lambda p: p["updated_at"], reverse=True)


@app.post("/api/projects")
async def create_project(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Naam is verplicht")
    p = {"id": new_id("prj"), "name": name, "client": body.get("client") or "",
         "template_id": body.get("template_id") or None,
         "privacy_default": body.get("privacy_default") or "project",
         "created_at": iso(), "updated_at": iso()}
    S.projects[p["id"]] = p
    return p


@app.patch("/api/projects/{pid}")
async def patch_project(pid: str, body: dict):
    p = S.projects.get(pid)
    if not p:
        raise nf("Project")
    for k in ("name", "client", "template_id", "privacy_default"):
        if k in body:
            p[k] = body[k]
    p["updated_at"] = iso()
    return p


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    if pid not in S.projects:
        raise nf("Project")
    del S.projects[pid]
    for cid in [c for c, ch in S.chats.items() if ch["project_id"] == pid]:
        S.chats.pop(cid, None)
        S.messages.pop(cid, None)
    return {"ok": True}


# --- templates -----------------------------------------------------------------

def _template_summary(t: dict) -> dict:
    return {k: t[k] for k in ("id", "name", "version", "created_at", "profile_summary")}


@app.get("/api/templates")
async def list_templates():
    return [_template_summary(t) for t in S.templates.values()]


@app.post("/api/templates")
async def upload_template(file: UploadFile = File(...), name: str = Form("")):
    data = await file.read()
    fname = file.filename or "sjabloon.docx"
    if not fname.lower().endswith((".docx", ".dotx")):
        raise HTTPException(422, "Alleen .docx of .dotx")
    await asyncio.sleep(0.6 / SPEED)  # "analyse"
    profile = json.loads(json.dumps(TEMPLATE_PROFILE))
    t = {"id": new_id("tpl"), "name": name.strip() or Path(fname).stem, "version": 1,
         "created_at": iso(), "filename": fname, "size": len(data),
         "profile_summary": f"{len(profile['outline'])} hoofdstukken · {len(profile['fields'])} velden · "
                            f"huisstijl gedetecteerd",
         "profile": profile}
    S.templates[t["id"]] = t
    return t


@app.get("/api/templates/{tid}")
async def get_template(tid: str):
    t = S.templates.get(tid)
    if not t:
        raise nf("Sjabloon")
    return t


# --- chats ------------------------------------------------------------------------

def _chat_public(c: dict) -> dict:
    return {k: c[k] for k in ("id", "project_id", "title", "privacy", "internet", "roles", "updated_at")}


@app.get("/api/projects/{pid}/chats")
async def list_chats(pid: str):
    if pid not in S.projects:
        raise nf("Project")
    chats = [_chat_public(c) for c in S.chats.values() if c["project_id"] == pid]
    return sorted(chats, key=lambda c: c["updated_at"], reverse=True)


@app.post("/api/projects/{pid}/chats")
async def create_chat(pid: str, body: dict | None = None):
    body = body or {}
    p = S.projects.get(pid)
    if not p:
        raise nf("Project")
    privacy = body.get("privacy") or p.get("privacy_default") or "project"
    c = {"id": new_id("chat"), "project_id": pid, "title": body.get("title") or "Nieuwe chat",
         "privacy": privacy, "internet": bool(body.get("internet")) and privacy != "private",
         "roles": {}, "updated_at": iso()}
    S.chats[c["id"]] = c
    S.messages[c["id"]] = []
    return _chat_public(c)


@app.patch("/api/chats/{cid}")
async def patch_chat(cid: str, body: dict):
    c = S.chats.get(cid)
    if not c:
        raise nf("Chat")
    for k in ("title", "privacy", "internet", "roles"):
        if k in body:
            c[k] = body[k]
    if c["privacy"] == "private":
        c["internet"] = False
    c["updated_at"] = iso()
    return _chat_public(c)


@app.get("/api/chats/{cid}/messages")
async def list_messages(cid: str):
    if cid not in S.chats:
        raise nf("Chat")
    return S.messages[cid]


def add_message(cid: str, role: str, content: str, meta: dict | None = None) -> dict:
    m = {"id": new_id("msg"), "role": role, "content": content, "created_at": iso(), "meta": meta or {}}
    S.messages.setdefault(cid, []).append(m)
    if cid in S.chats:
        S.chats[cid]["updated_at"] = iso()
    return m


SLASH = {"/nieuw": "new_document", "/hoofdstuk": "add_chapter", "/vertaal": "translate",
         "/corrigeer": "correct", "/review": "review", "/verrijk": "enrich", "/plaat": "diagram"}


@app.post("/api/chats/{cid}/messages")
async def post_message(cid: str, body: dict):
    c = S.chats.get(cid)
    if not c:
        raise nf("Chat")
    text = (body.get("text") or "").strip()
    action = body.get("action") or "auto"
    files = [S.files[f] for f in body.get("file_ids", []) if f in S.files]
    if action == "auto":
        first = text.split(" ", 1)[0].lower()
        action = SLASH.get(first, "chat")
        if first in SLASH:
            text = text[len(first):].strip()
    meta = {"action": action, "files": [{"id": f["id"], "name": f["name"], "size": f["size"]} for f in files]}
    msg = add_message(cid, "user", text, meta)
    if c["title"] == "Nieuwe chat" and text:
        c["title"] = text[:40] + ("…" if len(text) > 40 else "")
    job = create_job(cid, action, {"text": text, **(body.get("params") or {})})
    msg["meta"]["job_id"] = job["id"]
    return {"message": msg, "job_id": job["id"]}


# --- files ------------------------------------------------------------------------

@app.post("/api/files")
async def upload_file(file: UploadFile = File(...), chat_id: str = Form(""), project_id: str = Form("")):
    data = await file.read()
    f = {"id": new_id("file"), "name": file.filename or "bestand", "size": len(data),
         "mime": file.content_type or "application/octet-stream",
         "chat_id": chat_id or None, "project_id": project_id or None, "created_at": iso()}
    if chat_id and chat_id in S.chats:
        f["project_id"] = S.chats[chat_id]["project_id"]
    S.files[f["id"]] = f
    return {"id": f["id"], "name": f["name"], "size": f["size"]}


@app.get("/api/projects/{pid}/files")
async def list_project_files(pid: str):
    return [f for f in S.files.values() if f.get("project_id") == pid]


# --- documents / versions ----------------------------------------------------

@app.get("/api/projects/{pid}/documents")
async def list_documents(pid: str):
    docs = [d for d in S.documents.values() if d["project_id"] == pid]
    out = []
    for d in docs:
        vs = [S.versions[v] for v in d["version_ids"]]
        out.append({"id": d["id"], "name": d["name"], "kind": d["kind"],
                    "versions": [{"id": v["id"], "number": v["number"], "created_at": v["created_at"],
                                  "changes": v["changes"]} for v in vs]})
    return out


def _version(vid: str) -> dict:
    v = S.versions.get(vid)
    if not v:
        raise nf("Versie")
    return v


@app.get("/api/versions/{vid}/outline")
async def version_outline(vid: str):
    return [{"number": str(s["number"]), "title": s["title"], "level": s.get("level", 1)} for s in _version(vid)["sections"]]


@app.get("/api/versions/{vid}/preview", response_class=HTMLResponse)
async def version_preview(vid: str):
    v = _version(vid)
    d = S.documents[v["document_id"]]
    parts = [f'<h1 class="doc-title">{escape(d["name"])}</h1>',
             f'<p class="doc-meta">Versie {escape(v["number"])} · {escape(v["created_at"][:10])}</p>']
    for s in v["sections"]:
        lvl = min(max(int(s.get("level", 1)), 1), 3) + 1
        parts.append(f'<h{lvl}>{escape(str(s["number"]))} {escape(str(s["title"]))}</h{lvl}>')
        body = s.get("text", "")
        body = escape(body)
        body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
        parts.append(f"<p>{body}</p>")
        if s["title"] == "Bevindingen":
            parts.append('<table><thead><tr><th>Thema</th><th>Bevinding</th><th>Risico</th><th>Advies</th></tr>'
                         '</thead><tbody><tr><td>IAM</td><td>Handmatige roltoekenning</td><td>Hoog</td>'
                         '<td>Centrale rolhiërarchie</td></tr><tr><td>Integratie</td><td>Point-to-point</td>'
                         '<td>Middel</td><td>Integratievoorziening</td></tr></tbody></table>')
    return "\n".join(parts)


def _docx_bytes(v: dict) -> bytes:
    d = S.documents[v["document_id"]]
    paras = [f"<w:p><w:pPr><w:pStyle w:val=\"Title\"/></w:pPr><w:r><w:t>{escape(d['name'])}</w:t></w:r></w:p>"]
    for s in v["sections"]:
        paras.append(f"<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr><w:r><w:t>"
                     f"{escape(str(s['number']) + ' ' + str(s['title']))}</w:t></w:r></w:p>")
        paras.append(f"<w:p><w:r><w:t>{escape(s.get('text', '').replace('**', ''))}</w:t></w:r></w:p>")
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(paras) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


@app.get("/api/versions/{vid}/download")
async def version_download(vid: str):
    v = _version(vid)
    d = S.documents[v["document_id"]]
    fname = re.sub(r"[^\w\-]+", "_", d["name"]) + f"_v{v['number']}.docx"
    return Response(_docx_bytes(v),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/versions/{vid}/open")
async def version_open(vid: str, body: dict | None = None):
    _version(vid)
    return {"ok": True, "reveal": bool((body or {}).get("reveal"))}


# --- diagrams ---------------------------------------------------------------------

DIAGRAM_BOXES = [("Burger / medewerker", 40, 30), ("Zaaksysteem", 40, 150), ("Integratielaag", 260, 150),
                 ("DMS / archief", 480, 90), ("IAM", 480, 210), ("Basisregistraties", 260, 270)]
DIAGRAM_EDGES = [(0, 1), (1, 2), (2, 3), (2, 4), (2, 5)]


def _diagram_svg(title: str) -> str:
    w, h = 660, 340
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           'font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
           '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5b6b7f"/></marker></defs>']
    for a, b in DIAGRAM_EDGES:
        (_, x1, y1), (_, x2, y2) = DIAGRAM_BOXES[a], DIAGRAM_BOXES[b]
        out.append(f'<line x1="{x1 + 70}" y1="{y1 + 25}" x2="{x2 + 70}" y2="{y2 + 25}" stroke="#5b6b7f" '
                   'stroke-width="1.5" marker-end="url(#a)"/>')
    for i, (label, x, y) in enumerate(DIAGRAM_BOXES):
        fill = "#dbe8f7" if i != 2 else "#004B87"
        color = "#0b2540" if i != 2 else "#ffffff"
        out.append(f'<rect x="{x}" y="{y}" width="140" height="50" rx="6" fill="{fill}" stroke="#004B87"/>')
        out.append(f'<text x="{x + 70}" y="{y + 30}" text-anchor="middle" font-size="13" fill="{color}">'
                   f'{escape(label)}</text>')
    out.append(f'<text x="{w - 12}" y="{h - 10}" text-anchor="end" font-size="11" fill="#8a96a3">{escape(title)}</text>')
    out.append("</svg>")
    return "".join(out)


@app.get("/api/diagrams/{name}")
async def diagram_file(name: str):
    did, _, ext = name.rpartition(".")
    dg = S.diagrams.get(did)
    if not dg:
        raise nf("Plaat")
    if ext == "svg":
        return Response(_diagram_svg(dg["name"]), media_type="image/svg+xml")
    if ext == "drawio":
        cells = []
        for i, (label, x, y) in enumerate(DIAGRAM_BOXES):
            cells.append(f'<mxCell id="n{i}" value="{escape(label)}" style="rounded=1;whiteSpace=wrap;" vertex="1" '
                         f'parent="1"><mxGeometry x="{x}" y="{y}" width="140" height="50" as="geometry"/></mxCell>')
        for j, (a, b) in enumerate(DIAGRAM_EDGES):
            cells.append(f'<mxCell id="e{j}" edge="1" parent="1" source="n{a}" target="n{b}">'
                         '<mxGeometry relative="1" as="geometry"/></mxCell>')
        xml = ('<mxfile host="DocWizard"><diagram name="Plaat"><mxGraphModel><root><mxCell id="0"/>'
               '<mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
        return Response(xml, media_type="application/xml",
                        headers={"Content-Disposition": f'attachment; filename="{did}.drawio"'})
    if ext == "png":
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (660, 340), "white")
        dr = ImageDraw.Draw(img)
        for a, b in DIAGRAM_EDGES:
            (_, x1, y1), (_, x2, y2) = DIAGRAM_BOXES[a], DIAGRAM_BOXES[b]
            dr.line([(x1 + 70, y1 + 25), (x2 + 70, y2 + 25)], fill="#5b6b7f", width=2)
        for label, x, y in DIAGRAM_BOXES:
            dr.rounded_rectangle([x, y, x + 140, y + 50], 6, fill="#dbe8f7", outline="#004B87")
            dr.text((x + 10, y + 18), label, fill="#0b2540")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png",
                        headers={"Content-Disposition": f'attachment; filename="{did}.png"'})
    raise nf("Formaat")


# --- jobs -------------------------------------------------------------------------

def _job_public(j: dict) -> dict:
    out = {k: j[k] for k in ("id", "chat_id", "pipeline", "title", "status", "progress", "steps", "error", "created_at")}
    out["pausable"] = True  # alleen de mock ondersteunt pauzeren
    q = j.get("question")
    if q and not q["event"].is_set():
        out["pending_question"] = q["ev"]
    return out


@app.get("/api/jobs/{jid}")
async def get_job(jid: str):
    j = S.jobs.get(jid)
    if not j:
        raise nf("Job")
    return _job_public(j)


@app.get("/api/jobs/{jid}/events")
async def get_job_events(jid: str, after: int = 0, after_id: int = 0):
    """Zoals de engine: alleen opgeslagen events (geen token/reasoning/stats), met `id` = volgnummer.
    Net als in de engine overschrijft het artefact-id het volgnummer bij `artifact`-events."""
    if jid not in S.jobs:
        raise nf("Job")
    after = max(after, after_id)
    return [{"id": e["seq"], **e} for e in S.job_events.get(jid, [])
            if e["seq"] > after and e["type"] not in ("token", "reasoning", "stats")]


@app.get("/api/chats/{cid}/jobs")
async def list_chat_jobs(cid: str):
    if cid not in S.chats:
        raise nf("Chat")
    return [_job_public(j) for j in S.jobs.values() if j["chat_id"] == cid]


@app.post("/api/jobs/{jid}/retry")
async def retry_job(jid: str):
    j = S.jobs.get(jid)
    if not j:
        raise nf("Job")
    nj = create_job(j["chat_id"], j["pipeline"], {**j["params"], "resume_from": jid, "auto_approve": True})
    return {"job_id": nj["id"]}


@app.post("/api/jobs/{jid}/cancel")
async def cancel_job(jid: str):
    j = S.jobs.get(jid)
    if not j:
        raise nf("Job")
    j["cancel"].set()
    j["resume"].set()
    return {"ok": True}


@app.post("/api/jobs/{jid}/pause")
async def pause_job(jid: str):
    j = S.jobs.get(jid)
    if not j:
        raise nf("Job")
    if j["status"] == "running":
        j["resume"].clear()
        j["status"] = "paused"
        await emit(j, "job_status", status="paused")
        await emit(j, "log", level="info", text="Job gepauzeerd door gebruiker")
    return {"ok": True}


@app.post("/api/jobs/{jid}/resume")
async def resume_job(jid: str):
    j = S.jobs.get(jid)
    if not j:
        raise nf("Job")
    if j["status"] == "paused":
        j["status"] = "running"
        j["resume"].set()
        await emit(j, "job_status", status="running")
        await emit(j, "log", level="info", text="Job hervat")
    return {"ok": True}


@app.post("/api/jobs/{jid}/answer")
async def answer_job(jid: str, body: dict):
    j = S.jobs.get(jid)
    if not j:
        raise nf("Job")
    q = j.get("question")
    if not q or q["question_id"] != body.get("question_id"):
        raise HTTPException(409, "Geen openstaande vraag met dit id")
    q["answer"] = {"value": body.get("answer"), "edited": body.get("edited")}
    q["event"].set()
    return {"ok": True}


# --- SSE --------------------------------------------------------------------------

async def publish(chat_id: str, ev: dict) -> None:
    for q in list(S.subscribers.get(chat_id, ())):
        q.put_nowait(ev)


async def emit(job: dict, type_: str, **fields) -> dict:
    ev = {"seq": next(S.event_seq), "type": type_, "chat_id": job["chat_id"], "job_id": job["id"],
          "ts": utc_now(), **fields}
    S.job_events.setdefault(job["id"], []).append(ev)
    await publish(job["chat_id"], ev)
    return ev


@app.get("/api/events")
async def events(request: Request, chat_id: str):
    q: asyncio.Queue = asyncio.Queue()
    S.subscribers.setdefault(chat_id, set()).add(q)

    async def gen():
        try:
            yield "retry: 2000\n\n"
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"id: {ev.get('seq', '')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            S.subscribers.get(chat_id, set()).discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- job simulation -----------------------------------------------------------------

class Cancelled(Exception):
    pass


def create_job(chat_id: str, action: str, params: dict) -> dict:
    pipeline = action if action in PIPELINES else "chat"
    title, steps = PIPELINES[pipeline]
    j = {"id": new_id("job"), "chat_id": chat_id, "pipeline": pipeline, "title": title, "status": "queued",
         "created_at": iso(),
         "progress": 0.0, "error": None, "params": params,
         "steps": [{"key": k, "label": lbl, "status": "pending", "detail": None, "started_at": None,
                    "ended_at": None} for k, lbl in steps],
         "cancel": asyncio.Event(), "resume": asyncio.Event(), "question": None}
    j["resume"].set()
    S.jobs[j["id"]] = j
    j["task"] = asyncio.get_event_loop().create_task(run_job(j))
    return j


async def tick(job: dict, seconds: float) -> None:
    end = time.monotonic() + seconds / SPEED
    while True:
        if job["cancel"].is_set():
            raise Cancelled()
        if not job["resume"].is_set():
            await job["resume"].wait()
            end = time.monotonic() + seconds / SPEED
            continue
        left = end - time.monotonic()
        if left <= 0:
            return
        await asyncio.sleep(min(left, 0.05))


def _step(job: dict, key: str) -> dict:
    return next(s for s in job["steps"] if s["key"] == key)


async def set_step(job: dict, key: str, status: str, detail: str | None = None) -> None:
    s = _step(job, key)
    s["status"] = status
    if detail is not None:
        s["detail"] = detail
    if status == "running":
        s["started_at"] = iso()
        s["_t0"] = now()
    elif status in ("done", "failed", "skipped"):
        s["ended_at"] = iso()
    done = sum(1 for x in job["steps"] if x["status"] in ("done", "skipped"))
    job["progress"] = round(done / len(job["steps"]), 3)
    await emit(job, "step", key=key, label=s["label"], status=status, detail=s["detail"],
               progress=job["progress"], started_at=s["started_at"], ended_at=s["ended_at"])


async def stream(job: dict, key: str, text: str, kind: str = "token", rate: float = 28.0) -> None:
    """Stream tekst woord voor woord (rate ≈ tokens/s)."""
    words = re.findall(r"\S+\s*|\n", text)
    t0 = time.monotonic()
    for i, w in enumerate(words, 1):
        await tick(job, 1 / rate)
        await emit(job, kind, key=key, text=w)
        if kind == "token" and i % 12 == 0:
            el = max(time.monotonic() - t0, 0.01)
            remaining = (len(words) - i) / rate + job.get("_eta_extra", 0)
            await emit(job, "stats", tokens_per_s=round(i * 1.3 / el * SPEED, 1), eta_s=round(remaining / SPEED))


async def run_job(job: dict) -> None:
    await asyncio.sleep(0.05)
    job["status"] = "running"
    try:
        await emit(job, "job_started", pipeline=job["pipeline"], title=job["title"],
                   steps=[{"key": s["key"], "label": s["label"]} for s in job["steps"]])
        if job["pipeline"] == "chat":
            await run_chat(job)
        else:
            await run_pipeline(job)
    except Cancelled:
        job["status"] = "cancelled"
        for s in job["steps"]:
            if s["status"] == "running":
                s["status"] = "failed"
                await emit(job, "step", key=s["key"], label=s["label"], status="failed", detail="Gestopt",
                           progress=job["progress"])
        job["error"] = "Geannuleerd door gebruiker"
        await emit(job, "job_failed", error="Geannuleerd")
    except Exception as exc:  # pragma: no cover
        job["status"] = "failed"
        job["error"] = str(exc)
        await emit(job, "job_failed", error=str(exc))


async def run_chat(job: dict) -> None:
    await set_step(job, "answer", "running")
    await stream(job, "answer", "De vraag gaat over de architectuur; ik zoek eerst in de projectdocumenten. ",
                 kind="reasoning", rate=60)
    reply = random.choice(CHAT_REPLIES)
    await stream(job, "answer", reply, rate=40)
    await set_step(job, "answer", "done")
    job["status"] = "done"
    msg = add_message(job["chat_id"], "assistant", reply, {"job_id": job["id"]})
    await emit(job, "message", message=msg)
    await emit(job, "job_done", summary="Antwoord gegeven")


async def run_pipeline(job: dict) -> None:
    keys = [s["key"] for s in job["steps"]]
    chat = S.chats[job["chat_id"]]
    project = S.projects.get(chat["project_id"], {})
    outline = json.loads(json.dumps(DEFAULT_OUTLINE))
    tpl = S.templates.get(project.get("template_id") or "")
    if tpl:
        outline = [{"number": o["number"], "title": o["title"], "level": 1} for o in tpl["profile"]["outline"]]
    written: list[dict] = []
    job["_eta_extra"] = 60

    if "analyse" in keys:
        await set_step(job, "analyse", "running", "Invoer en bijlagen lezen")
        await emit(job, "log", level="info", text=f"Sjabloon: {tpl['name'] if tpl else 'standaard (geen sjabloon gekoppeld)'}")
        for r in REASONING:
            await stream(job, "analyse", r, kind="reasoning", rate=70)
        await tick(job, 0.4)
        await set_step(job, "analyse", "done", "3 thema's herkend")

    if "sources" in keys:
        await set_step(job, "sources", "running", "Kennisbank en projectdocumenten doorzoeken")
        srcs = [
            {"title": "Samenvatting interviews.docx", "kind": "file", "snippet": "IAM wordt per afdeling geregeld…"},
            {"title": "AQS RWS 2024 — Bevindingen", "kind": "project", "snippet": "Point-to-point koppelingen…"},
            {"title": "BIO 2.0 — Toegangsbeveiliging", "kind": "knowledge", "snippet": "Hoofdstuk 9 Toegangsbeveiliging"},
        ]
        if chat.get("internet"):
            srcs.append({"title": "NORA — Architectuurprincipes", "kind": "web", "url": "https://www.noraonline.nl/wiki/Architectuurprincipes",
                         "snippet": "Basisprincipes voor de Nederlandse overheid"})
            srcs.append({"title": "Archiefwet 1995 — wetten.overheid.nl", "kind": "web",
                         "url": "https://wetten.overheid.nl/BWBR0007376/", "snippet": "Artikel 12 overbrenging"})
            await emit(job, "log", level="info", text="Zoekvraag verzonden: 'architectuurprincipes overheid integratie'")
        for s in srcs:
            await tick(job, 0.35)
            await emit(job, "source", **s)
        await set_step(job, "sources", "done", f"{len(srcs)} bronnen gevonden")

    if "outline" in keys:
        await set_step(job, "outline", "running")
        txt = "\n".join(f"{o['number']} {o['title']}" for o in outline)
        await stream(job, "outline", txt + "\n", rate=35)
        await set_step(job, "outline", "done", f"{len(outline)} hoofdstukken")

    if "approve" in keys:
        await set_step(job, "approve", "running", "Wacht op jouw goedkeuring")
        qid = new_id("q")
        job["question"] = {"question_id": qid, "event": asyncio.Event(), "answer": None}
        if job["params"].get("auto_approve"):
            job["question"]["event"].set()
            job["question"]["answer"] = {"value": "approve", "edited": None}
        job["question"]["ev"] = await emit(job, "question", question_id=qid,
                   text="Klopt deze outline? Je kunt hem aanpassen voordat ik ga schrijven.",
                   options=[{"value": "approve", "label": "Goedkeuren"}, {"value": "regenerate", "label": "Opnieuw voorstellen"},
                            {"value": "cancel", "label": "Annuleren"}],
                   editable={"kind": "json", "value": outline})
        job["status"] = "waiting"
        await emit(job, "job_status", status="waiting")
        while not job["question"]["event"].is_set():
            if job["cancel"].is_set():
                raise Cancelled()
            await asyncio.sleep(0.05)
        job["status"] = "running"
        await emit(job, "job_status", status="running")
        ans = job["question"]["answer"] or {}
        job["question"] = None
        if ans.get("value") == "cancel":
            raise Cancelled()
        edited = ans.get("edited")
        if isinstance(edited, str):
            try:
                edited = json.loads(edited)
            except ValueError:
                edited = None
        if isinstance(edited, list) and edited:
            outline = [o for o in edited if isinstance(o, dict) and o.get("title")]
        await emit(job, "log", level="info", text=f"Outline goedgekeurd ({len(outline)} hoofdstukken)")
        await set_step(job, "approve", "done", "Goedgekeurd")

    if "write" in keys:
        todo = outline if job["pipeline"] in ("new_document",) else outline[:1]
        if job["pipeline"] == "add_chapter":
            todo = [{"number": str(len(outline) + 1), "title": job["params"].get("text") or "Nieuw hoofdstuk", "level": 1}]
        for i, o in enumerate(todo, 1):
            job["_eta_extra"] = (len(todo) - i) * 12 + 20
            await set_step(job, "write", "running", f"Sectie {o.get('number', i)} {o['title']} ({i}/{len(todo)})")
            await stream(job, "write", f"Ik richt me in {o['title']} op de kernboodschap. ", kind="reasoning", rate=80)
            text = SECTION_TEXT.get(o["title"], GENERIC_TEXT)
            await emit(job, "token", key="write", text=f"\n\n## {o.get('number', i)} {o['title']}\n\n")
            await stream(job, "write", text, rate=32)
            written.append({"number": str(o.get("number", i)), "title": o["title"], "level": o.get("level", 1), "text": text})
        await set_step(job, "write", "done", f"{len(todo)} secties geschreven")

    if "review" in keys:
        await set_step(job, "review", "running", "Consistentie en terminologie controleren")
        await stream(job, "review", "Controle: 'IAM' wordt overal eerst voluit geschreven. Geen verboden woorden gevonden. ",
                     kind="reasoning", rate=60)
        await emit(job, "log", level="warning", text="Sectie 4 noemt de Archiefwet zonder peildatum — aanname toegevoegd")
        await tick(job, 0.6)
        await set_step(job, "review", "done", "2 verbeteringen doorgevoerd")

    if "diagram" in keys:
        await set_step(job, "diagram", "running", "Elementen plaatsen en routeren")
        await tick(job, 1.0)
        did = new_id("dia")
        name = "Applicatielandschap"
        S.diagrams[did] = {"id": did, "name": name, "project_id": chat["project_id"]}
        await emit(job, "artifact", kind="diagram", id=did, name=name, url=f"/api/diagrams/{did}.svg")
        await set_step(job, "diagram", "done", "6 elementen, 5 relaties")

    if "build" in keys:
        await set_step(job, "build", "running", "Huisstijl toepassen, inhoudsopgave bijwerken")
        await tick(job, 1.0)
        doc = next((d for d in S.documents.values() if d["project_id"] == chat["project_id"]), None)
        if not doc or job["pipeline"] == "new_document":
            doc = {"id": new_id("doc"), "project_id": chat["project_id"],
                   "name": f"{tpl['profile']['doc_type'] if tpl else 'Document'} {project.get('client') or project.get('name', '')}".strip(),
                   "kind": "docx", "version_ids": []}
            S.documents[doc["id"]] = doc
            sections = written or [dict(o, text=GENERIC_TEXT) for o in outline]
        else:
            prev = S.versions[doc["version_ids"][-1]]
            sections = json.loads(json.dumps(prev["sections"]))
            if job["pipeline"] == "add_chapter":
                sections += written
            elif written:
                sections[0]["text"] = written[0]["text"]
        n = len(doc["version_ids"]) + 1
        vid = new_id("ver")
        changes = {"new_document": "Eerste versie", "add_chapter": "Hoofdstuk toegevoegd", "translate": "Vertaald",
                   "correct": "Taalcorrecties", "review": "Reviewopmerkingen", "enrich": "Verrijkt"}.get(job["pipeline"], "")
        S.versions[vid] = {"id": vid, "document_id": doc["id"], "number": f"0.{n}", "created_at": iso(),
                           "changes": changes, "sections": sections, "job_id": job["id"]}
        doc["version_ids"].append(vid)
        await emit(job, "artifact", kind="document", id=doc["id"], name=doc["name"], version_id=vid,
                   version=f"0.{n}", url=f"/api/versions/{vid}/download")
        await set_step(job, "build", "done", f"v0.{n} opgeslagen")

    await emit(job, "stats", tokens_per_s=round(random.uniform(16, 24), 1), eta_s=0)
    job["status"] = "done"
    job["progress"] = 1.0
    summary = f"{job['title']} afgerond."
    if job["pipeline"] == "diagram":
        text = "**Plaat klaar.** Je vindt *Applicatielandschap* hierboven en onder *Platen* in het documentpaneel."
    else:
        text = (f"**{job['title']} klaar.** Ik heb {len(written)} sectie(s) verwerkt. "
                "Bekijk het resultaat rechts in het documentpaneel of open het in Word.")
    msg = add_message(job["chat_id"], "assistant", text, {"job_id": job["id"]})
    await emit(job, "message", message=msg)
    await emit(job, "job_done", summary=summary)


# --- seed --------------------------------------------------------------------

def seed() -> None:
    profile = json.loads(json.dumps(TEMPLATE_PROFILE))
    t = {"id": new_id("tpl"), "name": "Architecture Quick Scan", "version": 1, "created_at": iso(),
         "filename": "aqs.dotx", "size": 48213,
         "profile_summary": "5 hoofdstukken · 3 velden · huisstijl gedetecteerd", "profile": profile}
    S.templates[t["id"]] = t
    for name, client in (("AQS RIVM", "RIVM"), ("PSA RWS", "Rijkswaterstaat")):
        p = {"id": new_id("prj"), "name": name, "client": client, "template_id": t["id"],
             "privacy_default": "project", "created_at": iso(), "updated_at": iso()}
        S.projects[p["id"]] = p
        c = {"id": new_id("chat"), "project_id": p["id"], "title": "Kick-off", "privacy": "project",
             "internet": False, "roles": {}, "updated_at": iso()}
        S.chats[c["id"]] = c
        S.messages[c["id"]] = []
        add_message(c["id"], "user", "Wat zijn de belangrijkste architectuurthema's voor deze klant?", {"action": "chat"})
        add_message(c["id"], "assistant", CHAT_REPLIES[0], {})
        add_message(c["id"], "event", "12 oudere berichten samengevat om de context klein te houden.",
                    {"compacted_count": 12, "summary": "Besproken: scope, stakeholders en planning."})


# --- static UI -----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__DOCWIZARD_TOKEN__", TOKEN)


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


def main() -> None:
    global SPEED
    ap = argparse.ArgumentParser(description="DocWizard mock-engine")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--seed", action="store_true", help="vul met voorbeeldprojecten")
    ap.add_argument("--speed", type=float, default=SPEED, help="simulatiesnelheid (2 = twee keer zo snel)")
    args = ap.parse_args()
    SPEED = args.speed
    if args.seed:
        seed()
    import uvicorn

    print(f"DocWizard mock op http://{args.host}:{args.port}/  (token: {TOKEN})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
