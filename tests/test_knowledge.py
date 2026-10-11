import asyncio
import os

import httpx
import pytest

from docwizard import knowledge
from docwizard.config import Connection
from docwizard.knowledge import chunk_text, fts_query, ingest_text, search
from docwizard.llm import LLMClient
from docwizard.store import close_all, repo, set_db_path


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWIZARD_HOME", str(tmp_path))
    set_db_path(tmp_path / "test.db")
    yield
    close_all()
    set_db_path(None)


DOC = """# AQS RWS

Inleiding over het onderzoek.

## 3 Bevindingen

### IAM

Het identiteits- en toegangsbeheer is versnipperd. Er zijn vier verschillende systemen voor
autorisatie en de beveiliging van accounts laat te wensen over.

### Archivering

De Archiefwet vraagt om duurzame toegankelijkheid. De bewaartermijnen zijn niet vastgelegd.
"""


def test_chunk_heading_paths():
    chunks = chunk_text(DOC)
    paths = [c.heading_path for c in chunks]
    assert paths == ["AQS RWS", "AQS RWS > 3 Bevindingen > IAM", "AQS RWS > 3 Bevindingen > Archivering"]
    assert "versnipperd" in chunks[1].text


def test_chunk_size_limits():
    para = "Dit is een zin over informatiebeveiliging en risico's. " * 40  # ~700 tokens
    text = "# Lang\n\n" + "\n\n".join([para] * 5)
    chunks = chunk_text(text)
    assert len(chunks) >= 4
    assert all(c.tokens <= 800 for c in chunks)
    huge = "# X\n\n" + ("woord " * 4000)  # no sentence boundaries
    assert all(c.tokens <= 800 for c in chunk_text(huge))


def test_fts_query():
    q = fts_query("Wat is de bewaartermijn van het archief in Zürich?")
    assert '"bewaartermijn"*' in q and '"zurich"*' in q
    assert '"de"' not in q and '"wat"' not in q and " OR " in q
    assert fts_query("de het een") == ""


def setup_scopes():
    p1 = repo.create_project("P1")
    p2 = repo.create_project("P2")
    secret = repo.create_project("Geheim", confidential=True)
    c1 = repo.create_chat(p1["id"], privacy="project")
    c1b = repo.create_chat(p1["id"], privacy="project")
    c1priv = repo.create_chat(p1["id"], privacy="private")
    c2 = repo.create_chat(p2["id"])
    ids = {
        "chat1": ingest_text("chat", c1["id"], "Notitie chat1", "Zaaksysteem migratie chat1"),
        "chat1b": ingest_text("chat", c1b["id"], "Notitie chat1b", "Zaaksysteem migratie andere chat"),
        "priv": ingest_text("chat", c1priv["id"], "Privé", "Zaaksysteem migratie privé notitie"),
        "proj1": ingest_text("project", p1["id"], "Projectdoc", "Zaaksysteem migratie projectdocument"),
        "proj2": ingest_text("project", p2["id"], "Ander project", "Zaaksysteem migratie ander project"),
        "secret": ingest_text("project", secret["id"], "Geheim", "Zaaksysteem migratie geheim"),
        "global": ingest_text("global", "nora", "NORA", "Zaaksysteem migratie volgens NORA", source_kind="pack"),
    }
    return {"p1": p1, "c1": c1, "c1priv": c1priv, "c2": c2}, ids


def _sources(results):
    return {r["source_id"] for r in results}


def test_scope_filters():
    ctx, ids = setup_scopes()
    c1 = ctx["c1"]["id"]
    private = _sources(search("zaaksysteem", chat_id=c1, privacy="private", k=20))
    assert private == {ids["chat1"], ids["global"]}
    project = _sources(search("zaaksysteem", chat_id=c1, privacy="project", k=20))
    assert project == {ids["chat1"], ids["chat1b"], ids["proj1"], ids["global"]}
    everything = _sources(search("zaaksysteem", chat_id=c1, privacy="all", k=20))
    assert everything == {ids["chat1"], ids["chat1b"], ids["proj1"], ids["proj2"], ids["global"]}
    # the private chat itself sees its own source
    own = _sources(search("zaaksysteem", chat_id=ctx["c1priv"]["id"], privacy="private", k=20))
    assert ids["priv"] in own


