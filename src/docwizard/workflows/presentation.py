"""Presentation pipeline: document(s) → storyline (approved) → slide content → house-style .pptx."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.jsonutil import extract_json
from ..store import repo
from ..wordengine.reader import DocModel
from .common import (attachments, current_document, extract_text, file_hash, ingest_attachments, project_dir,
                     retrieve, safe_name, snippets_text, today_nl)
from .jobs import JobCancelled, JobContext

DECK_STYLE = ("Je maakt presentaties voor bestuurders en architecten: één boodschap per dia, de titel is de "
              "conclusie (een volledige, korte zin), maximaal 5 bullets van maximaal 12 woorden, concreet en "
              "zonder vulwoorden. Verzin geen feiten of cijfers; gebruik alleen de bron. Nederlands.")


class StorySlide(BaseModel):
    type: str = "bullets"
    titel: str
    doel: str = Field(default="", description="wat deze dia moet overbrengen, 1 zin")
    bronsecties: list[str] = Field(default_factory=list, description="titels of nummers van brondocument-secties")


class Storyline(BaseModel):
    titel: str
    subtitel: str = ""
    doelgroep: str = ""
    kernboodschap: str = ""
    slides: list[StorySlide]


def _source(ctx: JobContext) -> tuple[str, dict[str, str], str]:
    """(name, {section title/number: markdown}, full markdown) of the source material."""
    files = attachments(ctx)
    sections: dict[str, str] = {}
    parts: list[str] = []
    name = ""
    docx = [f for f in files if f["name"].lower().endswith(".docx")]
    target = None
    if docx:
        model = DocModel.open(docx[0]["path"])
        name = Path(docx[0]["name"]).stem
    else:
        target = current_document(ctx, use_attachments=False)
        model = DocModel.open(target[1]["path"]) if target else None
        name = target[0]["name"] if target else ""
    if model is not None:
        for n in model.outline():
            md = model.to_markdown(n.start, n.end)
            sections[n.number] = md
            sections[n.title.lower()] = md
        parts.append(model.to_markdown())
    for f, text in ingest_attachments(ctx, [f for f in files if f not in docx[:1]]):
        parts.append(f"# {f['name']}\n\n{text}")
        name = name or Path(f["name"]).stem
    return name, sections, "\n\n".join(parts)


def _theme(ctx: JobContext):
    """User .pptx template > explicit theme > house colours from the project's Word template > 'helder'."""
    from ..pptengine import THEMES, theme_from_docx
    tid = ctx.params.get("template_id")
    templates = repo.list_templates()
    ppt_templates = [t for t in templates if str(t.get("path", "")).lower().endswith((".pptx", ".potx"))]
    chosen = next((t for t in ppt_templates if t["id"] == tid), None) or (ppt_templates[-1] if ppt_templates
                                                                           and not ctx.params.get("theme") else None)
    if chosen:
        return None, chosen["path"], f"sjabloon '{chosen['name']}'"
    name = ctx.params.get("theme")
    if name and name in THEMES:
        return THEMES[name], None, f"thema '{name}'"
    project = repo.get_project(ctx.project_id) if ctx.project_id else None
    wt = repo.get_template(project["template_id"]) if project and project.get("template_id") else None
    if wt and str(wt["path"]).lower().endswith((".docx", ".dotx")):
        try:
            return theme_from_docx(wt["path"]), None, f"huisstijlkleuren van '{wt['name']}'"
        except Exception:  # noqa: BLE001
            pass
    return THEMES["helder"], None, "thema 'helder'"


def _deck_files(doc: dict) -> tuple[Path, Path]:
    n = len(repo.list_versions(doc["id"])) + 1
    d = project_dir(doc.get("project_id")) / "documents" / doc["id"]
    d.mkdir(parents=True, exist_ok=True)
    base = f"{safe_name(doc['name'])} v0.{n}"
    return d / f"{base}.pptx", d / f"{base}.deck.json"


def _existing_deck(ctx: JobContext) -> tuple[dict, dict, dict] | None:
    """Latest presentation in this chat/project: (document, version, deck_json)."""
    vid = ctx.params.get("version_id")
    versions = []
    if vid:
        v = repo.get_version(vid)
        if v and str(v["path"]).endswith(".pptx"):
            versions = [v]
    if not versions and ctx.project_id:
        for d in repo.list_documents(ctx.project_id):
            if d.get("kind") == "pptx" and d.get("current_version_id"):
                v = repo.get_version(d["current_version_id"])
                if v and (v.get("chat_id") == ctx.chat_id or not ctx.chat_id):
                    versions.append(v)
    for v in versions:
        p = Path(v["path"]).with_suffix(".deck.json")
        if p.exists():
            return repo.get_document(v["document_id"]), v, json.loads(p.read_text(encoding="utf-8"))
    return None


