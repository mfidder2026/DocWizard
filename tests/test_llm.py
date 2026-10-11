"""LLM client: unit tests with a mock transport + small live tests (skipped when a server is down).

Live tests run sequentially and use tiny prompts. Disable with DOCWIZARD_LLM_TESTS=0."""

import asyncio
import json
import os

import httpx
import pytest
from pydantic import BaseModel

from docwizard.config import Connection, Settings
from docwizard.llm import (
    LLMCancelled, LLMClient, LLMError, LLMJSONError, ThinkFilter, client_for_role, count_tokens,
    extract_json, split_think,
)


def run(coro):
    return asyncio.run(coro)


def conn(kind="openai", **kw):
    return Connection(id="t", name="Test", kind=kind, base_url="http://test.local/v1", model="m", **kw)


def sse(*chunks, usage=None):
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    if usage:
        lines.append(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def delta(content=None, **extra):
    d = {"content": content} if content is not None else {}
    d.update(extra)
    return {"choices": [{"index": 0, "delta": d}]}


# ---- pure helpers --------------------------------------------------------
def test_count_tokens():
    assert count_tokens("") == 0
    assert count_tokens("a" * 32) == 10


def test_think_filter_split_across_chunks():
    f = ThinkFilter()
    out = [f.feed(x) for x in ["<thi", "nk>plan ", "maken</th", "ink>\n\nHallo", " wereld"]]
    out.append(f.flush())
    content = "".join(c for c, _ in out)
    reasoning = "".join(r for _, r in out)
    assert content == "Hallo wereld"
    assert reasoning == "plan maken"


def test_split_think_lone_close_tag():
    assert split_think("redeneren...</think>Antwoord") == ("Antwoord", "redeneren...")
    assert split_think("geen denken") == ("geen denken", "")


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('Hier is het:\n```json\n{"a": [1, 2]}\n```\nKlaar.', {"a": [1, 2]}),
    ('Uitleg {"a": "x}y", "b": {"c": 2}} en meer {"z": 0}', {"a": "x}y", "b": {"c": 2}}),
    ("<think>{bla}</think> [1, 2, 3]", [1, 2, 3]),
    ('{"a": 1,}', {"a": 1}),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_none():
    with pytest.raises(ValueError):
        extract_json("geen json hier")


# ---- client with mock transport -----------------------------------------
def test_chat_streaming_reasoning_and_usage():
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append(body)
        assert req.headers["authorization"] == "Bearer sk-1"
        return httpx.Response(200, content=sse(
            delta(reasoning_content="denk "), delta(reasoning="na"),
            delta("<think>stil</think>Hal"), delta("lo"),
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        ), headers={"content-type": "text/event-stream"})

    c = LLMClient(conn(api_key="sk-1"), transport=httpx.MockTransport(handler))
    toks, rs = [], []

    async def on_tok(t):
        toks.append(t)

    r = run(c.chat([{"role": "user", "content": "hoi"}], on_token=on_tok, on_reasoning=rs.append, max_tokens=5))
    assert r.text == "Hallo" and "".join(toks) == "Hallo"
    assert r.reasoning == "denk nastil"
    assert (r.prompt_tokens, r.completion_tokens, r.model) == (7, 3, "m")
    assert seen[0]["stream"] is True and seen[0]["max_tokens"] == 5
    assert "options" not in seen[0]


def test_ollama_num_ctx_and_schema_fallback():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, content=sse(delta('{"naam": "rood"}')))

    class Kleur(BaseModel):
        naam: str

    c = LLMClient(conn("ollama"), num_ctx=8192, transport=httpx.MockTransport(handler))
    k = run(c.chat_json([{"role": "user", "content": "kleur"}], Kleur))
    assert k.naam == "rood"
    assert bodies[0]["options"] == {"num_ctx": 8192}
    rf = bodies[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "Kleur"
    assert "response_format" not in bodies[1]
    assert "JSON-schema" in bodies[1]["messages"][0]["content"]


def test_chat_json_repair_round():
    replies = iter(['Sorry, geen JSON', '{"getal": "geen getal"}', '{"getal": 4}'])
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=sse(delta(next(replies))))

    class G(BaseModel):
        getal: int

    c = LLMClient(conn(), transport=httpx.MockTransport(handler))
    assert run(c.chat_json([{"role": "user", "content": "x"}], G)).getal == 4
    assert len(bodies) == 3
    assert bodies[1]["temperature"] < bodies[0]["temperature"]
    assert "Fout" in bodies[2]["messages"][-1]["content"]


