"""Conversation pipeline and the intent router used for 'Auto'."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..memory.context import build_messages
from ..store import repo
from .common import attachments, current_document, ingest_attachments, outline_text, retrieve
from .jobs import JobContext

ACTIONS = {
    "chat": "vraag beantwoorden, meedenken, uitleg (geen document maken/wijzigen)",
    "new_document": "een nieuw document maken op basis van een sjabloon (AQS, PSA, advies, cv …)",
    "add_chapter": "een hoofdstuk/sectie toevoegen aan een bestaand document of documenten samenvoegen",
    "translate": "een document vertalen",
    "correct": "spelling/grammatica/taal van een document corrigeren",
    "review": "een document beoordelen met opmerkingen",
    "enrich": "een bestaande sectie aanvullen/verrijken met extra informatie",
    "diagram": "een architectuurplaat/tekening maken of aanpassen",
}


class Route(BaseModel):
    actie: Literal["chat", "new_document", "add_chapter", "translate", "correct", "review", "enrich", "diagram"]
    doeltaal: str = Field(default="", description="bij vertalen: taalcode zoals en, de, fr")
    sectie: str = Field(default="", description="sectienummer of titel indien genoemd")
    notatie: str = Field(default="", description="bij platen: archimate, c4 of generic indien gevraagd")
    invoegen: bool = Field(default=False, description="plaat in het document plaatsen?")
    uitleg: str = ""


async def route(ctx: JobContext, text: str) -> Route:
    files = [f["name"] for f in attachments(ctx)]
    docs = repo.list_documents(ctx.project_id) if ctx.project_id else []
    r: Route = await ctx.llm([
        {"role": "system", "content": "Je bepaalt welke actie een documentenwizard moet uitvoeren. JSON."},
        {"role": "user", "content": "Mogelijke acties:\n" + "\n".join(f"- {k}: {v}" for k, v in ACTIONS.items())
                                    + f"\n\nBijlagen: {', '.join(files) or 'geen'}\n"
                                    + f"Documenten in project: {', '.join(d['name'] for d in docs[:10]) or 'geen'}\n\n"
                                    + f"Bericht van de gebruiker:\n{text}"},
    ], key="router", role="fast", schema=Route, temperature=0.0, max_tokens=300, stream=False)
    return r


async def chat(ctx: JobContext) -> dict:
    text = ctx.params.get("text", "")
    ctx.plan([("context", "Context en kennis ophalen"), ("antwoord", "Antwoorden")], title="Gesprek")
    async with ctx.step("context"):
        files = attachments(ctx)
        extra = ""
        for f, content in ingest_attachments(ctx, files):
            extra += f"\n\nBijlage '{f['name']}' (begin):\n{content[:4000]}"
        hits = retrieve(ctx, text, k=6)
        target = current_document(ctx, use_attachments=False)
        if target:
            from ..wordengine.reader import DocModel
            try:
                extra += f"\n\nHuidig document '{target[0]['name']}' ({target[1]['number']}), opbouw:\n" + \
                         outline_text(DocModel.open(target[1]["path"]), 2)
            except Exception:  # noqa: BLE001
                pass
        project = repo.get_project(ctx.project_id) if ctx.project_id else None
        system = ("Je bent DocWizard, een lokale assistent voor een enterprise-architect. Je helpt met "
                  "architectuurdocumenten (AQS, PSA, adviezen), denkt kritisch mee en antwoordt in helder "
                  "Nederlands. Gebruik de bronfragmenten waar relevant en verwijs met [nummer]. Verzin geen feiten. "
                  "Als de gebruiker een document wil laten maken of wijzigen, zeg welke actie (Nieuw document, "
                  "Hoofdstuk toevoegen, Vertalen, Corrigeren, Reviewen, Verrijken, Plaat maken) je kunt starten.")
        if project:
            system += f"\nProject: {project['name']}" + (f" (klant: {project['client']})" if project.get("client") else "")
        messages = build_messages(ctx.chat_id, system, text, budget_tokens=ctx.budget, retrieval=hits,
                                  extra_context=extra)
    async with ctx.step("antwoord"):
        answer = await ctx.llm(messages, key="antwoord", temperature=0.5)
        ctx.say(answer)
    await maybe_compact(ctx)
    return {"summary": "Beantwoord"}


async def maybe_compact(ctx: JobContext) -> None:
    if not ctx.chat_id:
        return
    try:
        from ..memory.compaction import compact_if_needed
        info = await compact_if_needed(ctx.chat_id, ctx.client("fast"), ctx.budget)
    except Exception as e:  # noqa: BLE001
        ctx.log(f"Samenvatten van de chat mislukt: {e}", "warn")
        return
    if info:
        n = info.get("compacted_count") or info.get("count") or 0
        msg = repo.add_message(ctx.chat_id, "event", f"{n} oudere berichten samengevat om de context klein te houden.",
                               {"compacted_count": n, "summary": info.get("summary", "")})
        ctx.emit("message", message=msg)


async def auto(ctx: JobContext) -> dict:
    """Route the request, then run the chosen pipeline inside this same job."""
    from . import PIPELINES
    text = ctx.params.get("text", "")
    saved = ctx.restored("route")
    r = Route.model_validate(saved) if saved else await route(ctx, text)
    ctx.checkpoint("route", r.model_dump())
    ctx.log(f"Gekozen actie: {r.actie}" + (f" — {r.uitleg}" if r.uitleg else ""))
    if r.doeltaal:
        ctx.params.setdefault("target_language", r.doeltaal)
    if r.sectie:
        ctx.params.setdefault("section", r.sectie)
        ctx.params.setdefault("after_section", r.sectie)
    if r.notatie:
        ctx.params.setdefault("notation", r.notatie)
    if r.invoegen:
        ctx.params.setdefault("insert", True)
    ctx.job["pipeline"] = r.actie
    result = await PIPELINES[r.actie](ctx)
    if r.actie != "chat":
        await maybe_compact(ctx)
    return result
