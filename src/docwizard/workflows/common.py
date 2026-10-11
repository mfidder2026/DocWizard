"""Shared helpers for pipelines: files, documents, knowledge, research."""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import shutil
from pathlib import Path

from ..config import app_paths
from ..knowledge import base as kb
from ..store import repo
from ..wordengine.compose import docx_to_markdown
from ..wordengine.reader import DocModel
from .jobs import JobContext

MONTHS_NL = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
             "oktober", "november", "december"]


def today_nl() -> str:
    d = _dt.date.today()
    return f"{d.day} {MONTHS_NL[d.month - 1]} {d.year}"


def safe_name(s: str, default: str = "document") -> str:
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", s or "").strip().strip(".")
    return re.sub(r"\s+", " ", s)[:120] or default


def project_dir(project_id: str | None) -> Path:
    d = app_paths().projects / (project_id or "_zonder_project")
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---- reading inputs ----------------------------------------------------------------

def extract_text(path: str | Path, max_chars: int = 400_000) -> str:
    """Markdown/plain text from docx, pdf, md, txt, html (best effort, fully local)."""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in (".docx", ".dotx", ".docm"):
            md, _ = docx_to_markdown(p, skip_front=False)
            return md[:max_chars]
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            return "\n\n".join((pg.extract_text() or "") for pg in reader.pages)[:max_chars]
        if ext in (".html", ".htm"):
            from bs4 import BeautifulSoup
            return BeautifulSoup(p.read_text(errors="ignore"), "html.parser").get_text("\n")[:max_chars]
        if ext in (".drawio", ".xml", ".png", ".jpg", ".jpeg", ".gif", ".emf"):
            return ""
        return p.read_text(errors="ignore")[:max_chars]
    except Exception as e:  # noqa: BLE001
        return f"[Kon {p.name} niet lezen: {e}]"


def attachments(ctx: JobContext) -> list[dict]:
    out = []
    for fid in ctx.params.get("file_ids") or []:
        f = repo.get_file(fid)
        if f and Path(f["path"]).exists():
            out.append(f)
    return out


def ingest_attachments(ctx: JobContext, files: list[dict]) -> list[tuple[dict, str]]:
    """Read attachments, store them in the chat knowledge base, return (file, text)."""
    res = []
    for f in files:
        text = extract_text(f["path"])
        if text.strip() and ctx.chat_id:
            kb.ingest_text("chat", ctx.chat_id, f["name"], text, source_kind="file",
                           project_id=ctx.project_id, chat_id=ctx.chat_id, path=f["path"])
        res.append((f, text))
        ctx.source(f["name"], "file", snippet=text[:200])
    return res


def retrieve(ctx: JobContext, query: str, k: int = 6) -> list[dict]:
    privacy = (ctx.chat or {}).get("privacy", "project")
    try:
        return kb.search(query, chat_id=ctx.chat_id, project_id=ctx.project_id, privacy=privacy, k=k)
    except Exception as e:  # noqa: BLE001
        ctx.log(f"Kennisbank zoeken mislukt: {e}", "warn")
        return []


def snippets_text(hits: list[dict], budget_chars: int = 9000) -> str:
    out, used = [], 0
    for i, h in enumerate(hits, 1):
        t = h["text"].strip()
        block = f"[{i}] {h.get('title', '')} — {h.get('heading_path', '')}\n{t}"
        if used + len(block) > budget_chars:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


# ---- documents ------------------------------------------------------------------------

def current_document(ctx: JobContext, use_attachments: bool = True) -> tuple[dict, dict] | None:
    """(document, version) the job should work on: explicit params, an attached .docx, or the chat's latest."""
    vid = ctx.params.get("version_id")
    if vid:
        v = repo.get_version(vid)
        if v:
            return repo.get_document(v["document_id"]), v
    did = ctx.params.get("document_id")
    if did:
        d = repo.get_document(did)
        if d and d.get("current_version_id"):
            return d, repo.get_version(d["current_version_id"])
    for f in (attachments(ctx) if use_attachments else []):
        if f["name"].lower().endswith((".docx", ".dotx", ".docm")):
            return import_docx(ctx, f["path"], f["name"])
    docs = repo.list_documents(ctx.project_id) if ctx.project_id else []
    for d in docs:
        if d.get("current_version_id"):
            v = repo.get_version(d["current_version_id"])
            if v and (not ctx.chat_id or v.get("chat_id") in (ctx.chat_id, None)):
                return d, v
    if docs and docs[0].get("current_version_id"):
        return docs[0], repo.get_version(docs[0]["current_version_id"])
    return None


def import_docx(ctx: JobContext, path: str, name: str) -> tuple[dict, dict]:
    stem = safe_name(Path(name).stem)
    doc = repo.create_document(ctx.project_id, stem)
    dest = project_dir(ctx.project_id) / "documents" / doc["id"] / f"{stem} (origineel).docx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    v = repo.add_version(doc["id"], str(dest), ctx.id, ctx.chat_id, {"bron": "geüpload"}, file_hash(dest), "origineel")
    return repo.get_document(doc["id"]), v


