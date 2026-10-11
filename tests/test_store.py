import threading

import pytest

from docwizard.store import close_all, conn, repo, set_db_path


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWIZARD_HOME", str(tmp_path))
    set_db_path(tmp_path / "test.db")
    yield tmp_path / "test.db"
    close_all()
    set_db_path(None)


def test_pragmas_and_schema_version():
    c = conn()
    assert c.execute("PRAGMA journal_mode").fetchone()["journal_mode"] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()["foreign_keys"] == 1
    assert c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"] >= 1


def test_default_path_uses_data_dir(tmp_path):
    set_db_path(None)
    repo.create_project("x")
    assert (tmp_path / "docwizard.db").exists()


def test_projects_crud():
    p = repo.create_project("AQS RIVM", client="RIVM", brief="Architectuur")
    assert len(p["id"]) == 12 and p["created_at"].endswith("Z") and p["confidential"] is False
    assert repo.list_projects()[0]["name"] == "AQS RIVM"
    p2 = repo.update_project(p["id"], name="AQS", confidential=True, meta={"kleur": "blauw"})
    assert p2["name"] == "AQS" and p2["confidential"] is True and p2["meta"] == {"kleur": "blauw"}
    with pytest.raises(ValueError):
        repo.update_project(p["id"], bestaat_niet=1)
    repo.delete_project(p["id"])
    assert repo.get_project(p["id"]) is None


def test_templates_versioning():
    t1 = repo.add_template("Huisstijl", "/x/a.docx", profile={"outline": []})
    t2 = repo.add_template("Huisstijl", "/x/b.docx")
    assert (t1["version"], t2["version"]) == (1, 2)
    assert repo.get_template(t1["id"])["profile"] == {"outline": []}
    assert len(repo.list_templates()) == 2


def test_chat_privacy_forces_offline():
    p = repo.create_project("P")
    c = repo.create_chat(p["id"], "Privé", privacy="private", internet=True)
    assert c["internet"] is False
    c = repo.update_chat(c["id"], privacy="project", internet=True)
    assert c["internet"] is True
    c = repo.update_chat(c["id"], privacy="private")
    assert c["internet"] is False
    with pytest.raises(ValueError):
        repo.create_chat(p["id"], privacy="geheim")
    assert [x["id"] for x in repo.list_chats(p["id"])] == [c["id"]]


def test_messages_order_compaction_and_summary():
    p = repo.create_project("P")
    c = repo.create_chat(p["id"])
    ids = [repo.add_message(c["id"], "user" if i % 2 == 0 else "assistant", f"bericht {i}",
                            meta={"job_id": "j1"} if i == 3 else None)["id"] for i in range(6)]
    msgs = repo.list_messages(c["id"])
    assert [m["id"] for m in msgs] == ids
    assert msgs[3]["meta"] == {"job_id": "j1"} and msgs[3]["job_id"] == "j1"
    assert msgs[0]["tokens"] > 0 and msgs[0]["compacted"] is False
    repo.mark_messages_compacted(ids[:4])
    assert [m["id"] for m in repo.list_messages(c["id"], include_compacted=False)] == ids[4:]
    assert [m["id"] for m in repo.list_messages(c["id"], limit=2)] == ids[4:]
    repo.set_chat_summary(c["id"], "samenvatting", ids[3])
    chat = repo.get_chat(c["id"])
    assert chat["summary"] == "samenvatting" and chat["summary_upto_message_id"] == ids[3]


def test_cascade_delete():
    p = repo.create_project("P")
    c = repo.create_chat(p["id"])
    repo.add_message(c["id"], "user", "x")
    d = repo.create_document(p["id"], "Rapport")
    repo.add_version(d["id"], "/v.docx")
    repo.delete_project(p["id"])
    assert conn().execute("SELECT COUNT(*) AS n FROM message").fetchone()["n"] == 0
    assert conn().execute("SELECT COUNT(*) AS n FROM doc_version").fetchone()["n"] == 0


def test_files():
    f = repo.add_file("chat", "c1", "a.pdf", "/nope/a.pdf", "application/pdf")
    assert f["size"] == 0 and repo.get_file(f["id"])["name"] == "a.pdf"
    assert len(repo.list_files("chat", "c1")) == 1


def test_documents_and_versions():
    p = repo.create_project("P")
    c = repo.create_chat(p["id"])
    d = repo.create_document(p["id"], "Rapport", "docx")
    v1 = repo.add_version(d["id"], "/v1.docx", job_id=None, chat_id=c["id"], changes=["nieuw"])
    v2 = repo.add_version(d["id"], "/v2.docx", changes={"secties": 2})
    assert (v1["number"], v2["number"]) == ("0.1", "0.2")
    assert repo.get_version(v1["id"])["changes"] == ["nieuw"]
    docs = repo.list_documents(p["id"])
    assert docs[0]["current_version_id"] == v2["id"]
    assert [v["number"] for v in docs[0]["versions"]] == ["0.1", "0.2"]


def test_jobs_and_events():
    c = repo.create_chat(None)
    j = repo.create_job(c["id"], "new_document", {"template": "t1"})
    assert j["status"] == "queued" and j["params"] == {"template": "t1"}
    j = repo.update_job(j["id"], status="running", progress=0.2, steps=[{"key": "plan"}])
    assert j["started_at"] and j["steps"] == [{"key": "plan"}]
    j = repo.update_job(j["id"], status="done", result={"version_id": "v"})
    assert j["ended_at"] and j["result"] == {"version_id": "v"}
    e1 = repo.add_job_event(j["id"], {"type": "step", "key": "plan"})
    e2 = repo.add_job_event(j["id"], {"type": "job_done"})
    assert e2 > e1
    assert [e["event"]["type"] for e in repo.list_job_events(j["id"], after_id=e1)] == ["job_done"]
    assert len(repo.list_jobs(chat_id=c["id"])) == 1


def test_memory_items():
    c = repo.create_chat(None)
    a = repo.add_memory_item("chat", c["id"], "decision", "We schrijven in het Nederlands")
    repo.add_memory_item("chat", c["id"], "fact", "Klant is RIVM", pinned=True)
    items = repo.list_memory_items("chat", c["id"])
    assert items[0]["pinned"] is True and len(items) == 2
    repo.update_memory_item(a["id"], valid=False)
    assert len(repo.list_memory_items("chat", c["id"])) == 1
    assert len(repo.list_memory_items("chat", c["id"], only_valid=False)) == 2


def test_audit_and_glossary():
    repo.add_audit("network", {"url": "https://x"})
    assert repo.list_audit("network")[0]["details"] == {"url": "https://x"}
    p = repo.create_project("P")
    repo.add_glossary_term(p["id"], "IAM", "identity and access management", "en")
    assert repo.list_glossary(p["id"])[0]["term"] == "IAM"


def test_threads_have_own_connections():
    c = repo.create_chat(None)
    errors = []

    def worker(n):
        try:
            for i in range(20):
                repo.add_message(c["id"], "user", f"t{n}-{i}")
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            close_all()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(repo.list_messages(c["id"])) == 80