def test_chat_json_gives_up():
    def handler(req):
        return httpx.Response(200, content=sse(delta("nee")))

    class G(BaseModel):
        getal: int

    c = LLMClient(conn(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMJSONError):
        run(c.chat_json([{"role": "user", "content": "x"}], G, attempts=2))


def test_list_wrapped_into_single_field_model():
    def handler(req):
        return httpx.Response(200, content=sse(delta('["a", "b"]')))

    class L(BaseModel):
        items: list[str]

    c = LLMClient(conn(), transport=httpx.MockTransport(handler))
    assert run(c.chat_json([{"role": "user", "content": "x"}], L)).items == ["a", "b"]


def test_cancel_returns_partial():
    async def stream():
        yield b'data: {"choices":[{"delta":{"content":"begin"}}]}\n\n'
        await asyncio.sleep(30)
        yield b"data: [DONE]\n\n"

    def handler(req):
        return httpx.Response(200, content=stream())

    async def go():
        cancel = asyncio.Event()
        c = LLMClient(conn(), transport=httpx.MockTransport(handler))

        async def on_tok(t):
            cancel.set()

        with pytest.raises(LLMCancelled) as ei:
            await asyncio.wait_for(c.chat([{"role": "user", "content": "x"}], on_token=on_tok, cancel=cancel), 5)
        return ei.value.partial

    assert run(go()).text == "begin"


def test_http_error():
    c = LLMClient(conn(), transport=httpx.MockTransport(lambda r: httpx.Response(500, text="kapot")))
    with pytest.raises(LLMError) as ei:
        run(c.chat([{"role": "user", "content": "x"}]))
    assert ei.value.status == 500


def test_detection_paths():
    def handler(req):
        p = req.url.path
        if p == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "nomic-embed"}, {"id": "chat-m", "context_length": 4096, "supports_vision": True}]})
        if p == "/api/v0/models":
            return httpx.Response(200, json={"data": [{"id": "chat-m", "max_context_length": 262144,
                                                       "loaded_context_length": 32768, "type": "vlm"}]})
        if p == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if p == "/api/show":
            return httpx.Response(200, json={"model_info": {"gemma4.context_length": 131072}})
        if p == "/v1/embeddings":
            inp = json.loads(req.content)["input"]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(i), 1.0]}
                                                      for i in reversed(range(len(inp)))]})
        return httpx.Response(404)

    t = httpx.MockTransport(handler)
    lm = LLMClient(Connection(id="l", name="L", kind="lmstudio", base_url="http://x/v1"), transport=t)
    assert run(lm.model()) == "chat-m"  # skips embedding models
    assert run(lm.detect_context()) == 32768
    ol = LLMClient(Connection(id="o", name="O", kind="ollama", base_url="http://x/v1", model="g"), transport=t)
    assert run(ol.detect_context()) == 131072
    gen = LLMClient(Connection(id="g", name="G", base_url="http://x/v1", model="chat-m"), transport=t)
    assert run(gen.detect_context()) == 4096
    manual = LLMClient(Connection(id="g", name="G", base_url="http://x/v1", context_limit=999), transport=t)
    assert run(manual.detect_context()) == 999
    res = run(gen.test())
    assert res["ok"] and res["context"] == 4096 and res["models"][1]["vision"] is True
    assert run(gen.embed(["a", "b"])) == [[0.0, 1.0], [1.0, 1.0]]