def test_search_result_shape_and_ranking():
    c = repo.create_chat(None)
    sid = ingest_text("chat", c["id"], "AQS", DOC, url="https://example.org/aqs")
    res = search("beveiliging autorisatie", chat_id=c["id"], privacy="private")
    assert res and res[0]["heading_path"].endswith("IAM")
    r = res[0]
    assert set(r) >= {"chunk_id", "source_id", "title", "heading_path", "text", "score", "url"}
    assert r["source_id"] == sid and r["url"] == "https://example.org/aqs" and r["score"] > 0
    # diacritics-insensitive + prefix (bewaartermijnen)
    assert search("bewaartermijn", chat_id=c["id"], privacy="private")[0]["heading_path"].endswith("Archivering")
    assert search("de het", chat_id=c["id"], privacy="private") == []


def test_dedupe_and_delete():
    c = repo.create_chat(None)
    a = ingest_text("chat", c["id"], "x", DOC)
    assert ingest_text("chat", c["id"], "x", DOC) == a
    assert len(knowledge.list_chunks(a)) == 3
    knowledge.delete_source(a)
    assert search("versnipperd", chat_id=c["id"], privacy="private") == []


class FakeEmbedder:
    """Bag-of-letters embedding: deterministic and good enough for ranking tests."""

    async def model(self):
        return "fake-embed"

    async def embed(self, texts):
        return [[t.lower().count(ch) + 0.01 for ch in "abcdefghijklmnopqrstuvwxyz"] for t in texts]


def test_embeddings_rrf():
    c = repo.create_chat(None)
    ingest_text("chat", c["id"], "AQS", DOC)
    n = asyncio.run(knowledge.embed_pending(FakeEmbedder()))
    assert n == 3
    assert asyncio.run(knowledge.embed_pending(FakeEmbedder())) == 0  # nothing left
    res = asyncio.run(knowledge.search_hybrid("versnipperd toegangsbeheer", chat_id=c["id"], privacy="private",
                                              client=FakeEmbedder()))
    assert res[0]["heading_path"].endswith("IAM")
    assert all(0 < r["score"] < 1 for r in res)  # RRF scores


def test_embeddings_disabled_is_fine():
    c = repo.create_chat(None)
    ingest_text("chat", c["id"], "AQS", DOC)
    # no embedding role configured in default settings
    assert asyncio.run(knowledge.embed_pending()) == 0
    res = asyncio.run(knowledge.search_hybrid("archiefwet", chat_id=c["id"], privacy="private"))
    assert res and res[0]["heading_path"].endswith("Archivering")


def _lmstudio_embed() -> bool:
    if os.environ.get("DOCWIZARD_LLM_TESTS") == "0":
        return False
    try:
        ids = [m["id"] for m in httpx.get("http://localhost:1234/v1/models", timeout=2).json()["data"]]
        return "text-embedding-nomic-embed-text-v1.5" in ids
    except (httpx.HTTPError, ValueError, KeyError):
        return False


@pytest.mark.skipif(not _lmstudio_embed(), reason="LM Studio nomic-embed niet beschikbaar")
def test_live_hybrid_with_nomic():
    client = LLMClient(Connection(id="e", name="LM Studio", kind="lmstudio", base_url="http://localhost:1234/v1",
                                  model="text-embedding-nomic-embed-text-v1.5"))
    c = repo.create_chat(None)
    ingest_text("chat", c["id"], "AQS", DOC)
    assert asyncio.run(knowledge.embed_pending(client)) == 3
    res = asyncio.run(knowledge.search_hybrid("wie mag waarbij inloggen", chat_id=c["id"], privacy="private",
                                              client=client))
    assert res  # vector side finds something even without lexical overlap
