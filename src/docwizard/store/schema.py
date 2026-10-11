"""Schema migrations. Append new migrations; never edit an applied one."""

V1 = """
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    client TEXT NOT NULL DEFAULT '',
    template_id TEXT,
    privacy_default TEXT NOT NULL DEFAULT 'project',
    brief TEXT NOT NULL DEFAULT '',
    confidential INTEGER NOT NULL DEFAULT 0,
    folder TEXT NOT NULL DEFAULT '',
    meta TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE template (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    path TEXT NOT NULL,
    profile TEXT,
    hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE chat (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES project(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'project' CHECK (privacy IN ('private','project','all')),
    internet INTEGER NOT NULL DEFAULT 0,
    roles TEXT,
    summary TEXT NOT NULL DEFAULT '',
    summary_upto_message_id TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    meta TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX chat_project ON chat(project_id);

CREATE TABLE message (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tokens INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    job_id TEXT,
    compacted INTEGER NOT NULL DEFAULT 0,
    meta TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX message_chat ON message(chat_id, seq);

CREATE TABLE memory_item (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('chat','project')),
    scope_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source_message_id TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX memory_scope ON memory_item(scope_kind, scope_id);

CREATE TABLE document (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'docx',
    current_version_id TEXT,
    meta TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE doc_version (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    number TEXT NOT NULL,
    seq INTEGER NOT NULL,
    path TEXT NOT NULL,
    hash TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    chat_id TEXT,
    changes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX doc_version_doc ON doc_version(document_id, seq);

CREATE TABLE job (
    id TEXT PRIMARY KEY,
    chat_id TEXT REFERENCES chat(id) ON DELETE SET NULL,
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    params TEXT,
    progress REAL NOT NULL DEFAULT 0,
    steps TEXT,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);
CREATE INDEX job_chat ON job(chat_id);

CREATE TABLE job_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX job_event_job ON job_event(job_id, id);

CREATE TABLE file (
    id TEXT PRIMARY KEY,
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    hash TEXT NOT NULL DEFAULT '',
    meta TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX file_owner ON file(owner_kind, owner_id);

CREATE TABLE source (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('chat','project','global')),
    scope_id TEXT NOT NULL DEFAULT '',
    project_id TEXT REFERENCES project(id) ON DELETE CASCADE,
    chat_id TEXT REFERENCES chat(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'file',
    title TEXT NOT NULL DEFAULT '',
    url TEXT,
    path TEXT,
    hash TEXT NOT NULL DEFAULT '',
    fetched_at TEXT,
    meta TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX source_project ON source(project_id);
CREATE INDEX source_chat ON source(chat_id);

CREATE TABLE chunk (
    n INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    embed_model TEXT
);
CREATE INDEX chunk_source ON chunk(source_id, ord);

CREATE VIRTUAL TABLE chunk_fts USING fts5(
    text, heading_path, title,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER chunk_ai AFTER INSERT ON chunk BEGIN
    INSERT INTO chunk_fts(rowid, text, heading_path, title)
    VALUES (new.n, new.text, new.heading_path,
            (SELECT title FROM source WHERE id = new.source_id));
END;

CREATE TRIGGER chunk_ad AFTER DELETE ON chunk BEGIN
    DELETE FROM chunk_fts WHERE rowid = old.n;
END;

CREATE TABLE glossary (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES project(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    translation TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE tm (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES project(id) ON DELETE CASCADE,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX tm_lookup ON tm(project_id, source_lang, target_lang, hash);

CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    details TEXT
);
"""

MIGRATIONS: list[str] = [V1]
