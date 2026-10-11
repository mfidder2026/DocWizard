"""Repository API: small synchronous functions returning plain dicts."""

from __future__ import annotations

from typing import Any

from docwizard.store.db import conn, dumps, new_id, now, tx

PRIVACY = ("private", "project", "all")


def _one(sql: str, *args: Any) -> dict | None:
    return conn().execute(sql, args).fetchone()


def _all(sql: str, *args: Any) -> list[dict]:
    return conn().execute(sql, args).fetchall()


def _update(table: str, id_: str, fields: dict, allowed: set[str], json_cols: set[str] = frozenset(),
            touch: bool = True) -> None:
    sets = {k: (dumps(v) if k in json_cols else v) for k, v in fields.items() if k in allowed}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"onbekende velden voor {table}: {sorted(unknown)}")
    if touch:
        sets["updated_at"] = now()
    if not sets:
        return
    cols = ", ".join(f"{k} = ?" for k in sets)
    conn().execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*sets.values(), id_))


# ---- projects ------------------------------------------------------------
_PROJECT_FIELDS = {"name", "client", "template_id", "privacy_default", "brief", "confidential", "folder", "meta"}


def create_project(name: str, client: str = "", template_id: str | None = None,
                   privacy_default: str = "project", brief: str = "", confidential: bool = False) -> dict:
    pid, ts = new_id(), now()
    conn().execute(
        "INSERT INTO project(id, name, client, template_id, privacy_default, brief, confidential,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, name, client, template_id, privacy_default, brief, int(confidential), ts, ts),
    )
    return get_project(pid)  # type: ignore[return-value]


def list_projects() -> list[dict]:
    return _all("SELECT * FROM project ORDER BY updated_at DESC")


def get_project(project_id: str) -> dict | None:
    return _one("SELECT * FROM project WHERE id = ?", project_id)


def update_project(project_id: str, **fields: Any) -> dict | None:
    if "confidential" in fields:
        fields["confidential"] = int(bool(fields["confidential"]))
    _update("project", project_id, fields, _PROJECT_FIELDS, {"meta"})
    return get_project(project_id)


def delete_project(project_id: str) -> None:
    conn().execute("DELETE FROM project WHERE id = ?", (project_id,))


# ---- templates -----------------------------------------------------------
def add_template(name: str, path: str, profile: dict | None = None, hash: str = "", version: int | None = None) -> dict:
    if version is None:
        row = _one("SELECT MAX(version) AS v FROM template WHERE name = ?", name)
        version = (row["v"] or 0) + 1
    tid = new_id()
    conn().execute(
        "INSERT INTO template(id, name, version, path, profile, hash, created_at) VALUES (?,?,?,?,?,?,?)",
        (tid, name, version, str(path), dumps(profile), hash, now()),
    )
    return get_template(tid)  # type: ignore[return-value]


def list_templates() -> list[dict]:
    return _all("SELECT * FROM template ORDER BY created_at DESC")


def get_template(template_id: str) -> dict | None:
    return _one("SELECT * FROM template WHERE id = ?", template_id)


def update_template(template_id: str, **fields: Any) -> dict | None:
    _update("template", template_id, fields, {"name", "path", "profile", "hash", "version"}, {"profile"}, touch=False)
    return get_template(template_id)


def delete_template(template_id: str) -> None:
    conn().execute("DELETE FROM template WHERE id = ?", (template_id,))


# ---- chats ---------------------------------------------------------------
def create_chat(project_id: str | None, title: str = "", privacy: str = "project", internet: bool = False) -> dict:
    if privacy not in PRIVACY:
        raise ValueError(f"ongeldige privacy: {privacy}")
    cid, ts = new_id(), now()
    conn().execute(
        "INSERT INTO chat(id, project_id, title, privacy, internet, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (cid, project_id, title, privacy, int(internet and privacy != "private"), ts, ts),
    )
    return get_chat(cid)  # type: ignore[return-value]


