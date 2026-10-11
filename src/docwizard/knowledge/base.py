"""Knowledge base: ingest, scoped FTS5/BM25 search, optional embeddings fused via RRF."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import unicodedata
from array import array
from typing import Any

from docwizard.knowledge.chunking import chunk_text
from docwizard.store import repo
from docwizard.store.db import conn, dumps, new_id, now, tx

log = logging.getLogger(__name__)

STOPWORDS = set("""
de het een en of in op aan van voor met door bij naar over uit om te tot als dan dat die dit deze
er is zijn was waren wordt worden werd kan kunnen moet moeten mag zal zullen heeft hebben had
niet geen ook nog wel maar want dus al alle wat wie waar wanneer hoe welke welk ik je jij u we wij
ze zij hij hem haar hun ons onze mijn jouw uw zich zo meer veel per via the a an and or of to for
on at by with from is are was were be been this that these those it its as not what which how
""".split())

RRF_K = 60


# ---- ingest ----------------------------------------------------------------
def ingest_text(
    scope_kind: str,
    scope_id: str,
    title: str,
    text: str,
    source_kind: str = "file",
    url: str | None = None,
    project_id: str | None = None,
    chat_id: str | None = None,
    *,
    path: str | None = None,
    meta: dict | None = None,
) -> str:
    """Store a source and its chunks; returns source_id. Re-ingesting identical text
    in the same scope returns the existing source."""
    if scope_kind not in ("chat", "project", "global"):
        raise ValueError(f"ongeldige scope: {scope_kind}")
    if scope_kind == "chat":
        chat_id = chat_id or scope_id
        if project_id is None:
            chat = repo.get_chat(chat_id)
            project_id = chat["project_id"] if chat else None
    elif scope_kind == "project":
        project_id = project_id or scope_id
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = conn().execute(
        "SELECT id FROM source WHERE scope_kind = ? AND scope_id = ? AND hash = ?", (scope_kind, scope_id, digest)
    ).fetchone()
    if existing:
        return existing["id"]

    sid, ts = new_id(), now()
    chunks = chunk_text(text)
    with tx() as c:
        c.execute(
            "INSERT INTO source(id, scope_kind, scope_id, project_id, chat_id, kind, title, url, path, hash,"
            " fetched_at, meta, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, scope_kind, scope_id, project_id, chat_id, source_kind, title, url, path, digest,
             ts if url else None, dumps(meta), ts),
        )
        c.executemany(
            "INSERT INTO chunk(id, source_id, ord, heading_path, text, tokens) VALUES (?,?,?,?,?,?)",
            [(new_id(), sid, i, ch.heading_path, ch.text, ch.tokens) for i, ch in enumerate(chunks)],
        )
    return sid


def get_source(source_id: str) -> dict | None:
    return conn().execute("SELECT * FROM source WHERE id = ?", (source_id,)).fetchone()


def list_sources(*, chat_id: str | None = None, project_id: str | None = None) -> list[dict]:
    return conn().execute(
        "SELECT s.*, (SELECT COUNT(*) FROM chunk WHERE source_id = s.id) AS chunks FROM source s"
        " WHERE (? IS NULL OR chat_id = ?) AND (? IS NULL OR project_id = ?) ORDER BY created_at",
        (chat_id, chat_id, project_id, project_id),
    ).fetchall()


def list_chunks(source_id: str) -> list[dict]:
    return conn().execute(
        "SELECT id, ord, heading_path, text, tokens FROM chunk WHERE source_id = ? ORDER BY ord", (source_id,)
    ).fetchall()


def delete_source(source_id: str) -> None:
    conn().execute("DELETE FROM source WHERE id = ?", (source_id,))


# ---- scope -----------------------------------------------------------------
def _scope_sql(privacy: str, chat_id: str | None, project_id: str | None) -> tuple[str, list[Any]]:
    """SQL condition on alias `s` (source). Global knowledge packs are local and always allowed.
    Sources of other *private* chats are never visible."""
    other_private = ("(s.chat_id IS NULL OR s.chat_id = ?"
                     " OR s.chat_id NOT IN (SELECT id FROM chat WHERE privacy = 'private'))")
    if privacy == "private":
        return "(s.chat_id = ? OR s.scope_kind = 'global')", [chat_id]
    if privacy == "project":
        return (f"((s.chat_id = ? OR s.project_id = ? OR s.scope_kind = 'global') AND {other_private})",
                [chat_id, project_id, chat_id])
    if privacy == "all":
        return (
            "((s.project_id IS NULL OR s.project_id = ? OR s.project_id NOT IN"
            f" (SELECT id FROM project WHERE confidential = 1)) AND {other_private})",
            [project_id, chat_id],
        )
    raise ValueError(f"ongeldige privacy: {privacy}")


def _resolve(chat_id: str | None, project_id: str | None) -> str | None:
    if project_id is None and chat_id:
        chat = repo.get_chat(chat_id)
        project_id = chat["project_id"] if chat else None
    return project_id


# ---- BM25 ------------------------------------------------------------------
def _fold(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)).lower()


def fts_query(query: str) -> str:
    """OR of quoted terms without stopwords; prefix match for longer words (Dutch
    inflection/compounds: 'beveiliging' also finds 'beveiligingsbeleid')."""
    terms: list[str] = []
    for t in re.findall(r"\w+", _fold(query)):
        if (len(t) < 2 and not t.isdigit()) or t in STOPWORDS or t in terms:
            continue
        terms.append(t)
    parts = [f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms]
    return " OR ".join(parts)


def _bm25(match: str, scope: str, args: list[Any], limit: int) -> list[dict]:
    if not match:
        return []
    return conn().execute(
        "SELECT c.id AS chunk_id, c.source_id, s.title, c.heading_path, c.text, s.url, s.kind AS source_kind,"
        " -bm25(chunk_fts, 1.0, 0.6, 0.4) AS score"
        " FROM chunk_fts JOIN chunk c ON c.n = chunk_fts.rowid JOIN source s ON s.id = c.source_id"
        f" WHERE chunk_fts MATCH ? AND {scope} ORDER BY bm25(chunk_fts, 1.0, 0.6, 0.4) LIMIT ?",
        (match, *args, limit),
    ).fetchall()


def search(
    query: str,
    *,
    chat_id: str | None,
    project_id: str | None = None,
    privacy: str = "project",
    k: int = 8,
    query_vec: list[float] | None = None,
    embed_model: str | None = None,
) -> list[dict]:
    """Scoped search. BM25 only, or BM25 + cosine (RRF) when `query_vec` is given."""
    project_id = _resolve(chat_id, project_id)
    scope, args = _scope_sql(privacy, chat_id, project_id)
    lexical = _bm25(fts_query(query), scope, args, k * 3 if query_vec else k)
    if not query_vec:
        return lexical[:k]
    semantic = _vector(query_vec, scope, args, k * 3, embed_model)
    return _rrf([lexical, semantic], k)


def _rrf(lists: list[list[dict]], k: int) -> list[dict]:
    fused: dict[str, dict] = {}
    for results in lists:
        for rank, r in enumerate(results):
            entry = fused.setdefault(r["chunk_id"], {**r, "score": 0.0})
            entry["score"] += 1.0 / (RRF_K + rank + 1)
    return sorted(fused.values(), key=lambda r: r["score"], reverse=True)[:k]


# ---- embeddings ------------------------------------------------------------
def _pack(vec: list[float]) -> bytes:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array("f", (x / norm for x in vec)).tobytes()


def _unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def _vector(query_vec: list[float], scope: str, args: list[Any], limit: int, model: str | None) -> list[dict]:
    q = _unpack(_pack(query_vec))
    rows = conn().execute(
        "SELECT c.id AS chunk_id, c.source_id, s.title, c.heading_path, c.text, s.url, s.kind AS source_kind,"
        " c.embedding FROM chunk c JOIN source s ON s.id = c.source_id"
        f" WHERE c.embedding IS NOT NULL AND (? IS NULL OR c.embed_model = ?) AND {scope}",
        (model, model, *args),
    ).fetchall()
    scored = []
    for r in rows:
        v = _unpack(r.pop("embedding"))
        if len(v) != len(q):
            continue
        r["score"] = sum(a * b for a, b in zip(q, v, strict=True))
        scored.append(r)
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


def _prefix(model: str, kind: str) -> str:
    """Task prefixes that nomic-embed models expect."""
    return f"search_{kind}: " if "nomic" in model.lower() else ""


def _embedding_client(settings: Any = None) -> Any | None:
    from docwizard.llm.client import LLMError, client_for_role

    try:
        return client_for_role("embedding", settings)
    except LLMError:
        return None


async def embed_pending(client: Any = None, *, settings: Any = None, batch: int = 32, limit: int = 2000) -> int:
    """Embed chunks without an embedding for the embedding model. Returns the count.
    No-op (0) when no embedding role is configured or the server is unreachable."""
    client = client or _embedding_client(settings)
    if client is None:
        return 0
    try:
        model = await client.model()
        rows = conn().execute(
            "SELECT c.id, c.heading_path, c.text FROM chunk c"
            " WHERE c.embedding IS NULL OR c.embed_model IS NOT ? LIMIT ?", (model, limit)
        ).fetchall()
        done = 0
        for i in range(0, len(rows), batch):
            part = rows[i : i + batch]
            vecs = await client.embed([_prefix(model, "document") + f"{r['heading_path']}\n{r['text']}"[:6000]
                                       for r in part])
            with tx() as c:
                c.executemany(
                    "UPDATE chunk SET embedding = ?, embed_model = ? WHERE id = ?",
                    [(_pack(v), model, r["id"]) for r, v in zip(part, vecs, strict=True)],
                )
            done += len(part)
        return done
    except Exception as e:  # noqa: BLE001 - embeddings are optional
        log.warning("embeddings overgeslagen: %s", e)
        return 0


async def search_hybrid(
    query: str,
    *,
    chat_id: str | None,
    project_id: str | None = None,
    privacy: str = "project",
    k: int = 8,
    client: Any = None,
    settings: Any = None,
) -> list[dict]:
    """Like `search`, but adds embedding similarity when an embedding model is available."""
    client = client or _embedding_client(settings)
    if client is not None:
        try:
            model = await client.model()
            [vec] = await client.embed([_prefix(model, "query") + query])
            return search(query, chat_id=chat_id, project_id=project_id, privacy=privacy, k=k,
                          query_vec=vec, embed_model=model)
        except Exception as e:  # noqa: BLE001
            log.warning("embedding-zoeken niet beschikbaar: %s", e)
    return search(query, chat_id=chat_id, project_id=project_id, privacy=privacy, k=k)