def test_test_reports_error():
    c = LLMClient(conn(), transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    res = run(c.test())
    assert res["ok"] is False and res["error"] and res["models"] == []


def test_client_for_role():
    s = Settings()
    assert client_for_role("writer", s).conn.id == "lmstudio"
    assert client_for_role("fast", s).conn.id == "mtplx"
    assert client_for_role("thinker", s).conn.id == "lmstudio"  # falls back to writer
    with pytest.raises(LLMError):
        client_for_role("embedding", s)
    s.roles["fast"] = "ollama"
    c = client_for_role("fast", s)
    assert c.conn.kind == "ollama" and c.num_ctx == s.quality_context_tokens + s.max_output_tokens


# ---- live (local servers) -------------------------------------------------
def _up(url: str) -> bool:
    if os.environ.get("DOCWIZARD_LLM_TESTS") == "0":
        return False
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _first_model(url: str, want: str) -> bool:
    try:
        return any(m["id"] == want for m in httpx.get(url, timeout=2).json()["data"])
    except (httpx.HTTPError, ValueError, KeyError):
        return False


MTPLX = _up("http://localhost:8000/v1/models")
OLLAMA = _up("http://localhost:11434/v1/models")
LMSTUDIO = _up("http://localhost:1234/v1/models")


class Antwoord(BaseModel):
    kleur: str


@pytest.mark.skipif(not MTPLX, reason="MTPLX niet bereikbaar")
def test_live_mtplx():
    c = LLMClient(Connection(id="m", name="MTPLX", kind="mtplx", base_url="http://localhost:8000/v1"))
    info = run(c.test())
    assert info["ok"] and info["context"] and info["context"] >= 8192
    r = run(c.chat([{"role": "user", "content": "Zeg alleen het woord: hallo"}], max_tokens=64))
    assert "<think>" not in r.text and r.completion_tokens > 0
    k = run(c.chat_json([{"role": "user", "content": "Welke kleur heeft een banaan? Eén woord."}],
                        Antwoord, max_tokens=64, think=False))
    assert k.kleur


@pytest.mark.skipif(not OLLAMA, reason="Ollama niet bereikbaar")
def test_live_ollama():
    c = LLMClient(Connection(id="o", name="Ollama", kind="ollama", base_url="http://localhost:11434/v1",
                             model="gemma4:12b-it-qat"), num_ctx=8192)
    assert run(c.detect_context())
    r = run(c.chat([{"role": "user", "content": "Zeg alleen: ok"}], max_tokens=64))
    assert r.text or r.reasoning
    r = run(c.chat([{"role": "user", "content": "Zeg alleen: ok"}], max_tokens=64, think=False))
    assert r.text and not r.reasoning


@pytest.mark.skipif(not (LMSTUDIO and _first_model("http://localhost:1234/v1/models",
                                                   "text-embedding-nomic-embed-text-v1.5")),
                    reason="LM Studio / nomic-embed niet beschikbaar")
def test_live_lmstudio_detect_and_embed():
    c = LLMClient(Connection(id="l", name="LM Studio", kind="lmstudio", base_url="http://localhost:1234/v1",
                             model="text-embedding-nomic-embed-text-v1.5"))
    models = run(c.list_models())
    assert any(m.context_length for m in models)
    vecs = run(c.embed(["search_document: archiefwet", "search_document: bewaartermijn"]))
    assert len(vecs) == 2 and len(vecs[0]) >= 256


def test_think_switch_per_kind():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=sse(delta("ok")))

    t = httpx.MockTransport(handler)
    run(LLMClient(conn("ollama"), transport=t).chat([{"role": "user", "content": "x"}], think=False))
    run(LLMClient(conn("mtplx"), transport=t).chat([{"role": "user", "content": "x"}], think=False))
    run(LLMClient(conn("mtplx"), transport=t).chat([{"role": "user", "content": "x"}]))
    assert bodies[0]["reasoning_effort"] == "none" and "chat_template_kwargs" not in bodies[0]
    assert bodies[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in bodies[2] and "reasoning_effort" not in bodies[2]
