import asyncio

import pytest

from docwizard.llm import ChatResult, count_tokens
from docwizard.memory import MemoryExtraction, build_messages, compact_if_needed
from docwizard.store import close_all, repo, set_db_path


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWIZARD_HOME", str(tmp_path))
    set_db_path(tmp_path / "test.db")
    yield
    close_all()
    set_db_path(None)


def chat_with_history(n=10, size=200):
    p = repo.create_project("P")
    c = repo.create_chat(p["id"])
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        repo.add_message(c["id"], role, f"[{i}] " + "woord " * size)
    return p, c


def total_tokens(msgs):
    return sum(count_tokens(m["content"]) + 4 for m in msgs)


def test_build_messages_structure_and_budget():
    p, c = chat_with_history(n=10, size=100)  # ~190 tokens each
    repo.add_memory_item("chat", c["id"], "decision", "Documenttaal is Nederlands", pinned=True)
    repo.add_memory_item("project", p["id"], "fact", "Klant is RIVM")
    repo.set_chat_summary(c["id"], "Eerder besproken: opzet rapport.", None)
    retrieval = [{"title": "Bron", "heading_path": "H > IAM", "text": "IAM-fragment", "url": "https://x"}]
    msgs = build_messages(c["id"], "Je bent DocWizard.", "Schrijf de inleiding.", budget_tokens=1000,
                          retrieval=retrieval, extra_context="Outline: 1 Inleiding")
    sys = msgs[0]
    assert sys["role"] == "system" and sys["content"].startswith("Je bent DocWizard.")
    assert "Documenttaal is Nederlands" in sys["content"] and "Klant is RIVM" in sys["content"]
    assert sys["content"].index("Documenttaal") < sys["content"].index("Klant is RIVM")  # pinned first
    assert "Eerder besproken" in sys["content"]
    last = msgs[-1]
    assert last["role"] == "user" and last["content"].endswith("Schrijf de inleiding.")
    assert "[1] Bron — H > IAM (https://x)" in last["content"] and "Outline: 1 Inleiding" in last["content"]
    assert total_tokens(msgs) <= 1000
    history = msgs[1:-1]
    assert 0 < len(history) < 10
    assert "[9]" in msgs[-2]["content"] or "[9]" in last["content"]  # newest kept
    roles = [m["role"] for m in msgs[1:]]
    assert roles[0] == "user" and all(a != b for a, b in zip(roles, roles[1:]))  # alternation


def test_build_messages_skips_compacted_and_duplicate_task():
    p, c = chat_with_history(n=4, size=5)
    ids = [m["id"] for m in repo.list_messages(c["id"])]
    repo.mark_messages_compacted(ids[:2])
    repo.add_message(c["id"], "user", "Voeg hoofdstuk 3 toe")
    msgs = build_messages(c["id"], "S", "Verzoek van de gebruiker: Voeg hoofdstuk 3 toe", budget_tokens=10000)
    text = "\n".join(m["content"] for m in msgs)
    assert "[0]" not in text and "[1]" not in text and "[2]" in text
    assert text.count("Voeg hoofdstuk 3 toe") == 1


class FakeClient:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append(("chat", messages, kw))
        return ChatResult(text="Nieuwe samenvatting: rapport voor RIVM, taal NL.", reasoning="",
                          prompt_tokens=1, completion_tokens=1, seconds=0.0, model="fake")

    async def chat_json(self, messages, schema, **kw):
        self.calls.append(("json", messages, kw))
        assert schema is MemoryExtraction
        return MemoryExtraction.model_validate({"items": [
            {"kind": "decision", "text": "Het rapport wordt in het Nederlands geschreven."},
            {"kind": "question", "text": "Wie is de opdrachtgever?"},
        ]})


def test_compaction_not_needed():
    _, c = chat_with_history(n=4, size=10)
    assert asyncio.run(compact_if_needed(c["id"], FakeClient(), budget_tokens=32000)) is None


def test_compaction():
    _, c = chat_with_history(n=20, size=200)  # ~20 x 380 tokens = 7600
    repo.set_chat_summary(c["id"], "Oude samenvatting.", None)
    client = FakeClient()
    info = asyncio.run(compact_if_needed(c["id"], client, budget_tokens=8000))
    assert info and info["type"] == "compacted" and info["compacted_count"] > 0
    assert len(info["memory_items"]) == 2
    remaining = repo.list_messages(c["id"], include_compacted=False)
    assert len(remaining) >= 4
    assert sum(m["tokens"] for m in remaining) <= 8000 * 0.5
    chat = repo.get_chat(c["id"])
    assert chat["summary"].startswith("Nieuwe samenvatting")
    all_msgs = repo.list_messages(c["id"])
    assert chat["summary_upto_message_id"] == all_msgs[info["compacted_count"] - 1]["id"]
    kinds = {i["kind"] for i in repo.list_memory_items("chat", c["id"])}
    assert kinds == {"decision", "question"}
    first_prompt = client.calls[0][1][1]["content"]
    assert "Oude samenvatting." in first_prompt and "[0]" in first_prompt
    assert client.calls[0][1][0]["content"].startswith("Je bent een nauwkeurige notulist")  # Dutch prompt
    # running again: under threshold now, and duplicate items are not re-added
    assert asyncio.run(compact_if_needed(c["id"], client, budget_tokens=8000)) is None
    info2 = asyncio.run(compact_if_needed(c["id"], client, budget_tokens=8000, force=True))
    assert info2 is None or info2["memory_items"] == []


def _mtplx_up() -> bool:
    import os

    import httpx

    if os.environ.get("DOCWIZARD_LLM_TESTS") == "0":
        return False
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _mtplx_up(), reason="MTPLX niet bereikbaar")
def test_live_compaction_mtplx():
    from docwizard.config import Connection
    from docwizard.llm import LLMClient

    c = repo.create_chat(None)
    for role, text in [
        ("user", "We maken een adviesrapport over archivering voor een gemeente."),
        ("assistant", "Prima. Welke taal en welke huisstijl?"),
        ("user", "Nederlands, huisstijl AQS. Deadline is vrijdag."),
        ("assistant", "Genoteerd: Nederlands, huisstijl AQS, deadline vrijdag."),
        ("user", "Hoofdstuk 2 moet over de Archiefwet gaan."),
        ("assistant", "Akkoord, hoofdstuk 2 behandelt de Archiefwet."),
    ]:
        repo.add_message(c["id"], role, text)
    client = LLMClient(Connection(id="m", name="MTPLX", kind="mtplx", base_url="http://localhost:8000/v1"))
    info = asyncio.run(compact_if_needed(c["id"], client, budget_tokens=60, force=True))
    assert info and info["compacted_count"] >= 2
    assert repo.get_chat(c["id"])["summary"]
