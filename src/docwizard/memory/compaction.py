"""Compaction: summarise old messages into the chat summary and extract memory items (§13.3)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from docwizard.llm.client import LLMError, count_tokens
from docwizard.store import repo

log = logging.getLogger(__name__)

TRIGGER_RATIO = 0.5   # compact when recent messages exceed this share of the budget
KEEP_RATIO = 0.25     # keep this much of the budget as verbatim recent messages
MIN_KEEP = 4          # always keep the last N messages verbatim
MAX_MSG_CHARS = 6000  # long messages are cut in the summarisation prompt

SUMMARY_SYSTEM = (
    "Je bent een nauwkeurige notulist. Je houdt een lopende samenvatting bij van een gesprek "
    "tussen een gebruiker en een documentassistent. Schrijf in het Nederlands, zakelijk en compact. "
    "Bewaar alles wat later nodig is: doel van het document, gemaakte keuzes en besluiten, "
    "voorkeuren van de gebruiker, genoemde feiten, namen, getallen en open vragen. "
    "Laat beleefdheden en herhalingen weg. Geef alleen de nieuwe samenvatting, zonder inleiding."
)

EXTRACT_SYSTEM = (
    "Haal uit het gesprek de blijvende geheugenitems: feiten (fact), besluiten (decision), "
    "voorkeuren van de gebruiker (preference), aannames (assumption) en open vragen (question). "
    "Elk item is één korte, op zichzelf staande zin in het Nederlands. Neem alleen items op die "
    "later nog van belang zijn. Geen items? Geef een lege lijst."
)


class MemoryItemOut(BaseModel):
    kind: Literal["fact", "decision", "preference", "assumption", "question"]
    text: str = Field(min_length=3)


class MemoryExtraction(BaseModel):
    items: list[MemoryItemOut] = Field(default_factory=list)


def _transcript(msgs: list[dict]) -> str:
    who = {"user": "Gebruiker", "assistant": "Assistent"}
    return "\n\n".join(f"{who.get(m['role'], m['role'])}: {m['content'][:MAX_MSG_CHARS]}" for m in msgs)


def _split_batches(msgs: list[dict], budget: int) -> list[list[dict]]:
    batches, cur, used = [], [], 0
    for m in msgs:
        t = count_tokens(m["content"][:MAX_MSG_CHARS]) + 4
        if cur and used + t > budget:
            batches.append(cur)
            cur, used = [], 0
        cur.append(m)
        used += t
    if cur:
        batches.append(cur)
    return batches


def select_for_compaction(chat_id: str, budget_tokens: int, *, force: bool = False) -> list[dict]:
    msgs = [m for m in repo.list_messages(chat_id, include_compacted=False) if m["role"] in ("user", "assistant")]
    total = sum(m["tokens"] or count_tokens(m["content"]) for m in msgs)
    if not force and total <= budget_tokens * TRIGGER_RATIO:
        return []
    keep, kept_tokens = 0, 0
    for m in reversed(msgs):
        t = m["tokens"] or count_tokens(m["content"])
        if keep >= MIN_KEEP and kept_tokens + t > budget_tokens * KEEP_RATIO:
            break
        keep += 1
        kept_tokens += t
    return msgs[: len(msgs) - keep]


async def compact_if_needed(chat_id: str, client: Any, budget_tokens: int, *, force: bool = False) -> dict | None:
    """Summarise the oldest non-compacted messages when they take > 50% of the budget.
    Returns {"type": "compacted", chat_id, compacted_count, summary, memory_items, upto_message_id} or None."""
    chat = repo.get_chat(chat_id)
    if chat is None:
        return None
    old = select_for_compaction(chat_id, budget_tokens, force=force)
    if not old:
        return None

    summary = chat.get("summary") or ""
    batch_budget = max(1000, int(budget_tokens * 0.4))
    for batch in _split_batches(old, batch_budget):
        prompt = (
            f"Huidige samenvatting:\n{summary or '(nog geen)'}\n\n"
            f"Nieuwe berichten:\n{_transcript(batch)}\n\n"
            "Schrijf de bijgewerkte samenvatting (oude samenvatting + nieuwe berichten samengevoegd)."
        )
        res = await client.chat(
            [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.2,
            think=False,
        )
        if res.text.strip():
            summary = res.text.strip()

    added: list[dict] = []
    try:
        existing = {i["text"].strip().lower() for i in repo.list_memory_items("chat", chat_id, only_valid=False)}
        for batch in _split_batches(old, batch_budget):
            out = await client.chat_json(
                [{"role": "system", "content": EXTRACT_SYSTEM},
                 {"role": "user", "content": _transcript(batch)}],
                MemoryExtraction,
                think=False,
            )
            for item in out.items:
                key = item.text.strip().lower()
                if key in existing:
                    continue
                existing.add(key)
                added.append(repo.add_memory_item("chat", chat_id, item.kind, item.text.strip(),
                                                  source_message_id=batch[-1]["id"]))
    except LLMError as e:  # the summary is the important part; items are best-effort
        log.warning("geheugenitems extraheren mislukt: %s", e)

    upto = old[-1]["id"]
    repo.set_chat_summary(chat_id, summary, upto)
    repo.mark_messages_compacted([m["id"] for m in old])
    return {
        "type": "compacted",
        "chat_id": chat_id,
        "compacted_count": len(old),
        "summary": summary,
        "memory_items": added,
        "upto_message_id": upto,
    }