def list_chats(project_id: str | None = None, include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM chat WHERE (? IS NULL OR project_id = ?)"
    if not include_archived:
        sql += " AND archived = 0"
    return _all(sql + " ORDER BY updated_at DESC", project_id, project_id)


def get_chat(chat_id: str) -> dict | None:
    return _one("SELECT * FROM chat WHERE id = ?", chat_id)


def update_chat(chat_id: str, **fields: Any) -> dict | None:
    chat = get_chat(chat_id)
    if chat is None:
        return None
    privacy = fields.get("privacy", chat["privacy"])
    if privacy not in PRIVACY:
        raise ValueError(f"ongeldige privacy: {privacy}")
    if "internet" in fields or "privacy" in fields:
        internet = fields.get("internet", chat["internet"])
        fields["internet"] = int(bool(internet) and privacy != "private")  # private: always offline
    if "archived" in fields:
        fields["archived"] = int(bool(fields["archived"]))
    _update("chat", chat_id, fields, {"title", "privacy", "internet", "roles", "archived", "meta", "project_id"},
            {"roles", "meta"})
    return get_chat(chat_id)


def delete_chat(chat_id: str) -> None:
    conn().execute("DELETE FROM chat WHERE id = ?", (chat_id,))


def touch_chat(chat_id: str) -> None:
    conn().execute("UPDATE chat SET updated_at = ? WHERE id = ?", (now(), chat_id))


def set_chat_summary(chat_id: str, summary: str, upto_message_id: str | None) -> None:
    conn().execute(
        "UPDATE chat SET summary = ?, summary_upto_message_id = ?, updated_at = ? WHERE id = ?",
        (summary, upto_message_id, now(), chat_id),
    )


# ---- messages ------------------------------------------------------------
def add_message(chat_id: str, role: str, content: str, meta: dict | None = None, *,
                model: str | None = None, job_id: str | None = None, tokens: int | None = None) -> dict:
    from docwizard.llm.client import count_tokens

    mid, ts = new_id(), now()
    with tx() as c:
        c.execute(
            "INSERT INTO message(id, chat_id, role, content, tokens, model, job_id, meta, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, chat_id, role, content, tokens if tokens is not None else count_tokens(content),
             model, job_id or (meta or {}).get("job_id"), dumps(meta), ts),
        )
        c.execute("UPDATE chat SET updated_at = ? WHERE id = ?", (ts, chat_id))
    return get_message(mid)  # type: ignore[return-value]


def get_message(message_id: str) -> dict | None:
    return _one("SELECT * FROM message WHERE id = ?", message_id)


def list_messages(chat_id: str, include_compacted: bool = True, limit: int | None = None) -> list[dict]:
    """Messages oldest-first. `limit` returns the newest N (still oldest-first)."""
    sql = "SELECT * FROM message WHERE chat_id = ?"
    if not include_compacted:
        sql += " AND compacted = 0"
    if limit:
        rows = _all(sql + " ORDER BY seq DESC LIMIT ?", chat_id, limit)
        return rows[::-1]
    return _all(sql + " ORDER BY seq", chat_id)


def update_message(message_id: str, **fields: Any) -> dict | None:
    _update("message", message_id, fields, {"content", "meta", "tokens", "model", "job_id"}, {"meta"}, touch=False)
    return get_message(message_id)


def mark_messages_compacted(message_ids: list[str]) -> None:
    with tx() as c:
        c.executemany("UPDATE message SET compacted = 1 WHERE id = ?", [(m,) for m in message_ids])


# ---- files ---------------------------------------------------------------
def add_file(owner_kind: str, owner_id: str, name: str, path: str, mime: str = "",
             size: int | None = None, hash: str = "", meta: dict | None = None) -> dict:
    from pathlib import Path

    if size is None:
        p = Path(path)
        size = p.stat().st_size if p.exists() else 0
    fid = new_id()
    conn().execute(
        "INSERT INTO file(id, owner_kind, owner_id, name, path, mime, size, hash, meta, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (fid, owner_kind, owner_id, name, str(path), mime, size, hash, dumps(meta), now()),
    )
    return get_file(fid)  # type: ignore[return-value]


def get_file(file_id: str) -> dict | None:
    return _one("SELECT * FROM file WHERE id = ?", file_id)


def list_files(owner_kind: str, owner_id: str) -> list[dict]:
    return _all("SELECT * FROM file WHERE owner_kind = ? AND owner_id = ? ORDER BY created_at", owner_kind, owner_id)


def delete_file(file_id: str) -> None:
    conn().execute("DELETE FROM file WHERE id = ?", (file_id,))


# ---- documents & versions ------------------------------------------------
def create_document(project_id: str | None, name: str, kind: str = "docx") -> dict:
    did, ts = new_id(), now()
    conn().execute(
        "INSERT INTO document(id, project_id, name, kind, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (did, project_id, name, kind, ts, ts),
    )
    return get_document(did)  # type: ignore[return-value]


def get_document(document_id: str) -> dict | None:
    return _one("SELECT * FROM document WHERE id = ?", document_id)


def update_document(document_id: str, **fields: Any) -> dict | None:
    _update("document", document_id, fields, {"name", "kind", "current_version_id", "meta"}, {"meta"})
    return get_document(document_id)


def delete_document(document_id: str) -> None:
    conn().execute("DELETE FROM document WHERE id = ?", (document_id,))


def add_version(document_id: str, path: str, job_id: str | None = None, chat_id: str | None = None,
                changes: Any = None, hash: str = "", number: str | None = None) -> dict:
    """New version; numbers run v0.1, v0.2, … unless `number` is given."""
    vid, ts = new_id(), now()
    with tx() as c:
        seq = (c.execute("SELECT MAX(seq) AS s FROM doc_version WHERE document_id = ?", (document_id,))
               .fetchone()["s"] or 0) + 1
        c.execute(
            "INSERT INTO doc_version(id, document_id, number, seq, path, hash, job_id, chat_id, changes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vid, document_id, number or f"0.{seq}", seq, str(path), hash, job_id, chat_id, dumps(changes), ts),
        )
        c.execute("UPDATE document SET current_version_id = ?, updated_at = ? WHERE id = ?", (vid, ts, document_id))
    return get_version(vid)  # type: ignore[return-value]


def get_version(version_id: str) -> dict | None:
    return _one("SELECT * FROM doc_version WHERE id = ?", version_id)