async def presentation(ctx: JobContext) -> dict:
    existing = _existing_deck(ctx) if ctx.params.get("revise") else None
    if existing:
        return await _revise(ctx, *existing)
    from ..pptengine import DECK_PROMPT_GUIDE, build_pptx, normalize_deck, render_previews

    ctx.plan([("bron", "Bronmateriaal lezen"), ("verhaal", "Verhaallijn opstellen"), ("dias", "Dia's schrijven"),
              ("platen", "Platen tekenen"), ("opmaak", "Opmaak in huisstijl"), ("controle", "Voorvertoning en controle")],
             title="Presentatie maken")
    instruction = ctx.params.get("text", "")
    async with ctx.step("bron"):
        src_name, sections, full = _source(ctx)
        if not full.strip():
            hits = retrieve(ctx, instruction, k=10)
            full = snippets_text(hits, 20000)
        if not full.strip():
            raise RuntimeError("Geen bronmateriaal gevonden. Voeg een document toe of maak er eerst een in dit project.")
        outline = "\n".join(k for k in sections if re.match(r"^\d", k)) or ""
        ctx.detail("bron", f"{src_name or 'bijlagen'} — {len(full.split())} woorden")
        overview = full if len(full) < ctx.budget * 3.2 * 0.45 else None

    async with ctx.step("verhaal"):
        saved = ctx.restored("story")
        if saved:
            story = Storyline.model_validate(saved)
        else:
            n_slides = ctx.params.get("slides") or "8 tot 14"
            if overview is None:
                ctx.detail("verhaal", "Bron is lang; eerst samenvatten")
                overview = await ctx.llm([
                    {"role": "system", "content": "Je vat documenten samen voor een presentatie. Nederlands."},
                    {"role": "user", "content": f"Vat per hoofdstuk samen (kernboodschap, belangrijkste feiten en "
                                                f"cijfers, besluiten, risico's):\n\n{full[: int(ctx.budget * 3.2 * 0.6)]}"},
                ], key="verhaal", role="fast", temperature=0.2, max_tokens=2000)
            story = await ctx.llm([
                {"role": "system", "content": DECK_STYLE + " Antwoord als JSON."},
                {"role": "user", "content": f"Opdracht: {instruction}\n\nBron ({src_name}):\n{overview}\n\n"
                                            + (f"Hoofdstukken in de bron:\n{outline}\n\n" if outline else "")
                                            + f"Maak de verhaallijn (Minto/piramide: begin met de conclusie) voor "
                                              f"{n_slides} dia's. Dia-typen: title, agenda, section, bullets, "
                                              f"two_column, comparison, kpi (2–4 grote getallen), quote, timeline "
                                              f"(stappen/planning), table, chart (alleen met echte cijfers), diagram "
                                              f"(architectuurplaat), closing. Wissel de typen af; niet meer dan 2 "
                                              f"bullets-dia's achter elkaar. Geef per dia de bronsecties."},
            ], key="verhaal", schema=Storyline, temperature=0.3, max_tokens=2500)
            if not ctx.params.get("auto_approve"):
                ans = await ctx.ask("Voorgestelde verhaallijn. Pas titels, volgorde of dia-typen aan en keur goed.",
                                    options=[{"value": "ok", "label": "Goedkeuren en maken"},
                                             {"value": "cancel", "label": "Stoppen"}],
                                    editable={"kind": "json", "value": json.loads(story.model_dump_json())})
                if ans.get("value") == "cancel":
                    raise JobCancelled()
                if ans.get("edited"):
                    try:
                        e = ans["edited"]
                        story = Storyline.model_validate(e if isinstance(e, dict) else json.loads(e))
                    except Exception as ex:  # noqa: BLE001
                        ctx.log(f"Aangepaste verhaallijn niet leesbaar, origineel gebruikt ({ex})", "warn")
            ctx.checkpoint("story", story.model_dump())
        ctx.detail("verhaal", f"{len(story.slides)} dia's — {story.kernboodschap[:120]}")

    slides: list[dict] = [dict(s) for s in (ctx.restored("slides") or [])]
    async with ctx.step("dias"):
        storyline_txt = "\n".join(f"{i + 1}. [{s.type}] {s.titel}" for i, s in enumerate(story.slides))
        for i, s in enumerate(story.slides):
            if i < len(slides):
                continue
            ctx.detail("dias", f"Dia {i + 1}/{len(story.slides)}: {s.titel}")
            if s.type in ("title", "closing", "agenda", "section"):
                slides.append({"type": s.type, "title": s.titel, "subtitle": story.subtitel if s.type == "title" else "",
                               "notes": s.doel})
                ctx.checkpoint("slides", slides)
                continue
            src = "\n\n".join(sections.get(b.lower().strip(), "") or sections.get(b.strip(), "")
                              for b in s.bronsecties)[:9000]
            if not src.strip():
                src = snippets_text(retrieve(ctx, f"{s.titel} {s.doel}", k=5), 6000) or (overview or full)[:6000]
            raw = await ctx.llm([
                {"role": "system", "content": DECK_STYLE + "\n\n" + DECK_PROMPT_GUIDE},
                {"role": "user", "content": f"Presentatie: {story.titel} (doelgroep: {story.doelgroep or 'stuurgroep'})\n"
                                            f"Kernboodschap: {story.kernboodschap}\nVerhaallijn:\n{storyline_txt}\n\n"
                                            f"Bron voor deze dia:\n{src}\n\nMaak dia {i + 1}: type '{s.type}', "
                                            f"werktitel '{s.titel}', doel: {s.doel}\nGeef alleen het JSON-object "
                                            f"van deze ene dia (met type, title, de velden van dit type en notes)."},
            ], key="dias", temperature=0.35, max_tokens=1400)
            try:
                obj = extract_json(raw)
                if isinstance(obj, dict) and isinstance(obj.get("slides"), list) and obj["slides"]:
                    obj = obj["slides"][0]
                if not isinstance(obj, dict):
                    raise ValueError("geen object")
            except Exception:  # noqa: BLE001
                ctx.log(f"Dia {i + 1}: geen geldige JSON, eenvoudige opsomming gebruikt", "warn")
                obj = {"type": "bullets", "title": s.titel, "bullets": [s.doel] if s.doel else []}
            obj.setdefault("type", s.type)
            obj.setdefault("title", s.titel)
            slides.append(obj)
            ctx.checkpoint("slides", slides)

    async with ctx.step("platen"):
        from .diagram import design_diagram, save_diagram
        count = 0
        for i, sl in enumerate(slides):
            img = sl.get("image")
            ref = img.get("ref", "") if isinstance(img, dict) else str(img or "")
            if sl.get("type") not in ("diagram", "image"):
                continue
            if _diagram_exists(ctx, ref) or (sl.get("type") == "image" and ref and not ref.startswith("diagram:")):
                continue
            ctx.detail("platen", f"Plaat voor dia {i + 1}: {sl.get('title')}")
            try:
                req = f"{sl.get('title')}. {sl.get('caption') or sl.get('notes') or ''}"
                spec, data = await design_diagram(ctx, req, (overview or full)[:5000], key="platen")
                did, _ = save_diagram(ctx, spec, data, req)
                sl["image"] = {"ref": f"diagram:{did}", "caption": sl.get("caption") or spec.title or ""}
                count += 1
            except Exception as e:  # noqa: BLE001
                ctx.log(f"Plaat voor dia {i + 1} mislukt ({e}); dia wordt een opsomming.", "warn")
                sl["type"] = "bullets"
        ctx.checkpoint("slides", slides)
        if not count:
            ctx.detail("platen", "geen platen nodig")

    async with ctx.step("opmaak"):
        theme, template, theme_label = _theme(ctx)
        deck_raw = {"title": story.titel, "subtitle": story.subtitel, "audience": story.doelgroep,
                    "author": ctx.settings.author_name, "date": today_nl(), "language": ctx.settings.doc_language,
                    "slides": slides}
        deck = normalize_deck(deck_raw)
        doc = repo.create_document(ctx.project_id, safe_name(ctx.params.get("title") or story.titel), "pptx")
        out, deck_path = _deck_files(doc)
        report = build_pptx(deck, out, theme=theme or "helder", template=template, images=_images(ctx))
        deck_path.write_text(json.dumps({"deck": deck_raw, "theme": getattr(theme, "name", None),
                                         "template": template}, ensure_ascii=False, indent=1), encoding="utf-8")
        for w in report.get("warnings", [])[:8]:
            ctx.log(w, "warn")
        ctx.detail("opmaak", f"{report.get('slides')} dia's, {theme_label}")

    async with ctx.step("controle"):
        pngs = render_previews(deck, theme=theme or "helder", template=template, images=_images(ctx), width=960)
        prev = out.with_suffix(".previews")
        prev.mkdir(exist_ok=True)
        for k, png in enumerate(pngs, 1):
            (prev / f"{k:02d}.png").write_bytes(png)
        v = repo.add_version(doc["id"], str(out), ctx.id, ctx.chat_id,
                             {"actie": "presentatie", "dia's": report.get("slides"), "stijl": theme_label},
                             file_hash(out))
        ctx.emit("artifact", kind="document", id=doc["id"], name=out.name, version_id=v["id"],
                 url=f"/api/versions/{v['id']}/download")
    ctx.say(f"Presentatie **{story.titel}** is gemaakt: {report.get('slides')} dia's in {theme_label}, met "
            f"sprekersnotities.\n\n**Kernboodschap:** {story.kernboodschap}\n\nVraag gerust aanpassingen, "
            f"bijvoorbeeld *'maak dia 4 een tijdlijn'* of *'kortere teksten'*.",
            {"version_id": v["id"], "document_id": doc["id"]})
    return {"summary": f"Presentatie '{story.titel}' gemaakt", "version_id": v["id"]}


