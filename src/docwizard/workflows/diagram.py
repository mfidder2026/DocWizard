"""Architecture diagram pipeline: LLM writes a semantic spec, the drawio module lays it out."""
from __future__ import annotations

import json
import re

from ..drawio import SPEC_JSON_SCHEMA, SPEC_PROMPT_GUIDE, build_drawio, normalize_spec, render_png, render_svg
from ..llm.jsonutil import extract_json
from ..store import db
from ..wordengine.compose import insert_markdown
from ..wordengine.package import DocxPackage
from ..wordengine.reader import DocModel
from ..wordengine.validate import validate
from .common import (attachments, current_document, extract_text, internet_allowed, next_version_path, outline_text,
                     project_dir, research, retrieve, safe_name, save_version, snippets_text)
from .jobs import JobContext


def diagram_dir(project_id: str | None):
    d = project_dir(project_id) / "diagrams"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def diagram(ctx: JobContext) -> dict:
    notation = ctx.params.get("notation", "")
    ctx.plan([("context", "Context verzamelen"), ("ontwerp", "Plaat ontwerpen (elementen en relaties)"),
              ("tekenen", "Layout en tekenen"), ("controle", "Controle"), ("invoegen", "In document plaatsen")],
             title="Architectuurplaat maken")
    request = ctx.params.get("text", "")
    async with ctx.step("context"):
        bits = []
        for f in attachments(ctx):
            if f["name"].lower().endswith(".drawio"):
                bits.append(f"Bestaande plaat (aanpassen):\n{extract_text(f['path'])[:6000]}")
            else:
                bits.append(f"Bijlage {f['name']}:\n{extract_text(f['path'])[:6000]}")
        hits = retrieve(ctx, request, k=6)
        if hits:
            bits.append("Relevante fragmenten:\n" + snippets_text(hits, 6000))
        target = current_document(ctx, use_attachments=False) if ctx.params.get("insert") else None
        if target:
            bits.append("Opbouw van het document:\n" + outline_text(DocModel.open(target[1]["path"]), 2))
        if internet_allowed(ctx) and ctx.params.get("research", True):
            q = await ctx.llm([{"role": "user", "content": f"Geef 2 korte, algemene zoekvragen (zonder namen van "
                                                           f"organisaties) om referentiearchitecturen te vinden "
                                                           f"voor: {request}. Eén per regel, geen uitleg."}],
                              key="context", role="fast", temperature=0.2, max_tokens=80)
            found = await research(ctx, [l.strip("-• ").strip() for l in q.splitlines() if l.strip()][:2],
                                   key="context", per_query=2, topic=request)
            if found:
                bits.append("Uit referentiebronnen:\n" + "\n".join(f"- {s['title']}: {s['facts'][:600]}" for s in found))
        context = "\n\n".join(bits)

    async with ctx.step("ontwerp"):
        system = ("Je bent een enterprise-architect die heldere architectuurplaten ontwerpt. Je levert alleen de "
                  "semantiek (elementen, groepen/lagen, relaties); layout gebeurt automatisch.\n\n" + SPEC_PROMPT_GUIDE)
        user = (f"{context}\n\nGevraagde plaat: {request}\n"
                + (f"Notatie: {notation}\n" if notation else "Kies de notatie: 'archimate' voor architectuurlagen, "
                                                             "'c4' voor software/containers, anders 'generic'.\n")
                + "Gebruik Nederlandse labels (kort, max 4 woorden), 6–20 elementen, zinvolle relaties. "
                  "Antwoord met alleen de JSON-spec.")
        raw = None
        try:
            res = await ctx.client("writer").chat([{"role": "system", "content": system},
                                                   {"role": "user", "content": user}],
                                                  json_schema=SPEC_JSON_SCHEMA, temperature=0.3, max_tokens=3000,
                                                  cancel=ctx.cancel)
            raw = res.text
        except Exception as e:  # noqa: BLE001
            ctx.log(f"Gestructureerde output niet ondersteund, vrije tekst gebruikt ({e})", "warn")
            raw = await ctx.llm([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                key="ontwerp", temperature=0.3, max_tokens=3000)
        ctx.emit("token", persist=False, key="ontwerp", text=raw[:4000])
        try:
            data = json.loads(raw)
        except ValueError:
            data = extract_json(raw)
        spec = normalize_spec(data)
        ctx.detail("ontwerp", f"{len(spec.nodes)} elementen, {len(spec.edges)} relaties ({spec.notation})")

    async with ctx.step("tekenen"):
        did = db.new_id()
        d = diagram_dir(ctx.project_id)
        name = safe_name(spec.title or request[:60] or "plaat")
        (d / f"{did}.drawio").write_text(build_drawio(spec), encoding="utf-8")
        (d / f"{did}.svg").write_text(render_svg(spec), encoding="utf-8")
        (d / f"{did}.png").write_bytes(render_png(spec, scale=2.0))
        (d / f"{did}.json").write_text(json.dumps({"name": name, "spec": data}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        ctx.emit("artifact", kind="diagram", id=did, name=f"{name}.drawio", url=f"/api/diagrams/{did}.svg")

    async with ctx.step("controle"):
        problems = _layout_check(spec)
        ctx.detail("controle", "geen problemen" if not problems else "; ".join(problems))

    result = {"summary": f"Plaat '{name}' gemaakt", "diagram_id": did}
    async with ctx.step("invoegen"):
        if not target:
            ctx.skip("invoegen", "niet gevraagd")
        else:
            doc, version = target
            section = ctx.params.get("after_section")
            md = f"![{spec.title or name}](diagram:{did})"
            out = next_version_path(doc, "plaat toegevoegd")
            from .documents import _diagram_resolver
            insert_markdown(version["path"], md, out, after_section=section, images=_diagram_resolver(ctx))
            if validate(DocxPackage.open(out)):
                ctx.log("Controle gaf waarschuwingen", "warn")
            v = save_version(ctx, doc, out, {"actie": "plaat toegevoegd", "plaat": name})
            result["version_id"] = v["id"]
    ctx.say(f"Plaat **{name}** is gemaakt ({spec.notation}, {len(spec.nodes)} elementen). Het .drawio-bestand is "
            f"te openen en aan te passen in draw.io. Zeg bijvoorbeeld *'maak de database rood'* of *'voeg "
            f"Digikoppeling toe'* om hem aan te passen.\n\n![{name}](/api/diagrams/{did}.svg)",
            {"diagram_id": did})
    return result


def _layout_check(spec) -> list[str]:
    from ..drawio import compute_layout
    try:
        lay = compute_layout(spec)
    except Exception as e:  # noqa: BLE001
        return [f"layout: {e}"]
    boxes = getattr(lay, "nodes", {}) or {}
    probs = []
    items = list(boxes.items()) if isinstance(boxes, dict) else []
    for i, (a, ra) in enumerate(items):
        for b, rb in items[i + 1:]:
            try:
                if ra.x < rb.x + rb.w and rb.x < ra.x + ra.w and ra.y < rb.y + rb.h and rb.y < ra.y + ra.h:
                    probs.append(f"overlap {a}/{b}")
            except AttributeError:
                return []
    return probs[:5]


__all__ = ["diagram", "diagram_dir", "re"]