def list_versions(document_id: str) -> list[dict]:
    return _all("SELECT * FROM doc_version WHERE document_id = ? ORDER BY seq", document_id)


def list_documents(project_id: str | None) -> list[dict]:
    """Documents with their versions (`versions`: oldest-first)."""
    docs = _all("SELECT * FROM document WHERE (? IS NULL OR project_id = ?) ORDER BY updated_at DESC",
                project_id, project_id)
    for d in docs:
        d["versions"] = list_versions(d["id"])
    return docs


# ---- jobs ----------------------------------------------------------------
_JOB_FIELDS = {"status", "progress", "steps", "result", "error", "params", "started_at", "ended_at"}


def create_job(chat_id: str | None, pipeline: str, params: dict | None = None) -> dict:
    jid = new_id()
    conn().execute(
        "INSERT INTO job(id, chat_id, pipeline, params, created_at) VALUES (?,?,?,?,?)",
        (jid, chat_id, pipeline, dumps(params or {}), now()),
    )
    return get_job(jid)  # type: ignore[return-value]


def get_job(job_id: str) -> dict | None:
    return _one("SELECT * FROM job WHERE id = ?", job_id)


def list_jobs(chat_id: str | None = None, status: str | None = None) -> list[dict]:
    return _all(
        "SELECT * FROM job WHERE (? IS NULL OR chat_id = ?) AND (? IS NULL OR status = ?) ORDER BY created_at",
        chat_id, chat_id, status, status,
    )


def update_job(job_id: str, **fields: Any) -> dict | None:
    """Fields: status, progress, steps, result, error, params, started_at, ended_at.
    status 'running' / a final status set started_at / ended_at automatically."""
    status = fields.get("status")
    if status == "running":
        fields.setdefault("started_at", now())
    elif status in ("done", "failed", "cancelled"):
        fields.setdefault("ended_at", now())
    _update("job", job_id, fields, _JOB_FIELDS, {"steps", "result", "params"}, touch=False)
    return get_job(job_id)


def add_job_event(job_id: str, event: dict) -> int:
    cur = conn().execute(
        "INSERT INTO job_event(job_id, event, created_at) VALUES (?,?,?)", (job_id, dumps(event), now())
    )
    return int(cur.lastrowid)


def list_job_events(job_id: str, after_id: int = 0) -> list[dict]:
    return _all("SELECT * FROM job_event WHERE job_id = ? AND id > ? ORDER BY id", job_id, after_id)


# ---- memory --------------------------------------------------------------
MEMORY_KINDS = ("fact", "decision", "preference", "assumption", "question")


def add_memory_item(scope_kind: str, scope_id: str, kind: str, text: str,
                    source_message_id: str | None = None, pinned: bool = False) -> dict:
    iid = new_id()
    conn().execute(
        "INSERT INTO memory_item(id, scope_kind, scope_id, kind, text, source_message_id, pinned, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (iid, scope_kind, scope_id, kind, text, source_message_id, int(pinned), now()),
    )
    return _one("SELECT * FROM memory_item WHERE id = ?", iid)  # type: ignore[return-value]


def list_memory_items(scope_kind: str, scope_id: str, only_valid: bool = True) -> list[dict]:
    sql = "SELECT * FROM memory_item WHERE scope_kind = ? AND scope_id = ?"
    if only_valid:
        sql += " AND valid = 1"
    return _all(sql + " ORDER BY pinned DESC, created_at", scope_kind, scope_id)


def update_memory_item(item_id: str, **fields: Any) -> dict | None:
    for k in ("pinned", "valid"):
        if k in fields:
            fields[k] = int(bool(fields[k]))
    _update("memory_item", item_id, fields, {"kind", "text", "pinned", "valid"}, touch=False)
    return _one("SELECT * FROM memory_item WHERE id = ?", item_id)


def delete_memory_item(item_id: str) -> None:
    conn().execute("DELETE FROM memory_item WHERE id = ?", (item_id,))


# ---- glossary / audit ----------------------------------------------------
def add_glossary_term(project_id: str | None, term: str, translation: str = "", language: str = "",
                      note: str = "") -> dict:
    gid = new_id()
    conn().execute(
        "INSERT INTO glossary(id, project_id, term, translation, language, note, created_at) VALUES (?,?,?,?,?,?,?)",
        (gid, project_id, term, translation, language, note, now()),
    )
    return _one("SELECT * FROM glossary WHERE id = ?", gid)  # type: ignore[return-value]


def list_glossary(project_id: str | None) -> list[dict]:
    return _all("SELECT * FROM glossary WHERE project_id IS ? ORDER BY term", project_id)


def add_audit(kind: str, details: dict) -> int:
    cur = conn().execute("INSERT INTO audit(ts, kind, details) VALUES (?,?,?)", (now(), kind, dumps(details)))
    return int(cur.lastrowid)


def list_audit(kind: str | None = None, limit: int = 200) -> list[dict]:
    return _all("SELECT * FROM audit WHERE (? IS NULL OR kind = ?) ORDER BY id DESC LIMIT ?", kind, kind, limit)