async def _revise(ctx: JobContext, doc: dict, version: dict, saved: dict) -> dict:
    from ..pptengine import DECK_PROMPT_GUIDE, THEMES, build_pptx, normalize_deck, render_previews
    ctx.plan([("lezen", "Huidige presentatie lezen"), ("aanpassen", "Aanpassingen maken"),
              ("opmaak", "Opnieuw opmaken"), ("controle", "Voorvertoning")], title="Presentatie aanpassen")
    async with ctx.step("lezen"):
        deck_raw = saved["deck"]
        compact = json.dumps(deck_raw, ensure_ascii=False)
    async with ctx.step("aanpassen"):
        raw = await ctx.llm([
            {"role": "system", "content": DECK_STYLE + "\n\n" + DECK_PROMPT_GUIDE},
            {"role": "user", "content": f"Huidige presentatie (JSON):\n{compact}\n\nGevraagde aanpassing: "
                                        f"{ctx.params.get('text', '')}\n\nGeef de VOLLEDIGE aangepaste presentatie "
                                        f"als JSON in hetzelfde formaat. Laat ongevraagde dia's ongewijzigd."},
        ], key="aanpassen", temperature=0.2, max_tokens=min(8000, len(compact) // 2 + 1500))
        new = extract_json(raw)
        if not isinstance(new, dict) or not new.get("slides"):
            raise RuntimeError("Het model gaf geen bruikbare aangepaste presentatie; probeer het specifieker.")
    async with ctx.step("opmaak"):
        theme = THEMES.get(saved.get("theme") or "", None) or _theme(ctx)[0]
        template = saved.get("template")
        deck = normalize_deck(new)
        out, deck_path = _deck_files(doc)
        report = build_pptx(deck, out, theme=theme or "helder", template=template, images=_images(ctx))
        deck_path.write_text(json.dumps({**saved, "deck": new}, ensure_ascii=False, indent=1), encoding="utf-8")
    async with ctx.step("controle"):
        prev = out.with_suffix(".previews")
        prev.mkdir(exist_ok=True)
        for k, png in enumerate(render_previews(deck, theme=theme or "helder", template=template,
                                                images=_images(ctx), width=960), 1):
            (prev / f"{k:02d}.png").write_bytes(png)
        v = repo.add_version(doc["id"], str(out), ctx.id, ctx.chat_id, {"actie": "presentatie aangepast"},
                             file_hash(out))
        ctx.emit("artifact", kind="document", id=doc["id"], name=out.name, version_id=v["id"],
                 url=f"/api/versions/{v['id']}/download")
    ctx.say(f"Presentatie aangepast (versie {v['number']}, {report.get('slides')} dia's).", {"version_id": v["id"]})
    return {"summary": "Presentatie aangepast", "version_id": v["id"]}


def _diagram_exists(ctx: JobContext, ref: str) -> bool:
    m = re.match(r"diagram:([\w-]+)", ref or "")
    return bool(m) and (project_dir(ctx.project_id) / "diagrams" / f"{m.group(1)}.png").exists()


def _images(ctx: JobContext):
    from .documents import _diagram_resolver
    base = _diagram_resolver(ctx)

    def resolve(ref: str):
        got = base(ref)
        if got:
            return got
        # Media from an attached/current .docx (e.g. 'word/media/image3.png').
        for f in attachments(ctx):
            if f["name"].lower().endswith(".docx"):
                from ..wordengine.package import DocxPackage
                pkg = DocxPackage.open(f["path"])
                if pkg.has(ref):
                    return pkg.raw(ref), ref.rsplit(".", 1)[-1]
        return None
    return resolve


__all__ = ["presentation", "extract_text"]
