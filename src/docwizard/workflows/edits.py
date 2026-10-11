"""In-place pipelines: translate, correct (tracked changes), review (comments)."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..llm.client import count_tokens
from ..store import repo
from ..wordengine.inplace import Comments, RevisionIds, Segment, apply_text, extract_segments, set_language
from ..wordengine.ns import qn
from ..wordengine.package import DocxPackage
from ..wordengine.reader import DocModel
from ..wordengine.validate import validate
from .common import current_document, next_version_path, save_version
from .jobs import JobContext

LANGS = {"nl": ("Nederlands", "nl-NL"), "en": ("Engels (Brits)", "en-GB"), "de": ("Duits", "de-DE"),
         "fr": ("Frans", "fr-FR"), "es": ("Spaans", "es-ES"), "it": ("Italiaans", "it-IT"),
         "pl": ("Pools", "pl-PL"), "pt": ("Portugees", "pt-PT")}

_ITEM = re.compile(r"\[\[(\d+)\]\][ \t]?")

TAG_RULES = ("Elke regel begint met [[nummer]]; geef exact dezelfde nummers terug, één regel per nummer. "
             "Tags zoals <1>…</1> markeren opmaak (vet, cursief, link): houd ze om de overeenkomstige woorden. "
             "Tokens zoals <x1/> zijn vaste elementen (tab, veld, afbeelding): laat ze exact staan, in dezelfde "
             "volgorde. Voeg geen uitleg toe.")


def _batches(segs: list[Segment], max_tokens: int) -> list[list[tuple[int, Segment]]]:
    out, cur, used = [], [], 0
    for i, s in enumerate(segs):
        t = count_tokens(s.tagged) + 6
        if cur and used + t > max_tokens:
            out.append(cur)
            cur, used = [], 0
        cur.append((i, s))
        used += t
    if cur:
        out.append(cur)
    return out


def _numbered(batch: list[tuple[int, Segment]]) -> str:
    return "\n".join(f"[[{i}]] {s.tagged}" for i, s in batch)


def _parse(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    parts = _ITEM.split(text)
    for k in range(1, len(parts) - 1, 2):
        out[int(parts[k])] = parts[k + 1].rstrip("\n").strip("\n")
    return {k: v.rstrip() for k, v in out.items()}


def _load(ctx: JobContext):
    target = current_document(ctx)
    if not target:
        raise RuntimeError("Geen document gevonden. Voeg een .docx toe of maak eerst een document in dit project.")
    doc, version = target
    return doc, version, DocxPackage.open(version["path"])


# ---- translate ---------------------------------------------------------------------------

async def translate(ctx: JobContext) -> dict:
    lang = (ctx.params.get("target_language") or ctx.params.get("language") or "en").lower()[:2]
    lang_name, lang_tag = LANGS.get(lang, (lang, lang))
    ctx.plan([("lezen", "Document lezen"), ("vertalen", f"Vertalen naar {lang_name}"),
              ("herstel", "Ontbrekende stukken opnieuw"), ("opslaan", "Opslaan met behoud van lay-out")],
             title=f"Vertalen naar {lang_name}")
    async with ctx.step("lezen"):
        doc, version, pkg = _load(ctx)
        segs = extract_segments(pkg, include_headers=ctx.params.get("headers", True))
        glossary = [g for g in repo.list_glossary(ctx.project_id) if not g.get("language") or g["language"] == lang] \
            if ctx.project_id else []
        gl = "\n".join(f"- {g['term']} → {g['translation'] or g['term']}" for g in glossary[:80])
        ctx.detail("lezen", f"{len(segs)} tekstdelen")
    system = (f"Je bent een professionele vertaler van architectuur- en overheidsdocumenten. Vertaal naar "
              f"{lang_name}. Behoud betekenis, toon en terminologie; vertaal namen van systemen, organisaties en "
              f"wetten niet tenzij er een gangbare vertaling is (zet dan de originele naam erachter). "
              + TAG_RULES + (f"\nVerplichte terminologie:\n{gl}" if gl else ""))
    results: dict[int, str] = {}
    async with ctx.step("vertalen"):
        batches = _batches(segs, min(1400, ctx.budget // 6))
        for bi, batch in enumerate(batches, 1):
            ctx.detail("vertalen", f"Deel {bi}/{len(batches)}")
            ans = await ctx.llm([{"role": "system", "content": system},
                                 {"role": "user", "content": _numbered(batch)}],
                                key="vertalen", temperature=0.2,
                                max_tokens=int(sum(count_tokens(s.tagged) for _, s in batch) * 1.8) + 200)
            got = _parse(ans)
            for i, s in batch:
                if i in got and got[i].strip():
                    results[i] = got[i]
    async with ctx.step("herstel"):
        missing = [(i, s) for i, s in enumerate(segs) if i not in results]
        for n, (i, s) in enumerate(missing, 1):
            ctx.detail("herstel", f"{n}/{len(missing)}")
            ans = await ctx.llm([{"role": "system", "content": system},
                                 {"role": "user", "content": f"[[{i}]] {s.tagged}"}], key="herstel", temperature=0.1)
            got = _parse(ans) or {i: ans.strip()}
            if got.get(i):
                results[i] = got[i]
    async with ctx.step("opslaan"):
        fallbacks = 0
        for i, s in enumerate(segs):
            if i in results:
                fallbacks += 0 if apply_text(s, results[i]) else 1
        set_language(pkg, lang_tag)
        out = next_version_path(doc, f"vertaald {lang}")
        pkg.save(out)
        problems = validate(pkg)
        if problems:
            ctx.log("Controle: " + "; ".join(problems[:5]), "warn")
        v = save_version(ctx, doc, out, {"actie": "vertaald", "taal": lang, "delen": len(results)})
    note = f" Bij {fallbacks} alinea's ging opmaak binnen de zin verloren; controleer die." if fallbacks else ""
    ctx.say(f"**{doc['name']}** is vertaald naar {lang_name} ({len(results)}/{len(segs)} tekstdelen), "
            f"met behoud van de lay-out. Versie {v['number']}.{note}", {"version_id": v["id"]})
    return {"summary": f"Vertaald naar {lang_name}", "version_id": v["id"]}


# ---- correct --------------------------------------------------------------------------------

LEVELS = {
    "fouten": "Corrigeer alleen spel-, grammatica-, interpunctie- en typefouten. Verander verder niets.",
    "licht": "Corrigeer fouten en verbeter waar nodig de leesbaarheid licht (kortere zinnen, actieve vorm, "
             "consistente terminologie). Verander de inhoud niet.",
    "b1": "Herschrijf naar helder taalniveau B1 (korte zinnen, gewone woorden, actieve vorm) zonder inhoud te "
          "verliezen. Vaktermen mogen blijven als ze nodig zijn.",
}


async def correct(ctx: JobContext) -> dict:
    level = ctx.params.get("level", "licht")
    ctx.plan([("lezen", "Document lezen"), ("corrigeren", "Corrigeren"), ("opslaan", "Wijzigingen bijhouden en opslaan")],
             title="Corrigeren met wijzigingen bijhouden")
    async with ctx.step("lezen"):
        doc, version, pkg = _load(ctx)
        segs = [s for s in extract_segments(pkg) if len(s.plain.split()) >= 2]
        ctx.detail("lezen", f"{len(segs)} alinea's")
    lang = ctx.settings.doc_language
    system = (f"Je bent een zorgvuldige eindredacteur ({LANGS.get(lang, (lang,))[0]}). {LEVELS.get(level, LEVELS['licht'])} "
              + TAG_RULES + " Geef ALLEEN de regels terug die je wijzigt; laat ongewijzigde regels weg. "
                            "Is alles correct, antwoord dan met: GEEN WIJZIGINGEN")
    changes: dict[int, str] = {}
    async with ctx.step("corrigeren"):
        batches = _batches(segs, min(1200, ctx.budget // 6))
        for bi, batch in enumerate(batches, 1):
            ctx.detail("corrigeren", f"Deel {bi}/{len(batches)} — {len(changes)} correcties tot nu toe")
            ans = await ctx.llm([{"role": "system", "content": system},
                                 {"role": "user", "content": _numbered(batch)}],
                                key="corrigeren", temperature=0.1,
                                max_tokens=int(sum(count_tokens(s.tagged) for _, s in batch) * 1.5) + 200)
            for i, new in _parse(ans).items():
                if 0 <= i < len(segs) and new.strip() and new.strip() != segs[i].tagged.strip():
                    changes[i] = new
    async with ctx.step("opslaan"):
        rid = RevisionIds(pkg)
        author = ctx.settings.author_name and f"DocWizard ({ctx.settings.author_name})" or "DocWizard"
        for i, new in changes.items():
            apply_text(segs[i], new, track=True, author=author, rev_ids=rid)
        out = next_version_path(doc, "gecorrigeerd")
        pkg.save(out)
        problems = validate(pkg)
        if problems:
            ctx.log("Controle: " + "; ".join(problems[:5]), "warn")
        v = save_version(ctx, doc, out, {"actie": "gecorrigeerd", "niveau": level, "wijzigingen": len(changes)})
    ctx.say(f"{len(changes)} alinea's gecorrigeerd in **{doc['name']}** als *bijgehouden wijzigingen* "
            f"(versie {v['number']}). Accepteer of weiger ze in Word via Controleren → Wijzigingen.",
            {"version_id": v["id"]})
    return {"summary": f"{len(changes)} correcties", "version_id": v["id"]}


# ---- review ---------------------------------------------------------------------------------

class Finding(BaseModel):
    nummer: int = Field(description="nummer van de alinea [[n]] waar de opmerking bij hoort")
    opmerking: str
    ernst: str = Field(default="middel", description="hoog | middel | laag")


class SectionReview(BaseModel):
    bevindingen: list[Finding] = Field(default_factory=list)
    score: int = Field(default=7, description="1-10")
    samenvatting: str = ""


RUBRIC = ("Beoordeel als ervaren lead-architect: is de vraag/aanleiding helder, zijn bevindingen onderbouwd, "
          "is het advies concreet en geprioriteerd, zijn risico's en wet- en regelgeving benoemd, is de tekst "
          "consistent (termen, cijfers, namen) en zonder tegenspraak, is de taal helder?")


async def review(ctx: JobContext) -> dict:
    ctx.plan([("lezen", "Document lezen"), ("review", "Secties beoordelen"), ("opslaan", "Opmerkingen plaatsen")],
             title="Review met opmerkingen")
    async with ctx.step("lezen"):
        doc, version, pkg = _load(ctx)
        model = DocModel(pkg)
        segs = extract_segments(pkg, include_headers=False)
        by_el = {id(s.paragraph): s for s in segs}
        units = []
        nodes = [n for n in model.outline() if n.level == 1] or []
        for node in nodes:
            items = []
            for b in model.blocks[node.start:node.end]:
                for p in ([b.element] if b.element.tag == qn("w:p") else b.element.iter(qn("w:p"))):
                    s = by_el.get(id(p))
                    if s:
                        items.append(s)
            if items:
                units.append((node, items))
        ctx.detail("lezen", f"{len(units)} hoofdstukken")
    extra = ctx.params.get("text", "")
    comments = Comments(pkg, author="DocWizard review")
    results = []
    async with ctx.step("review"):
        for ui, (node, items) in enumerate(units, 1):
            ctx.detail("review", f"{ui}/{len(units)}: {node.number} {node.title}")
            # Long chapters are reviewed in parts.
            for part in _batches(items, min(2500, ctx.budget // 3)):
                rv: SectionReview = await ctx.llm([
                    {"role": "system", "content": f"Je reviewt een architectuurdocument. {RUBRIC} "
                                                  f"Geef concrete, bruikbare opmerkingen (max 6), alleen waar echt "
                                                  f"iets te verbeteren is. JSON, Nederlands."},
                    {"role": "user", "content": f"Hoofdstuk {node.number} {node.title}\n"
                                                + (f"Extra aandachtspunten van de gebruiker: {extra}\n" if extra else "")
                                                + "\n" + "\n".join(f"[[{i}]] {s.plain}" for i, s in part)},
                ], key="review", schema=SectionReview, temperature=0.2, max_tokens=1500)
                lookup = dict(part)
                for f in rv.bevindingen:
                    s = lookup.get(f.nummer) or part[0][1]
                    comments.add(s.paragraph, f"[{f.ernst}] {f.opmerking}")
                results.append((node, rv))
    async with ctx.step("opslaan"):
        out = next_version_path(doc, "review")
        pkg.save(out)
        problems = validate(pkg)
        if problems:
            ctx.log("Controle: " + "; ".join(problems[:5]), "warn")
        total = sum(len(r.bevindingen) for _, r in results)
        v = save_version(ctx, doc, out, {"actie": "review", "opmerkingen": total})
    lines = []
    seen = set()
    for node, r in results:
        if node.number in seen:
            continue
        seen.add(node.number)
        lines.append(f"| {node.number} {node.title} | {r.score}/10 | {r.samenvatting[:160]} |")
    ctx.say(f"Review van **{doc['name']}**: {total} opmerkingen geplaatst als Word-opmerkingen (versie "
            f"{v['number']}).\n\n| Hoofdstuk | Score | Samenvatting |\n|---|---|---|\n" + "\n".join(lines),
            {"version_id": v["id"]})
    return {"summary": f"{total} reviewopmerkingen", "version_id": v["id"]}
