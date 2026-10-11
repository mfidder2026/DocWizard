"""Job engine: one sequential worker, visible steps, live tokens, questions to the user.

Local models run on one machine, so jobs never overlap: they wait in a single
queue. Every state change becomes an event that is stored (for reconnects) and
pushed to the UI over SSE.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from ..config import Settings, load_settings
from ..llm.client import LLMCancelled, LLMClient, client_for_role, count_tokens
from ..store import repo

log = logging.getLogger("docwizard.jobs")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class EventBus:
    """Fan-out of events to SSE subscribers, per chat."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, chat_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._subs.setdefault(chat_id, set()).add(q)
        return q

    def unsubscribe(self, chat_id: str, q: asyncio.Queue) -> None:
        self._subs.get(chat_id, set()).discard(q)

    def publish(self, chat_id: str | None, event: dict) -> None:
        for q in list(self._subs.get(chat_id or "", ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


bus = EventBus()


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job: dict, runner: "JobRunner"):
        self.job = job
        self.id: str = job["id"]
        self.chat_id: str | None = job.get("chat_id")
        self.params: dict = job.get("params") or {}
        self.runner = runner
        self.settings: Settings = load_settings()
        self.cancel = asyncio.Event()
        self.steps: list[dict] = []
        self.chat = repo.get_chat(self.chat_id) if self.chat_id else None
        self.project_id: str | None = (self.chat or {}).get("project_id") or self.params.get("project_id")
        self._token_buffer: dict[str, str] = {}
        self._last_flush = 0.0

    # ---- events ------------------------------------------------------------------
    def emit(self, type_: str, persist: bool = True, **data: Any) -> None:
        ev = {"type": type_, "chat_id": self.chat_id, "job_id": self.id, "ts": _now(), **data}
        if persist:
            repo.add_job_event(self.id, ev)
        bus.publish(self.chat_id, ev)

    def log(self, text: str, level: str = "info") -> None:
        self.emit("log", level=level, text=text)

    def source(self, title: str, kind: str, url: str | None = None, snippet: str = "") -> None:
        self.emit("source", title=title, kind=kind, url=url, snippet=snippet[:300])

    def plan(self, steps: list[tuple[str, str]], title: str) -> None:
        self.steps = [{"key": k, "label": l, "status": "pending", "detail": None,
                       "started_at": None, "ended_at": None} for k, l in steps]
        repo.update_job(self.id, steps=self.steps)
        self.emit("job_started", pipeline=self.job["pipeline"], title=title,
                  steps=[{"key": k, "label": l} for k, l in steps])

    def add_steps(self, steps: list[tuple[str, str]], after: str | None = None) -> None:
        """Steps discovered at runtime (e.g. one per section)."""
        new = [{"key": k, "label": l, "status": "pending", "detail": None, "started_at": None, "ended_at": None}
               for k, l in steps]
        idx = next((i + 1 for i, s in enumerate(self.steps) if s["key"] == after), len(self.steps))
        self.steps[idx:idx] = new
        repo.update_job(self.id, steps=self.steps)
        self.emit("job_started", pipeline=self.job["pipeline"], title=None,
                  steps=[{"key": s["key"], "label": s["label"]} for s in self.steps])

    def _set_step(self, key: str, status: str, detail: str | None = None, label: str | None = None) -> None:
        st = next((s for s in self.steps if s["key"] == key), None)
        if st is None:
            st = {"key": key, "label": label or key, "status": status, "detail": None,
                  "started_at": None, "ended_at": None}
            self.steps.append(st)
        st["status"] = status
        if label:
            st["label"] = label
        if detail is not None:
            st["detail"] = detail
        if status == "running":
            st["started_at"] = _now()
        elif status in ("done", "failed", "skipped"):
            st["ended_at"] = _now()
        done = sum(1 for s in self.steps if s["status"] in ("done", "skipped"))
        progress = done / max(1, len(self.steps))
        repo.update_job(self.id, steps=self.steps, progress=progress)
        self.emit("step", key=key, label=st["label"], status=status, detail=st["detail"], progress=progress)

    @asynccontextmanager
    async def step(self, key: str, label: str | None = None, detail: str | None = None):
        self.check_cancel()
        self._set_step(key, "running", detail, label)
        try:
            yield
        except (JobCancelled, LLMCancelled):
            self._set_step(key, "failed", "geannuleerd")
            raise
        except Exception as e:
            self._set_step(key, "failed", str(e)[:300])
            raise
        else:
            self._set_step(key, "done")

    def skip(self, key: str, detail: str = "") -> None:
        self._set_step(key, "skipped", detail)

    def detail(self, key: str, detail: str) -> None:
        st = next((s for s in self.steps if s["key"] == key), None)
        if st:
            st["detail"] = detail
            self.emit("step", key=key, label=st["label"], status=st["status"], detail=detail, persist=False)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()

    # ---- checkpoints (resume after failure) --------------------------------------------
    def checkpoint(self, name: str, value: Any) -> None:
        """Persist intermediate results so a failed/cancelled job can be resumed without redoing work."""
        job = repo.get_job(self.id) or {}
        result = job.get("result") or {}
        cp = result.get("checkpoint") or {}
        cp[name] = value
        result["checkpoint"] = cp
        repo.update_job(self.id, result=result)

    def restored(self, name: str, default: Any = None) -> Any:
        """Checkpoint value from the job this one resumes (params.resume_from)."""
        src = self.params.get("resume_from")
        if not src:
            return default
        if not hasattr(self, "_resume_cp"):
            job = repo.get_job(src) or {}
            self._resume_cp = (job.get("result") or {}).get("checkpoint") or {}
        value = self._resume_cp.get(name, default)
        if value is not default:
            self.checkpoint(name, value)   # carry forward, so a second failure can resume again
        return value

    # ---- questions -----------------------------------------------------------------
    async def ask(self, text: str, options: list[dict] | None = None, editable: dict | None = None) -> dict:
        """Ask the user and wait. Returns {"value": ..., "edited": ...}."""
        qid = uuid.uuid4().hex[:10]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.runner.questions[qid] = (self.id, fut)
        repo.update_job(self.id, status="waiting")
        self.emit("question", question_id=qid, text=text,
                  options=options or [{"value": "ok", "label": "Akkoord"}], editable=editable)
        waiter = asyncio.ensure_future(self.cancel.wait())
        done, _ = await asyncio.wait({fut, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        self.runner.questions.pop(qid, None)
        if fut not in done:
            raise JobCancelled()
        repo.update_job(self.id, status="running")
        return fut.result()

    # ---- LLM -------------------------------------------------------------------------
    def client(self, role: str = "writer") -> LLMClient:
        roles = dict(self.settings.roles)
        roles.update(((self.chat or {}).get("roles") or {}))
        cid = roles.get(role) or roles.get("writer")
        conn = self.settings.connection(cid) if cid else None
        if conn is None:
            return client_for_role(role, self.settings)
        return LLMClient(conn)

    @property
    def budget(self) -> int:
        return self.settings.quality_context_tokens

    async def llm(self, messages: list[dict], *, key: str, role: str = "writer",
                  schema: type[BaseModel] | None = None, temperature: float = 0.4,
                  max_tokens: int | None = None, stream: bool = True, think: bool | None = None) -> Any:
        """Call the model with live token/reasoning events. Returns text, or a model instance for `schema`."""
        self.check_cancel()
        client = self.client(role)
        started = time.monotonic()
        produced = {"n": 0}

        async def on_token(t: str) -> None:
            produced["n"] += count_tokens(t)
            if stream:
                self._buffer(key, "token", t)

        async def on_reasoning(t: str) -> None:
            self._buffer(key, "reasoning", t)

        max_tokens = max_tokens or self.settings.max_output_tokens
        if think is None:
            # Local reasoning models can spend the whole token budget "thinking" and return no text.
            think = bool(getattr(self.settings, "allow_thinking", False)) and schema is None and role != "fast"
        try:
            if schema is not None:
                res = await client.chat_json(messages, schema, temperature=temperature, max_tokens=max_tokens,
                                             on_token=on_token, on_reasoning=on_reasoning, cancel=self.cancel,
                                             think=think)
            else:
                res = await client.chat(messages, temperature=temperature, max_tokens=max_tokens,
                                        on_token=on_token, on_reasoning=on_reasoning, cancel=self.cancel,
                                        think=think)
        finally:
            self._flush()
        if schema is None and not res.text.strip() and res.reasoning and think is not False:
            self.log("Het model dacht na maar gaf geen tekst; opnieuw zonder denkstappen.", "warn")
            return await self.llm(messages, key=key, role=role, temperature=temperature, max_tokens=max_tokens,
                                  stream=stream, think=False)
        secs = max(0.001, time.monotonic() - started)
        n = produced["n"]
        model = client.conn.model or getattr(client, "_model", "") or ""
        if schema is None:
            n = res.completion_tokens or n
            secs = res.seconds or secs
            model = res.model or model
        self.emit("stats", persist=False, tokens_per_s=round(n / secs, 1), model=model)
        if schema is not None:
            return res
        return res.text

    def _buffer(self, key: str, kind: str, text: str) -> None:
        bk = f"{kind}\x00{key}"
        self._token_buffer[bk] = self._token_buffer.get(bk, "") + text
        if time.monotonic() - self._last_flush > 0.08:
            self._flush()

    def _flush(self) -> None:
        self._last_flush = time.monotonic()
        for bk, text in self._token_buffer.items():
            if text:
                kind, key = bk.split("\x00", 1)
                self.emit(kind, persist=False, key=key, text=text)
        self._token_buffer = {}

    # ---- chat messages ---------------------------------------------------------------
    def say(self, content: str, meta: dict | None = None) -> dict | None:
        if not self.chat_id:
            return None
        msg = repo.add_message(self.chat_id, "assistant", content, {"job_id": self.id, **(meta or {})})
        self.emit("message", message=msg)
        return msg


Pipeline = Callable[[JobContext], Awaitable[dict | None]]


class JobRunner:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.pipelines: dict[str, Pipeline] = {}
        self.contexts: dict[str, JobContext] = {}
        self.questions: dict[str, tuple[str, asyncio.Future]] = {}
        self._worker: asyncio.Task | None = None

    def register(self, name: str, fn: Pipeline) -> None:
        self.pipelines[name] = fn

    def start(self) -> None:
        # Jobs that were running when the app stopped cannot continue mid-generation.
        for j in repo.list_jobs():
            if j["status"] in ("queued", "running", "waiting"):
                repo.update_job(j["id"], status="failed", error="Onderbroken doordat DocWizard werd afgesloten.")
        self._worker = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for ctx in self.contexts.values():
            ctx.cancel.set()
        if self._worker:
            self._worker.cancel()

    def submit(self, chat_id: str | None, pipeline: str, params: dict) -> dict:
        if pipeline not in self.pipelines:
            raise KeyError(f"Onbekende pipeline: {pipeline}")
        job = repo.create_job(chat_id, pipeline, params)
        repo.update_job(job["id"], status="queued")
        position = self.queue.qsize() + (1 if any(c for c in self.contexts.values()) else 0)
        if position and chat_id:
            bus.publish(chat_id, {"type": "log", "chat_id": chat_id, "job_id": job["id"], "ts": _now(),
                                  "level": "info", "text": f"In de wachtrij (positie {position})."})
        self.queue.put_nowait(job["id"])
        return repo.get_job(job["id"])

    def retry(self, job_id: str) -> dict:
        """Resubmit a failed/cancelled job; pipelines skip work found in its checkpoints."""
        job = repo.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        params = {**(job.get("params") or {}), "resume_from": job_id, "auto_approve": True}
        return self.submit(job.get("chat_id"), job["pipeline"], params)

    def cancel(self, job_id: str) -> bool:
        ctx = self.contexts.get(job_id)
        if ctx:
            ctx.cancel.set()
            return True
        job = repo.get_job(job_id)
        if job and job["status"] == "queued":
            repo.update_job(job_id, status="cancelled")
            return True
        return False

    def answer(self, question_id: str, answer: dict) -> bool:
        item = self.questions.get(question_id)
        if not item:
            return False
        _, fut = item
        if not fut.done():
            fut.set_result(answer)
        return True

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = repo.get_job(job_id)
            if not job or job["status"] == "cancelled":
                continue
            ctx = JobContext(job, self)
            self.contexts[job_id] = ctx
            repo.update_job(job_id, status="running")
            try:
                result = await self.pipelines[job["pipeline"]](ctx)
                prev = (repo.get_job(job_id) or {}).get("result") or {}
                repo.update_job(job_id, status="done", result={**prev, **(result or {})}, progress=1.0)
                ctx.emit("job_done", summary=(result or {}).get("summary", ""))
            except (JobCancelled, LLMCancelled):
                repo.update_job(job_id, status="cancelled", error="Geannuleerd door gebruiker")
                ctx.emit("job_failed", error="Geannuleerd")
            except Exception as e:  # noqa: BLE001 — every failure must reach the UI
                log.error("job %s failed: %s", job_id, traceback.format_exc())
                msg = friendly_error(e, ctx)
                repo.update_job(job_id, status="failed", error=msg)
                ctx.emit("job_failed", error=msg)
            finally:
                self.contexts.pop(job_id, None)


def friendly_error(e: Exception, ctx: JobContext | None = None) -> str:
    """Turn technical failures into something the user can act on."""
    import httpx
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        url = str(getattr(getattr(e, "request", None), "url", "")) or "de modelserver"
        return (f"De AI-server is niet bereikbaar ({url}). Start LM Studio, Ollama of MTPLX, of kies in "
                f"Instellingen → AI-verbindingen een server die draait. Klik daarna op Hervatten.")
    if isinstance(e, httpx.ReadTimeout):
        return "De AI-server reageerde te lang niet. Klik op Hervatten om verder te gaan."
    if isinstance(e, httpx.RemoteProtocolError):
        return "De verbinding met de AI-server werd verbroken (is het model gestopt?). Klik op Hervatten."
    return f"{type(e).__name__}: {e}"


runner = JobRunner()
