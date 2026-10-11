"""Assemble the messages for one LLM call within a token budget (ONTWERP §13.2)."""

from __future__ import annotations

from docwizard.llm.client import count_tokens
from docwizard.store import repo

MSG_OVERHEAD = 4
KIND_LABELS = {
    "fact": "Feit", "decision": "Besluit", "preference": "Voorkeur",
    "assumption": "Aanname", "question": "Open vraag",
}


def _tok(text: str) -> int:
    return count_tokens(text) + MSG_OVERHEAD


def _fit_lines(lines: list[str], budget: int) -> list[str]:
    out, used = [], 0
    for line in lines:
        t = count_tokens(line) + 1
        if used + t > budget:
            break
        out.append(line)
        used += t
    return out


def memory_block(chat: dict, budget: int) -> str:
    items = repo.list_memory_items("chat", chat["id"])
    if chat.get("project_id"):
        items = repo.list_memory_items("project", chat["project_id"]) + items
    items.sort(key=lambda i: not i["pinned"])  # pinned first, otherwise stable
    lines = _fit_lines([f"- {KIND_LABELS.get(i['kind'], i['kind'])}: {i['text']}" for i in items], budget)
    return "Geheugen (feiten, besluiten en voorkeuren uit dit gesprek):\n" + "\n".join(lines) if lines else ""


def retrieval_block(retrieval: list[dict], budget: int) -> str:
    parts, used = [], 0
    for i, r in enumerate(retrieval, 1):
        head = r.get("title") or ""
        if r.get("heading_path"):
            head = f"{head} — {r['heading_path']}" if head else r["heading_path"]
        if r.get("url"):
            head += f" ({r['url']})"
        part = f"[{i}] {head}\n{r.get('text', '').strip()}"
        t = count_tokens(part)
        if used + t > budget:
            break
        parts.append(part)
        used += t
    if not parts:
        return ""
    return "Relevante fragmenten uit de bronnen (verwijs met [nummer]):\n\n" + "\n\n".join(parts)


def build_messages(
    chat_id: str,
    system: str,
    task: str,
    *,
    budget_tokens: int,
    retrieval: list[dict] | None = None,
    extra_context: str = "",
) -> list[dict]:
    """system (+ memory + summary) → recent messages that fit → user turn with
    retrieved snippets, extra context and the task. Stable parts come first (KV-cache)."""
    chat = repo.get_chat(chat_id)
    if chat is None:
        raise KeyError(chat_id)

    sys_parts = [system.strip()]
    mem = memory_block(chat, budget_tokens // 10)
    if mem:
        sys_parts.append(mem)
    if chat.get("summary"):
        sys_parts.append("Samenvatting van het eerdere gesprek:\n" + chat["summary"].strip())
    system_msg = {"role": "system", "content": "\n\n".join(p for p in sys_parts if p)}

    fixed = _tok(system_msg["content"]) + _tok(task) + _tok(extra_context)
    remaining = max(0, budget_tokens - fixed)
    rag = retrieval_block(retrieval or [], min(remaining, int(budget_tokens * 0.4)))
    remaining -= count_tokens(rag)

    history = [m for m in repo.list_messages(chat_id, include_compacted=False) if m["role"] in ("user", "assistant")]
    # The current request is usually already stored as the newest user message.
    if history and history[-1]["role"] == "user" and history[-1]["content"].strip() and \
            history[-1]["content"].strip() in task:
        history.pop()
    picked: list[dict] = []
    for m in reversed(history):
        t = _tok(m["content"])
        if t > remaining:
            break
        picked.append({"role": m["role"], "content": m["content"]})
        remaining -= t
    picked.reverse()

    final = "\n\n".join(p for p in (rag, extra_context.strip(), task.strip()) if p)
    return [system_msg, *_alternate(picked + [{"role": "user", "content": final}])]


def _alternate(msgs: list[dict]) -> list[dict]:
    """Some chat templates (e.g. Gemma) require user/assistant alternation starting with user."""
    out: list[dict] = []
    for m in msgs:
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append(dict(m))
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out