def next_version_path(doc: dict, label: str) -> Path:
    n = len(repo.list_versions(doc["id"])) + 1
    d = project_dir(doc.get("project_id")) / "documents" / doc["id"]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe_name(doc['name'])} v0.{n} - {safe_name(label)}.docx"


def save_version(ctx: JobContext, doc: dict, path: Path, changes: dict) -> dict:
    v = repo.add_version(doc["id"], str(path), ctx.id, ctx.chat_id, changes, file_hash(path))
    ctx.emit("artifact", kind="document", id=doc["id"], name=path.name, version_id=v["id"],
             url=f"/api/versions/{v['id']}/download")
    return v


def outline_text(model: DocModel, max_level: int = 3) -> str:
    return "\n".join(f"{'  ' * (n.level - 1)}{n.number} {n.title}" for n in model.outline() if n.level <= max_level)


# ---- research --------------------------------------------------------------------------

def internet_allowed(ctx: JobContext) -> bool:
    chat = ctx.chat or {}
    return bool(chat.get("internet")) and chat.get("privacy") != "private"


async def research(ctx: JobContext, queries: list[str], *, key: str, per_query: int = 3,
                   topic: str = "") -> list[dict]:
    """Search → fetch → summarize relevant facts per source → store in chat knowledge. Sequential."""
    from .. import research as rs
    project = repo.get_project(ctx.project_id) if ctx.project_id else None
    sensitive = [t for t in [(project or {}).get("client"), (project or {}).get("name")] if t]
    clean = []
    for q in queries:
        cq, removed = rs.sanitize_query(q, sensitive)
        if removed:
            ctx.log(f"Uit zoekvraag verwijderd (privacy): {', '.join(removed)}")
        if cq.strip():
            clean.append(cq.strip())
    clean = list(dict.fromkeys(clean))[:8]
    if not clean:
        return []
    if ctx.settings.web.confirm_queries:
        ans = await ctx.ask("Deze zoekvragen gaan naar internet. Controleer ze (één per regel) of sla over.",
                            options=[{"value": "ok", "label": "Zoeken"}, {"value": "skip", "label": "Overslaan"}],
                            editable={"kind": "text", "value": "\n".join(clean)})
        if ans.get("value") == "skip":
            ctx.log("Internetonderzoek overgeslagen door gebruiker.")
            return []
        edited = ans.get("edited")
        if isinstance(edited, str) and edited.strip():
            clean = [l.strip() for l in edited.splitlines() if l.strip()]
    found: list[dict] = []
    seen: set[str] = set()
    for qi, q in enumerate(clean, 1):
        ctx.detail(key, f"Zoeken {qi}/{len(clean)}: {q}")
        try:
            results = await rs.search(q, n=per_query + 2, chat=ctx.chat, web=ctx.settings.web)
        except Exception as e:  # noqa: BLE001
            ctx.log(f"Zoeken mislukt voor '{q}': {e}", "warn")
            continue
        for r in results[:per_query]:
            url = r.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                page = await rs.fetch(url, chat=ctx.chat, egress=rs.make_egress(ctx.settings.web))
            except Exception as e:  # noqa: BLE001
                ctx.log(f"Ophalen mislukt: {url} ({e})", "warn")
                continue
            text = (page.get("text") or "")[:12000]
            if len(text) < 200:
                continue
            ctx.detail(key, f"Bron samenvatten: {page.get('title') or url}")
            facts = await ctx.llm([
                {"role": "system", "content": "Je vat webbronnen feitelijk samen voor een architectuurdocument. "
                                              "Alleen wat in de bron staat; geen eigen kennis toevoegen. Nederlands."},
                {"role": "user", "content": f"Onderwerp: {topic}\nZoekvraag: {q}\nBron: {page.get('title')} ({url})\n\n"
                                            f"Tekst:\n{text}\n\nGeef maximaal 8 relevante, controleerbare feiten als "
                                            f"opsomming (met wet-/artikelnummers, jaartallen, namen waar de bron die "
                                            f"geeft). Schrijf 'NIET RELEVANT' als de bron niets bijdraagt."},
            ], key=key, role="fast", temperature=0.2, max_tokens=700, stream=False)
            if "NIET RELEVANT" in facts.upper()[:40]:
                continue
            title = page.get("title") or url
            if ctx.chat_id:
                kb.ingest_text("chat", ctx.chat_id, title, f"Bron: {url}\n\n{facts}\n\n---\n{text[:6000]}",
                               source_kind="web", url=url, project_id=ctx.project_id, chat_id=ctx.chat_id)
            ctx.source(title, "web", url=url, snippet=facts[:300])
            found.append({"title": title, "url": url, "facts": facts, "fetched_at": page.get("fetched_at")})
    return found
