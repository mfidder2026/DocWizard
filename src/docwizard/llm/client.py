"""Async OpenAI-compatible client for local servers (LM Studio, Ollama, MTPLX, generic)."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from docwizard.config import Connection, Settings, load_settings
from docwizard.llm.jsonutil import extract_json
from docwizard.llm.thinking import ThinkFilter, split_think

TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)
OPTIONAL_KEYS = ("response_format", "stream_options", "chat_template_kwargs", "reasoning_effort")
Callback = Callable[[str], Awaitable[None] | None]
M = TypeVar("M", bound=BaseModel)


@dataclass
class ModelInfo:
    id: str
    context_length: int | None = None
    vision: bool | None = None


@dataclass
class ChatResult:
    text: str
    reasoning: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    model: str


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMCancelled(LLMError):
    """Raised when the cancel event fires; `.partial` holds what was generated so far."""

    def __init__(self, partial: ChatResult):
        super().__init__("geannuleerd")
        self.partial = partial


class LLMJSONError(LLMError):
    def __init__(self, message: str, last_text: str = ""):
        super().__init__(message)
        self.last_text = last_text


def count_tokens(text: str) -> int:
    return math.ceil(len(text) / 3.2)


async def _call(cb: Callback | None, text: str) -> None:
    if cb and text:
        r = cb(text)
        if inspect.isawaitable(r):
            await r


def _ctx_from(d: dict) -> int | None:
    for k in ("loaded_context_length", "context_length", "max_context_length", "max_model_len", "context_window"):
        v = d.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return None


class LLMClient:
    def __init__(self, conn: Connection, *, num_ctx: int | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.conn = conn
        self.transport = transport
        self.base = conn.base_url.rstrip("/")
        self.root = re.sub(r"/v1$", "", self.base)
        # Ollama: context window to request per call (Ollama otherwise truncates silently).
        self.num_ctx = num_ctx or conn.context_limit
        self._model = conn.model

    # ---- plumbing -----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.conn.api_key:
            h["Authorization"] = f"Bearer {self.conn.api_key}"
        return h

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT, headers=self._headers(), transport=self.transport)

    async def _get_json(self, url: str) -> Any:
        async with self._http() as h:
            r = await h.get(url)
            r.raise_for_status()
            return r.json()

    async def model(self) -> str:
        """Configured model, or the first non-embedding model the server lists."""
        if not self._model:
            models = await self.list_models()
            chat_models = [m for m in models if "embed" not in m.id.lower()] or models
            if not chat_models:
                raise LLMError(f"{self.conn.name}: geen modellen gevonden")
            self._model = chat_models[0].id
        return self._model

    # ---- discovery ----------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        data = await self._get_json(f"{self.base}/models")
        out = []
        for m in data.get("data", []):
            vision = m.get("supports_vision")
            if vision is None and "modalities" in m:
                vision = "image" in m["modalities"].get("input", [])
            out.append(ModelInfo(id=m["id"], context_length=_ctx_from(m), vision=vision))
        if self.conn.kind == "lmstudio":
            try:
                extra = {m["id"]: m for m in (await self._get_json(f"{self.root}/api/v0/models")).get("data", [])}
                for mi in out:
                    if mi.id in extra:
                        mi.context_length = _ctx_from(extra[mi.id]) or mi.context_length
                        if extra[mi.id].get("type") == "vlm":
                            mi.vision = True
            except (httpx.HTTPError, ValueError):
                pass
        return out

    async def detect_context(self) -> int | None:
        """Context length of the configured model (manual override wins)."""
        if self.conn.context_limit:
            return self.conn.context_limit
        try:
            model = await self.model()
            if self.conn.kind == "ollama":
                # Loaded model: /api/ps shows the context window actually in use.
                ps = await self._get_json(f"{self.root}/api/ps")
                for m in ps.get("models") or []:
                    if m.get("name") == model and isinstance(m.get("context_length"), int):
                        return m["context_length"]
                async with self._http() as h:
                    r = await h.post(f"{self.root}/api/show", json={"model": model})
                    r.raise_for_status()
                    info = r.json()
                m = re.search(r"num_ctx\s+(\d+)", info.get("parameters") or "")
                if m:
                    return int(m.group(1))
                for k, v in (info.get("model_info") or {}).items():
                    if k.endswith(".context_length") and isinstance(v, int):
                        return v
                return None
            for mi in await self.list_models():
                if mi.id == model:
                    return mi.context_length
        except (httpx.HTTPError, ValueError, LLMError):
            return None
        return None

    # ---- chat ---------------------------------------------------------
    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.4,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        schema_name: str = "output",
        think: bool | None = None,
        on_token: Callback | None = None,
        on_reasoning: Callback | None = None,
        cancel: asyncio.Event | None = None,
    ) -> ChatResult:
        model = await self.model()
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
            }
        if self.conn.kind == "ollama":
            body["options"] = {"num_ctx": self.num_ctx or 16384}
        if think is False:
            # Ollama honours reasoning_effort; MTPLX/vLLM/llama.cpp-style servers the template flag.
            if self.conn.kind == "ollama":
                body["reasoning_effort"] = "none"
            else:
                body["chat_template_kwargs"] = {"enable_thinking": False}

        state = _StreamState(on_token, on_reasoning)
        t0 = time.monotonic()
        stream_task = asyncio.ensure_future(self._stream_with_fallback(body, state))
        if cancel is None:
            await stream_task
        else:
            cancel_task = asyncio.ensure_future(cancel.wait())
            done, _ = await asyncio.wait({stream_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            cancel_task.cancel()
            if stream_task not in done:
                stream_task.cancel()  # closes the HTTP stream
                try:
                    await stream_task
                except (asyncio.CancelledError, Exception):
                    pass
                raise LLMCancelled(state.result(model, messages, time.monotonic() - t0))
            stream_task.result()
        res = state.result(model, messages, time.monotonic() - t0)
        if not res.text.strip() and res.reasoning.strip() and (think is False or json_schema is not None):
            # Some servers (e.g. LM Studio with Qwen + structured output) stream the actual answer in the
            # reasoning channel. Without thinking, that channel *is* the answer.
            res.text, res.reasoning = res.reasoning.strip(), ""
        return res

    async def _stream_with_fallback(self, body: dict, state: _StreamState) -> None:
        try:
            await self._stream(body, state)
        except LLMError as e:
            optional = [k for k in OPTIONAL_KEYS if k in body]
            if not (e.status and 400 <= e.status < 500 and e.status not in (401, 403, 404) and optional):
                raise
            # Server rejected an optional feature (structured output, usage, thinking switch):
            # retry without them and rely on prompt + JSON extraction.
            await self._stream({k: v for k, v in body.items() if k not in optional}, state)

    async def _stream(self, body: dict, state: _StreamState) -> None:
        async with self._http() as h:
            async with h.stream("POST", f"{self.base}/chat/completions", json=body) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode("utf-8", "replace")[:500]
                    raise LLMError(f"{self.conn.name}: HTTP {r.status_code}: {detail}", r.status_code)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if "error" in chunk:
                        raise LLMError(f"{self.conn.name}: {chunk['error']}")
                    await state.handle(chunk)
        await state.finish()

    async def chat_json(
        self,
        messages: list[dict],
        schema: type[M],
        *,
        temperature: float = 0.2,
        attempts: int = 3,
        use_server_schema: bool = True,
        **kw: Any,
    ) -> M:
        """Structured output: server schema → extraction → repair round → lower temperature."""
        js = schema.model_json_schema()
        hint = (
            "Antwoord uitsluitend met één JSON-object dat voldoet aan dit JSON-schema, zonder uitleg:\n"
            + json.dumps(js, ensure_ascii=False)
        )
        msgs = _with_system_hint(messages, hint)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", schema.__name__)[:64]
        last_text, error = "", ""
        for attempt in range(attempts):
            temp = temperature if attempt == 0 else max(0.0, temperature - 0.1 * attempt)
            res = await self.chat(
                msgs, temperature=temp,
                json_schema=js if use_server_schema else None, schema_name=name, **kw,
            )
            last_text = res.text
            if not res.text.strip() and not res.reasoning.strip():
                raise LLMError(f"{self.conn.name}: het model gaf een leeg antwoord (is het model nog geladen?)")
            try:
                return _validate(schema, extract_json(res.text))
            except (ValueError, ValidationError) as e:
                error = str(e)[:1500]
            msgs = msgs + [
                {"role": "assistant", "content": res.text[:4000]},
                {"role": "user", "content": (
                    "Je antwoord was geen geldige JSON volgens het schema. Fout:\n"
                    f"{error}\nGeef alleen de gecorrigeerde JSON, zonder uitleg."
                )},
            ]
        raise LLMJSONError(f"geen geldige JSON na {attempts} pogingen: {error}", last_text)

    # ---- embeddings / test ---------------------------------------------
    async def embed(self, texts: list[str], *, batch: int = 64) -> list[list[float]]:
        model = await self.model()
        out: list[list[float]] = []
        async with self._http() as h:
            for i in range(0, len(texts), batch):
                r = await h.post(f"{self.base}/embeddings", json={"model": model, "input": texts[i : i + batch]})
                if r.status_code >= 400:
                    raise LLMError(f"{self.conn.name}: embeddings HTTP {r.status_code}: {r.text[:300]}", r.status_code)
                data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
                out.extend(d["embedding"] for d in data)
        return out

    async def test(self) -> dict:
        t0 = time.monotonic()
        try:
            models = await self.list_models()
            latency = int((time.monotonic() - t0) * 1000)
            context = await self.detect_context() if models else None
            return {
                "ok": True,
                "models": [{"id": m.id, "context_length": m.context_length, "vision": m.vision} for m in models],
                "latency_ms": latency,
                "context": context,
                "error": None,
            }
        except Exception as e:  # noqa: BLE001 - surfaced to the settings UI
            return {
                "ok": False, "models": [], "latency_ms": int((time.monotonic() - t0) * 1000),
                "context": None, "error": f"{type(e).__name__}: {e}",
            }


class _StreamState:
    def __init__(self, on_token: Callback | None, on_reasoning: Callback | None):
        self.on_token, self.on_reasoning = on_token, on_reasoning
        self.filter = ThinkFilter()
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.usage: dict = {}

    async def _emit(self, content: str, reasoning: str) -> None:
        if reasoning:
            self.reasoning.append(reasoning)
            await _call(self.on_reasoning, reasoning)
        if content:
            self.text.append(content)
            await _call(self.on_token, content)

    async def handle(self, chunk: dict) -> None:
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            r = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if r:
                await self._emit("", r)
            c = delta.get("content") or ""
            if c:
                await self._emit(*self.filter.feed(c))

    async def finish(self) -> None:
        await self._emit(*self.filter.flush())

    def result(self, model: str, messages: list[dict], seconds: float) -> ChatResult:
        raw = "".join(self.text)
        text, extra = split_think(raw) if "</think>" in raw else (raw.strip(), "")
        reasoning = "".join(self.reasoning) + extra
        return ChatResult(
            text=text,
            reasoning=reasoning.strip(),
            prompt_tokens=self.usage.get("prompt_tokens")
            or sum(count_tokens(str(m.get("content", ""))) for m in messages),
            completion_tokens=self.usage.get("completion_tokens") or count_tokens(raw + reasoning),
            seconds=seconds,
            model=model,
        )


def _with_system_hint(messages: list[dict], hint: str) -> list[dict]:
    msgs = [dict(m) for m in messages]
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = f"{msgs[0]['content']}\n\n{hint}"
    else:
        msgs.insert(0, {"role": "system", "content": hint})
    return msgs


def _validate(schema: type[M], obj: Any) -> M:
    fields = list(schema.model_fields)
    if isinstance(obj, list) and len(fields) == 1:
        obj = {fields[0]: obj}
    return schema.model_validate(obj)


def client_for_role(role: str, settings: Settings | None = None) -> LLMClient:
    """Client for a model role. writer/fast/thinker fall back to writer; embedding/vision do not."""
    s = settings or load_settings()
    conn_id = s.roles.get(role) or ""
    conn = s.connection(conn_id) if conn_id else None
    if conn is None and role not in ("embedding", "vision"):
        conn = s.connection(s.roles.get("writer", "")) or next((c for c in s.connections if c.enabled), None)
    if conn is None or not conn.enabled:
        raise LLMError(f"geen (actieve) verbinding ingesteld voor rol '{role}'")
    num_ctx = min(conn.context_limit or 10**9, s.quality_context_tokens + s.max_output_tokens)
    return LLMClient(conn, num_ctx=num_ctx)
