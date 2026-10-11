import json
import sys

import pytest

from docwizard import config, platform


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWIZARD_HOME", str(tmp_path))
    return tmp_path


def test_defaults():
    s = config.load_settings()
    ids = {c.id: c for c in s.connections}
    assert ids["lmstudio"].base_url == "http://localhost:1234/v1" and ids["lmstudio"].kind == "lmstudio"
    assert ids["ollama"].base_url == "http://localhost:11434/v1" and ids["ollama"].kind == "ollama"
    assert ids["mtplx"].base_url == "http://localhost:8000/v1" and ids["mtplx"].kind == "mtplx"
    assert s.ui_language == "nl" and s.doc_language == "nl"
    assert set(s.roles) >= {"writer", "fast", "embedding", "vision"}


def test_roundtrip_custom_connection_and_language(home):
    s = config.load_settings()
    s.ui_language = "en"
    s.doc_language = "en-GB"
    c = s.add_connection("Mijn vLLM", "http://192.168.1.5:8080", api_key="sk-test", model="qwen")
    c2 = s.add_connection("Mijn vLLM", "http://host:9000/v1/")
    assert c.id == "mijn-vllm" and c2.id == "mijn-vllm-2"
    assert c.base_url == "http://192.168.1.5:8080/v1"  # /v1 appended when no path
    assert c2.base_url == "http://host:9000/v1"
    s.roles["writer"] = c.id
    config.save_settings(s)

    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data["ui_language"] == "en"
    s2 = config.load_settings()
    assert s2.connection("mijn-vllm").api_key == "sk-test"
    assert s2.connection("mijn-vllm").model == "qwen"
    assert s2.roles["writer"] == "mijn-vllm"

    s2.remove_connection("mijn-vllm")
    assert s2.connection("mijn-vllm") is None and s2.roles["writer"] == ""


def test_corrupt_settings_falls_back(home):
    (home / "settings.json").write_text("{nope", encoding="utf-8")
    s = config.load_settings()
    assert len(s.connections) == 3
    assert (home / "settings.json.bad").exists()


def test_app_paths(home):
    p = config.app_paths()
    assert p.home == home and p.db == home / "docwizard.db"
    assert p.templates.is_dir() and p.projects.is_dir() and p.logs.is_dir() and p.cache.is_dir()
    other = home / "elders"
    p2 = config.app_paths(config.Settings(data_dir_override=str(other)))
    assert p2.db == other / "docwizard.db"


def test_default_data_dir_per_os(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCWIZARD_HOME")
    d = platform.default_data_dir()
    assert d.name == "DocWizard"
    if sys.platform == "darwin":
        assert "Application Support" in str(d)


def test_open_helpers_use_os_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(platform.subprocess, "Popen", lambda args: calls.append(args))
    f = tmp_path / "a.docx"
    f.write_bytes(b"x")
    if sys.platform == "win32":
        monkeypatch.setattr(platform.os, "startfile", lambda p: calls.append(["startfile", p]))
    platform.open_with_default_app(f)
    platform.reveal_in_finder(f)
    assert len(calls) == 2
    if sys.platform == "darwin":
        assert calls[0][0] == "open" and calls[1][:2] == ["open", "-R"]
