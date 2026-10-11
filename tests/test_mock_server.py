"""Smoke-tests voor de UI-mockserver (scripts/mock_server.py)."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"


@pytest.fixture()
def mock(monkeypatch):
    monkeypatch.setenv("DOCWIZARD_TOKEN", TOKEN)
    monkeypatch.setenv("MOCK_SPEED", "50")
    spec = importlib.util.spec_from_file_location("dw_mock_server", ROOT / "scripts" / "mock_server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dw_mock_server"] = mod
    spec.loader.exec_module(mod)
    with TestClient(mod.app) as client:
        client.headers["X-DocWizard-Token"] = TOKEN
        yield mod, client
    sys.modules.pop("dw_mock_server", None)


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_index_injects_token_and_serves_static(mock):
    _, c = mock
    r = c.get("/")
    assert r.status_code == 200
    assert TOKEN in r.text and "__DOCWIZARD_TOKEN__" not in r.text
    for path in ("/app.js", "/i18n.js", "/styles.css", "/js/jobcard.js", "/js/markdown.js"):
        assert c.get(path).status_code == 200, path


def test_token_required(mock):
    mod, _ = mock
    with TestClient(mod.app) as anon:
        assert anon.get("/api/health").status_code == 200
        assert anon.get("/api/projects").status_code == 401
        assert anon.get(f"/api/projects?token={TOKEN}").status_code == 200


def test_settings_and_connection_test(mock):
    _, c = mock
    s = c.get("/api/settings").json()
    assert {x["id"] for x in s["connections"]} >= {"lmstudio", "ollama", "mtplx"}
    s["connections"].append({"id": "eigen", "name": "Eigen", "kind": "openai", "base_url": "http://localhost:9000/v1",
                             "api_key": "k", "model": "m", "context_limit": None, "enabled": True})
    s["roles"]["writer"] = "eigen"
    assert c.put("/api/settings", json=s).json()["roles"]["writer"] == "eigen"
    ok = c.post("/api/connections/test", json=s["connections"][-1]).json()
    assert ok["ok"] and ok["models"] and ok["latency_ms"] >= 0
    bad = c.post("/api/connections/test", json={**s["connections"][-1], "base_url": "http://localhost:9999/v1"}).json()
    assert not bad["ok"] and bad["error"]
    s["connections"][-1]["base_url"] = "localhost"
    assert c.put("/api/settings", json=s).status_code == 422


def test_projects_templates_chats(mock):
    _, c = mock
    t = c.post("/api/templates", files={"file": ("aqs.dotx", b"PK\x03\x04", "application/octet-stream")},
               data={"name": "AQS"}).json()
    assert t["profile"]["outline"]
    assert c.get(f"/api/templates/{t['id']}").json()["name"] == "AQS"
    p = c.post("/api/projects", json={"name": "AQS RIVM", "client": "RIVM", "template_id": t["id"]}).json()
    assert c.patch(f"/api/projects/{p['id']}", json={"name": "AQS RIVM 2"}).json()["name"] == "AQS RIVM 2"
    ch = c.post(f"/api/projects/{p['id']}/chats", json={"privacy": "private", "internet": True}).json()
    assert ch["privacy"] == "private" and ch["internet"] is False
    ch = c.patch(f"/api/chats/{ch['id']}", json={"privacy": "project", "internet": True, "title": "Kick-off"}).json()
    assert ch["internet"] is True and ch["title"] == "Kick-off"
    f = c.post("/api/files", files={"file": ("notes.txt", b"hallo", "text/plain")}, data={"chat_id": ch["id"]}).json()
    assert f["size"] == 5
    assert c.delete(f"/api/projects/{p['id']}").status_code == 200


def test_new_document_job_flow(mock):
    _, c = mock
    p = c.post("/api/projects", json={"name": "Demo"}).json()
    ch = c.post(f"/api/projects/{p['id']}/chats", json={}).json()
    res = c.post(f"/api/chats/{ch['id']}/messages", json={"text": "/nieuw AQS", "file_ids": [], "action": "auto"}).json()
    jid = res["job_id"]
    assert res["message"]["meta"]["action"] == "new_document"

    def question():
        evs = c.get(f"/api/jobs/{jid}/events").json()
        return next((e for e in evs if e["type"] == "question"), None)

    q = wait_for(question)
    assert q["editable"]["kind"] == "json"
    outline = q["editable"]["value"][:2]
    pending = c.get(f"/api/jobs/{jid}").json()
    assert pending["status"] == "waiting" and pending["pending_question"]["question_id"] == q["question_id"]
    assert c.post(f"/api/jobs/{jid}/answer", json={"question_id": q["question_id"], "answer": "approve",
                                                    "edited": outline}).status_code == 200
    job = wait_for(lambda: (j := c.get(f"/api/jobs/{jid}").json())["status"] == "done" and j)
    assert all(s["status"] == "done" for s in job["steps"])
    evs = c.get(f"/api/jobs/{jid}/events").json()
    types = {e["type"] for e in evs}
    assert {"job_started", "step", "source", "question", "artifact", "job_done"} <= types
    assert not types & {"token", "reasoning", "stats"}  # zoals de engine: niet opgeslagen
    assert c.get(f"/api/jobs/{jid}/events?after={evs[-1]['seq']}").json() == []
    assert [j["id"] for j in c.get(f"/api/chats/{ch['id']}/jobs").json()] == [jid]
    assert all(e["chat_id"] == ch["id"] and e["job_id"] == jid and "ts" in e for e in evs)
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs)
    doc_art = next(e for e in evs if e["type"] == "artifact" and e["kind"] == "document")
    dia_art = next(e for e in evs if e["type"] == "artifact" and e["kind"] == "diagram")
    vid = doc_art["version_id"]
    assert [o["title"] for o in c.get(f"/api/versions/{vid}/outline").json()] == [o["title"] for o in outline]
    assert "<h2>" in c.get(f"/api/versions/{vid}/preview").text
    dl = c.get(f"/api/versions/{vid}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    assert c.post(f"/api/versions/{vid}/open").json()["ok"]
    for ext in ("svg", "drawio", "png"):
        assert c.get(f"/api/diagrams/{dia_art['id']}.{ext}").status_code == 200
    docs = c.get(f"/api/projects/{p['id']}/documents").json()
    assert docs[0]["versions"][0]["id"] == vid
    roles = [m["role"] for m in c.get(f"/api/chats/{ch['id']}/messages").json()]
    msgs = c.get(f"/api/chats/{ch['id']}/messages").json()
    assert roles == ["user", "assistant"] and msgs[0]["meta"]["job_id"] == jid


def test_cancel_job(mock):
    _, c = mock
    p = c.post("/api/projects", json={"name": "Demo"}).json()
    ch = c.post(f"/api/projects/{p['id']}/chats", json={}).json()
    jid = c.post(f"/api/chats/{ch['id']}/messages", json={"text": "x", "action": "new_document"}).json()["job_id"]
    wait_for(lambda: c.get(f"/api/jobs/{jid}").json()["status"] in ("running", "waiting"))
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    job = wait_for(lambda: (j := c.get(f"/api/jobs/{jid}").json())["status"] == "cancelled" and j)
    assert job["error"]
    new = c.post(f"/api/jobs/{jid}/retry").json()["job_id"]
    assert new != jid
    wait_for(lambda: c.get(f"/api/jobs/{new}").json()["status"] == "done")
