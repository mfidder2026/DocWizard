"""Pipelines that create or extend documents: new from template, add chapter, enrich."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..store import repo
from ..wordengine.compose import docx_to_markdown, insert_markdown, new_from_template
from ..wordengine.fields import norm_key
from ..wordengine.package import DocxPackage
from ..wordengine.profile import analyze_template
from ..wordengine.reader import DocModel
from ..wordengine.validate import validate
from .common import (attachments, current_document, file_hash, ingest_attachments, internet_allowed,
                     next_version_path, outline_text, project_dir, research, retrieve, safe_name, save_version,
                     snippets_text, today_nl)
from .jobs import JobContext

STYLE_GUIDE = (
    "Schrijfwijze: zakelijk, helder en adviserend Nederlands (B1–B2), actieve zinnen, concreet en controleerbaar. "
    "Geen marketingtaal, geen herhaling van de opdracht, geen inleidende zinnen als 'In dit hoofdstuk…'. "
    "Verzin geen feiten, namen, bedragen of wetsartikelen: gebruik alleen de aangeleverde informatie en bronnen. "
    "Ontbreekt informatie, schrijf dan kort wat nog bevestigd moet worden ('Nog te bevestigen: …'). "
    "Herhaal nooit de instructies of labels uit de opdracht (zoals 'Kernpunten:')."
)


class Analysis(BaseModel):
    onderwerp: str = ""
    opdrachtgever: str = ""
    doel: str = ""
    kernfeiten: list[str] = Field(default_factory=list)
    betrokkenen: list[str] = Field(default_factory=list)
    systemen: list[str] = Field(default_factory=list)
    knelpunten: list[str] = Field(default_factory=list)
    besluiten: list[str] = Field(default_factory=list)
    open_vragen: list[str] = Field(default_factory=list)
    aannames: list[str] = Field(default_factory=list)


class PlanSection(BaseModel):
    titel: str
    niveau: int = 1
    kernpunten: list[str] = Field(default_factory=list)
    lengte_woorden: int = 250


class Plan(BaseModel):
    titel: str
    secties: list[PlanSection]
    zoekvragen: list[str] = Field(default_factory=list)


class FieldValues(BaseModel):
    waarden: dict[str, str] = Field(default_factory=dict)


class Placement(BaseModel):
    na_sectie: str = Field(description="sectienummer waarna het hoofdstuk komt, of 'einde'")
    niveau: int = 1
    reden: str = ""


def _analysis_md(a: Analysis) -> str:
    parts = [f"Onderwerp: {a.onderwerp}", f"Opdrachtgever: {a.opdrachtgever}", f"Doel: {a.doel}"]
    for label, items in (("Kernfeiten", a.kernfeiten), ("Betrokkenen", a.betrokkenen), ("Systemen", a.systemen),
                         ("Knelpunten", a.knelpunten), ("Besluiten", a.besluiten),
                         ("Open vragen", a.open_vragen), ("Aannames", a.aannames)):
        if items:
            parts.append(f"{label}:\n" + "\n".join(f"- {i}" for i in items))
    return "\n".join(parts)


def _template_for(ctx: JobContext) -> dict:
    tid = ctx.params.get("template_id")
    if not tid and ctx.project_id:
        tid = (repo.get_project(ctx.project_id) or {}).get("template_id")
    t = repo.get_template(tid) if tid else None
    if t and not str(t["path"]).lower().endswith((".docx", ".dotx", ".docm")):
        t = None
    if not t:
        templates = [x for x in repo.list_templates() if str(x["path"]).lower().endswith((".docx", ".dotx", ".docm"))]
        if len(templates) == 1:
            t = templates[0]
    if not t:
        raise RuntimeError("Geen sjabloon gekozen. Upload een sjabloon (Sjablonen) en koppel het aan het project.")
    return t


async def _gather_input(ctx: JobContext, key: str) -> str:
    """User instruction + attachments, condensed to fit the model."""
    text = ctx.params.get("text", "")
    files = attachments(ctx)
    pieces = [f"Opdracht van de gebruiker:\n{text}"]
    for f, content in ingest_attachments(ctx, files):
        pieces.append(f"Bijlage '{f['name']}':\n{content}")
    combined = "\n\n".join(pieces)
    limit_chars = int(ctx.budget * 3.2 * 0.5)
    if len(combined) <= limit_chars:
        return combined
    # Too long for one prompt: condense chunk by chunk (map) — everything stays in the knowledge base.
    ctx.detail(key, "Invoer is lang; samenvatten in delen")
    chunks = [combined[i:i + limit_chars // 2] for i in range(0, len(combined), limit_chars // 2)]
    notes = []
    for i, ch in enumerate(chunks, 1):
        ctx.detail(key, f"Deel {i}/{len(chunks)} samenvatten")
        notes.append(await ctx.llm([
            {"role": "system", "content": "Je maakt volledige, feitelijke notities van werkdocumenten. Nederlands."},
            {"role": "user", "content": f"Maak gedetailleerde notities (feiten, besluiten, namen, systemen, cijfers, "
                                        f"open vragen) van dit deel:\n\n{ch}"},
        ], key=key, role="fast", temperature=0.2, max_tokens=1200))
    return f"Opdracht van de gebruiker:\n{text}\n\nNotities uit de bijlagen:\n" + "\n\n".join(notes)


# ---- new document ----------------------------------------------------------------------

async def new_document(ctx: JobContext) -> dict:
    template = _template_for(ctx)
    quality = ctx.params.get("quality", "standaard")
    ctx.plan([("input", "Invoer lezen"), ("analyse", "Invoer analyseren"), ("sjabloon", "Sjabloon analyseren"),
              ("outline", "Opbouw voorstellen"), ("onderzoek", "Onderzoek (kennisbank / internet)"),
              ("schrijven", "Secties schrijven"), ("velden", "Voorblad en velden invullen"),
              ("opbouwen", "Document opbouwen in huisstijl"), ("controle", "Controleren")],
             title=f"Nieuw document op basis van '{template['name']}'")

    async with ctx.step("input"):
        brief = ctx.restored("brief") or await _gather_input(ctx, "input")
        ctx.checkpoint("brief", brief)

    async with ctx.step("analyse"):
        saved = ctx.restored("analysis")
        analysis: Analysis = Analysis.model_validate(saved) if saved else await ctx.llm([
            {"role": "system", "content": "Je bent een ervaren enterprise-architect. Analyseer de invoer zorgvuldig "
                                          "en volledig. Antwoord in het Nederlands, als JSON."},
            {"role": "user", "content": f"{brief}\n\nAnalyseer bovenstaande invoer voor het opstellen van een "
                                        f"'{template['name']}'. Neem alleen op wat in de invoer staat."},
        ], key="analyse", schema=Analysis, temperature=0.2, max_tokens=2000)
        ctx.checkpoint("analysis", analysis.model_dump())

    async with ctx.step("sjabloon"):
        profile = template.get("profile") or analyze_template(template["path"])
        tsections = [s for s in profile["outline"] if s["level"] <= 3]
        example_topic = _example_topic(profile)
        skeleton = "\n".join(
            f"{'  ' * (s['level'] - 1)}- {s['title']} (niveau {s['level']})"
            + (f" — instructie: {'; '.join(s['guidance'])}" if s["guidance"] else "")
            for s in tsections)

    async with ctx.step("outline"):
        saved_plan = ctx.restored("plan")
        plan: Plan = Plan.model_validate(saved_plan) if saved_plan else await ctx.llm([
            {"role": "system", "content": "Je plant de opbouw van een architectuurdocument volgens een vast "
                                          "huisstijlsjabloon. Antwoord als JSON in het Nederlands."},
            {"role": "user", "content": f"Sjabloonstructuur (volg deze volgorde en titels; laat secties alleen weg "
                                        f"als ze echt niet van toepassing zijn, voeg alleen toe als het nodig is):\n"
                                        f"{skeleton}\n\nAnalyse van de invoer:\n{_analysis_md(analysis)}\n\n"
                                        f"Geef: een documenttitel, per sectie (titel, niveau, 2–6 kernpunten uit de "
                                        f"analyse, lengte in woorden 80–600) en maximaal 6 algemene zoekvragen "
                                        f"voor achtergrondinformatie (zoals wet- en regelgeving, standaarden, "
                                        f"referentiearchitecturen), zonder namen of vertrouwelijke details."},
        ], key="outline", schema=Plan, temperature=0.3, max_tokens=3000)
        if not plan.secties:
            plan.secties = [PlanSection(titel=s["title"], niveau=s["level"]) for s in tsections]
        if not ctx.params.get("auto_approve") and not saved_plan:
            ans = await ctx.ask(
                "Voorgestelde opbouw. Pas desgewenst titels, niveaus en kernpunten aan en keur goed.",
                options=[{"value": "ok", "label": "Goedkeuren en schrijven"}, {"value": "cancel", "label": "Stoppen"}],
                editable={"kind": "json", "value": json.loads(plan.model_dump_json())})
            if ans.get("value") == "cancel":
                from .jobs import JobCancelled
                raise JobCancelled()
            if ans.get("edited"):
                try:
                    edited = ans["edited"] if isinstance(ans["edited"], dict) else json.loads(ans["edited"])
                    plan = Plan.model_validate(edited)
                except Exception as e:  # noqa: BLE001
                    ctx.log(f"Aangepaste opbouw niet leesbaar, origineel gebruikt ({e})", "warn")

    ctx.checkpoint("plan", plan.model_dump())
    web_sources: list[dict] = ctx.restored("web_sources") or []
    async with ctx.step("onderzoek"):
        if web_sources:
            ctx.detail("onderzoek", f"{len(web_sources)} bronnen uit eerdere poging")
        elif internet_allowed(ctx) and plan.zoekvragen:
            web_sources = await research(ctx, plan.zoekvragen, key="onderzoek", topic=analysis.onderwerp)
            ctx.detail("onderzoek", f"{len(web_sources)} bronnen gebruikt")
            ctx.checkpoint("web_sources", web_sources)
        else:
            ctx.detail("onderzoek", "Alleen lokale kennis (internet staat uit voor deze chat)")

    # Per-section writing; summaries go last so they can summarize the rest.
    order = sorted(range(len(plan.secties)),
                   key=lambda i: 1 if re.search(r"samenvatting|summary", plan.secties[i].titel, re.I) else 0)
    ctx.add_steps([(f"s{i}", f"Schrijven: {plan.secties[i].titel}") for i in order], after="schrijven")
    ctx.skip("schrijven", f"{len(plan.secties)} secties")
    bodies: dict[int, str] = {int(k): v for k, v in (ctx.restored("bodies") or {}).items()}
    digest: list[str] = list(ctx.restored("digest") or [])
    outline_titles = "\n".join(f"{'  ' * (s.niveau - 1)}{s.titel}" for s in plan.secties)
    tmap = {norm_key(s["title"]): s for s in profile["outline"]}
    for i in order:
        sec = plan.secties[i]
        key = f"s{i}"
        if i in bodies:
            ctx.skip(key, "uit eerdere poging")
            continue
        async with ctx.step(key):
            ts = tmap.get(norm_key(sec.titel), {})
            is_summary = bool(re.search(r"samenvatting|summary", sec.titel, re.I))
            hits = retrieve(ctx, f"{sec.titel} {' '.join(sec.kernpunten)}", k=6)
            for h in hits[:3]:
                ctx.source(h["title"], "knowledge", url=h.get("url"), snippet=h["text"][:160])
            has_children = i + 1 < len(plan.secties) and plan.secties[i + 1].niveau > sec.niveau
            guidance = "; ".join(ts.get("guidance") or [])
            sample = ts.get("sample_markdown") or ""
            table_hint = ""
            for t in ts.get("tables") or []:
                if t.get("columns"):
                    table_hint += f"\nGebruik waar passend een Markdown-tabel met kolommen: {' | '.join(t['columns'])}."
            context = (f"Samenvattingen van de al geschreven secties:\n" + "\n".join(digest)) if digest else ""
            if is_summary:
                context = "Inhoud van het document (samengevat):\n" + "\n".join(digest)
            cite = ("Verwijs naar webbronnen met [nummer] als je ze gebruikt." if web_sources
                    else "Gebruik geen bronverwijzingen zoals [1].")
            user = (f"Document: {plan.titel}\nOpbouw:\n{outline_titles}\n\n"
                    f"Analyse van de opdracht:\n{_analysis_md(analysis)}\n\n"
                    + (f"{context}\n\n" if context else "")
                    + (f"Bronfragmenten:\n{snippets_text(hits)}\n\n" if hits else "")
                    + f"Schrijf nu de inhoud van sectie '{sec.titel}' (niveau {sec.niveau}).\n"
                    + (f"Instructie uit het sjabloon: {guidance}\n" if guidance else "")
                    + (f"Voorbeeld uit een eerder document in deze huisstijl (dat ging over: {example_topic}). Gebruik "
                       f"het alleen voor opbouw, stijl en detailniveau. Neem vaste onderdelen letterlijk over (zoals "
                       f"tabellen met schalen of definities en standaardformuleringen), maar NIETS dat specifiek is "
                       f"voor dat eerdere onderwerp (systemen, producten, namen, feiten):\n<<<\n{sample}\n>>>\n"
                       if sample else "")
                    + (f"Behandel in elk geval: {'; '.join(sec.kernpunten)}.\n" if sec.kernpunten else "")
                    + (f"Deze sectie heeft subsecties; schrijf alleen een korte inleiding (max 80 woorden).\n"
                       if has_children else f"Omvang: ongeveer {sec.lengte_woorden} woorden.\n")
                    + table_hint + "\n" + cite
                    + "\nGeef alleen de inhoud in Markdown (alinea's, opsommingen, tabellen), zonder de sectiekop "
                      "zelf en zonder nieuwe koppen.")
            body = await ctx.llm([{"role": "system", "content": "Je bent een senior enterprise-architect die "
                                                                "documenten schrijft in de huisstijl van de "
                                                                "organisatie. " + STYLE_GUIDE},
                                  {"role": "user", "content": user}],
                                 key=key, temperature=0.5, max_tokens=min(4096, int(sec.lengte_woorden * 2.6) + 400))
            body = _clean_body(body)
            if not body.strip():
                raise RuntimeError(f"Het model gaf geen tekst voor '{sec.titel}'. Probeer 'Hervatten'.")
            leaked = leaked_terms(body, sample, brief + "\n" + _analysis_md(analysis))
            if leaked:
                ctx.detail(key, f"Termen uit het voorbeelddocument verwijderen: {', '.join(leaked)}")
                body = _clean_body(await ctx.llm([
                    {"role": "system", "content": "Je redigeert een sectie van een architectuurdocument. " + STYLE_GUIDE},
                    {"role": "user", "content": f"Deze tekst bevat begrippen uit een ánder document die niet bij dit "
                                                f"onderwerp horen: {', '.join(leaked)}.\nVerwijder die passages of "
                                                f"herschrijf ze passend bij de analyse. Laat de rest ongewijzigd.\n\n"
                                                f"Analyse:\n{_analysis_md(analysis)}\n\nTekst:\n{body}\n\n"
                                                f"Geef alleen de verbeterde tekst in Markdown."}],
                    key=key, temperature=0.2)) or body
            if quality == "grondig" and not has_children:
                body = await _review_and_revise(ctx, key, sec.titel, body, analysis)
            bodies[i] = body
            words = body.split()
            digest.append(f"- {sec.titel}: {' '.join(words[:70])}{'…' if len(words) > 70 else ''}")
            ctx.checkpoint("bodies", {str(k): v for k, v in bodies.items()})
            ctx.checkpoint("digest", digest)

    async with ctx.step("velden"):
        try:
            values = await _field_values(ctx, profile, plan, analysis)
        except Exception as e:  # noqa: BLE001 — cover fields are nice-to-have, never fatal
            ctx.log(f"Velden konden niet automatisch worden ingevuld ({e}); standaardwaarden gebruikt.", "warn")
            values = _default_field_values(ctx, profile, plan, analysis)
        if values:
            ctx.detail("velden", ", ".join(f"{k}: {v}" for k, v in list(values.items())[:6]))

    async with ctx.step("opbouwen"):
        md = "\n\n".join(f"{'#' * max(1, min(9, s.niveau))} {s.titel}\n\n{bodies.get(i, '')}"
                         for i, s in enumerate(plan.secties))
        if web_sources:
            md += "\n\n# Bronnen\n\n" + "\n".join(
                f"{n}. [{w['title']}]({w['url']}) (geraadpleegd {today_nl()})" for n, w in enumerate(web_sources, 1))
        name = safe_name(ctx.params.get("title") or plan.titel)
        doc = repo.create_document(ctx.project_id, name)
        out = next_version_path(doc, "concept")
        rep = new_from_template(template["path"], md, out, fields=values, title=plan.titel,
                                author=ctx.settings.author_name or None, images=_diagram_resolver(ctx))
        for w in rep.warnings:
            ctx.log(w, "warn")
        (out.with_suffix(".md")).write_text(md, encoding="utf-8")

    async with ctx.step("controle"):
        problems = validate(DocxPackage.open(out))
        if problems:
            ctx.log("Controle: " + "; ".join(problems[:5]), "warn")
        version = save_version(ctx, doc, out, {"actie": "nieuw document", "sjabloon": template["name"],
                                               "secties": len(plan.secties), "bronnen": len(web_sources)})

    open_q = "\n".join(f"- {q}" for q in analysis.open_vragen[:8])
    assum = "\n".join(f"- {a}" for a in analysis.aannames[:8])
    msg = (f"**{plan.titel}** is gemaakt in de huisstijl van *{template['name']}* "
           f"({len(plan.secties)} secties"
           + (f", {rep.toc_entries} items in de inhoudsopgave" if rep.toc_entries else "")
           + f", {sum(len(b.split()) for b in bodies.values())} woorden"
           f"{f', {len(web_sources)} webbronnen' if web_sources else ''}).\n\n"
           + (f"**Open vragen**\n{open_q}\n\n" if open_q else "")
           + (f"**Aannames**\n{assum}\n\n" if assum else "")
           + "Open het document in Word; de paginanummers in de inhoudsopgave worden bij openen bijgewerkt.")
    ctx.say(msg, {"version_id": version["id"], "document_id": doc["id"]})
    return {"summary": f"{plan.titel} gemaakt", "document_id": doc["id"], "version_id": version["id"]}


def _example_topic(profile: dict) -> str:
    """What the template's example content was about (e.g. the 'Betreft' cover line)."""
    for f in profile.get("fields") or []:
        if re.search(r"betreft|onderwerp|titel|title|subject", f["label"], re.I) and f.get("current"):
            return f["current"][:200]
    first = next((s for s in profile.get("outline") or [] if s.get("sample_markdown")), None)
    return (first or {}).get("sample_markdown", "")[:200].replace("\n", " ") or "een ander onderwerp"


_TERM_RX = re.compile(r"\b(?:[A-Z][\w\-]*[A-Z0-9][\w\-]*|[A-Z][a-z]{2,}[\w\-]*|\w*\d\w*)\b")
_COMMON = {"Het", "De", "Een", "Dit", "Deze", "Wij", "We", "Er", "In", "Op", "Voor", "Bij", "Met", "Door", "Als",
           "Nog", "Geen", "Advies", "Architectuur", "Wijziging", "Kosten", "Doorlooptijd", "Maat", "Indicatoren",
           "Opvolging", "Optie", "Zie", "Ook", "Dat", "Waar", "Wat", "Hoe", "Daarnaast", "Daarom", "Tevens"}


def leaked_terms(body: str, sample: str, source: str) -> list[str]:
    """Distinctive terms that appear in the template example but not in the user's input (copied content)."""
    if not sample:
        return []
    src = source.lower()
    sample_terms = set()
    for m in _TERM_RX.finditer(sample):
        sample_terms.add(m.group(0))
        sample_terms.update(p for p in m.group(0).split("-") if len(p) >= 2)
    hits = []
    for m in _TERM_RX.finditer(body):
        word = m.group(0)
        for t in [word] + [p for p in word.split("-") if p != word]:
            if t in _COMMON or len(t) < 2 or t.isdigit() or t not in sample_terms or not t[0].isupper():
                continue
            if t.lower() in src or t.lower().rstrip("s") in src:
                continue
            if word not in hits:
                hits.append(word)
    return hits[:12]


def _clean_body(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```$", r"\1", text, flags=re.S)
    # Drop a repeated section heading at the top and demote stray headings to bold lines.
    text = re.sub(r"^\s*#{1,6}\s+.*\n+", "", text, count=1) if re.match(r"^\s*#", text) else text
    text = re.sub(r"^#{1,6}\s+(.*)$", r"**\1**", text, flags=re.M)
    text = re.sub(r"^\s*\**(Kernpunten|Behandel in elk geval)\**\s*:.*$", "", text, flags=re.M | re.I)
    return text.strip()


class Review(BaseModel):
    goed_genoeg: bool = True
    problemen: list[str] = Field(default_factory=list)


async def _review_and_revise(ctx: JobContext, key: str, title: str, body: str, analysis: Analysis) -> str:
    ctx.detail(key, "Beoordelen")
    rv: Review = await ctx.llm([
        {"role": "system", "content": "Je bent een kritische reviewer van architectuurdocumenten. JSON, Nederlands."},
        {"role": "user", "content": f"Analyse:\n{_analysis_md(analysis)}\n\nSectie '{title}':\n{body}\n\n"
                                    f"Beoordeel op: juistheid t.o.v. de analyse (geen verzonnen feiten), volledigheid, "
                                    f"concreetheid, helderheid, taal. Noem alleen echte problemen."},
    ], key=key, role="fast", schema=Review, temperature=0.1, max_tokens=800)
    if rv.goed_genoeg or not rv.problemen:
        return body
    ctx.detail(key, f"Herschrijven ({len(rv.problemen)} verbeterpunten)")
    revised = await ctx.llm([
        {"role": "system", "content": "Je verbetert een sectie van een architectuurdocument. " + STYLE_GUIDE},
        {"role": "user", "content": f"Sectie '{title}':\n{body}\n\nVerbeterpunten:\n" +
                                    "\n".join(f"- {p}" for p in rv.problemen) +
                                    "\n\nGeef de verbeterde sectie-inhoud in Markdown, zonder kop."},
    ], key=key, temperature=0.4)
    return _clean_body(revised)


def _default_field_values(ctx: JobContext, profile: dict, plan: Plan, analysis: Analysis,
                          todo: list | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    for f in profile.get("fields") or []:
        k, label = f["key"], f["label"].lower()
        if re.search(r"datum|date", label):
            values[k] = today_nl()
        elif re.search(r"versie|version", label):
            values[k] = ctx.params.get("version", "0.1")
        elif re.search(r"status", label):
            values[k] = "Concept"
        elif re.search(r"auteur|author|verkenning door|opgesteld door", label) and ctx.settings.author_name:
            values[k] = ctx.settings.author_name
        elif re.search(r"titel|title|betreft|onderwerp", label):
            values[k] = plan.titel
        elif re.search(r"klant|opdrachtgever|organisatie", label) and analysis.opdrachtgever:
            values[k] = analysis.opdrachtgever
        elif todo is not None:
            todo.append(f)
    return values


async def _field_values(ctx: JobContext, profile: dict, plan: Plan, analysis: Analysis) -> dict[str, str]:
    fields = profile.get("fields") or []
    if not fields:
        return {}
    todo: list = []
    values = _default_field_values(ctx, profile, plan, analysis, todo)
    if todo:
        fv: FieldValues = await ctx.llm([
            {"role": "system", "content": "Je vult velden op het voorblad van een document in. JSON."},
            {"role": "user", "content": f"Document: {plan.titel}\n{_analysis_md(analysis)}\n\nVelden (sleutel: label "
                                        f"(huidige waarde)):\n" + "\n".join(
                f"- {f['key']}: {f['label']} ({f.get('current', '')})" for f in todo) +
                                        "\n\nGeef per sleutel een waarde. Laat leeg (\"\") als het onbekend is; "
                                        "verzin niets. Bestaande waarden van een ander document niet overnemen."},
        ], key="velden", role="fast", schema=FieldValues, temperature=0.1, max_tokens=600)
        values.update({k: v for k, v in fv.waarden.items() if v})
    return values


def _diagram_resolver(ctx: JobContext):
    """Markdown images like ![titel](diagram:<id>) resolve to generated diagram PNGs of this project."""
    def resolve(src: str):
        m = re.match(r"diagram:([\w-]+)", src)
        if m:
            p = project_dir(ctx.project_id) / "diagrams" / f"{m.group(1)}.png"
            if p.exists():
                return p.read_bytes(), "png"
        p = Path(src)
        if p.exists() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif"):
            return p.read_bytes(), p.suffix.lstrip(".")
        return None
    return resolve


# ---- add chapter ------------------------------------------------------------------------

async def add_chapter(ctx: JobContext) -> dict:
    ctx.plan([("doel", "Doeldocument bepalen"), ("inhoud", "Hoofdstuk lezen of schrijven"),
              ("positie", "Positie bepalen"), ("invoegen", "Invoegen in huisstijl"), ("controle", "Controleren")],
             title="Hoofdstuk toevoegen")
    async with ctx.step("doel"):
        from .common import import_docx
        files = attachments(ctx)
        docx_files = [f for f in files if f["name"].lower().endswith(".docx")]
        others = [f for f in files if f not in docx_files]
        explicit = ctx.params.get("version_id") or ctx.params.get("document_id")
        existing = current_document(ctx, use_attachments=False)
        if explicit and existing:
            target, chapter_files = existing, files
        elif len(docx_files) >= 2:
            # First .docx = the document, the others = chapters to add.
            target, chapter_files = import_docx(ctx, docx_files[0]["path"], docx_files[0]["name"]), docx_files[1:] + others
        elif len(docx_files) == 1 and existing:
            target, chapter_files = existing, files
        elif len(docx_files) == 1:
            target, chapter_files = import_docx(ctx, docx_files[0]["path"], docx_files[0]["name"]), others
        else:
            target, chapter_files = existing, others
        if not target:
            raise RuntimeError("Geen doeldocument gevonden. Voeg het document (.docx) toe of maak eerst een document.")
        doc, version = target
        model = DocModel.open(version["path"])
        ctx.detail("doel", f"{doc['name']} ({version['number']})")

    images = None
    async with ctx.step("inhoud"):
        chapter_md = ""
        for f in chapter_files:
            if f["name"].lower().endswith(".docx") and f["path"] != version["path"]:
                md, images = docx_to_markdown(f["path"])
                chapter_md += md + "\n\n"
            else:
                from .common import extract_text
                chapter_md += extract_text(f["path"]) + "\n\n"
        instruction = ctx.params.get("text", "")
        if not chapter_md.strip():
            hits = retrieve(ctx, instruction, k=8)
            chapter_md = await ctx.llm([
                {"role": "system", "content": "Je schrijft een nieuw hoofdstuk voor een bestaand architectuurdocument. "
                                              + STYLE_GUIDE},
                {"role": "user", "content": f"Opbouw van het bestaande document:\n{outline_text(model)}\n\n"
                                            + (f"Bronfragmenten:\n{snippets_text(hits)}\n\n" if hits else "")
                                            + f"Opdracht: {instruction}\n\nSchrijf het hoofdstuk in Markdown, "
                                              f"beginnend met één kop van niveau 1 (#) en eventueel subkoppen (##)."},
            ], key="inhoud", temperature=0.5)
        elif ctx.params.get("harmonize"):
            chapter_md = await ctx.llm([
                {"role": "system", "content": "Je harmoniseert een hoofdstuk met een bestaand document zonder de "
                                              "inhoud te veranderen. " + STYLE_GUIDE},
                {"role": "user", "content": f"Opbouw van het document:\n{outline_text(model)}\n\nHoofdstuk:\n"
                                            f"{chapter_md}\n\nPas terminologie en aanspreekvorm aan het document aan; "
                                            f"behoud structuur, koppen en tabellen. Geef Markdown."},
            ], key="inhoud", temperature=0.3)
        if not re.search(r"^#\s", chapter_md, re.M):
            title = ctx.params.get("chapter_title") or "Nieuw hoofdstuk"
            chapter_md = f"# {title}\n\n{chapter_md}"

    async with ctx.step("positie"):
        after = ctx.params.get("after_section")
        level = ctx.params.get("level")
        if not after:
            first_heading = re.search(r"^#+\s+(.*)$", chapter_md, re.M).group(1)
            pl: Placement = await ctx.llm([
                {"role": "system", "content": "Je bepaalt waar een nieuw hoofdstuk logisch past. JSON."},
                {"role": "user", "content": f"Opbouw van het document:\n{outline_text(model, 2)}\n\n"
                                            f"Nieuw hoofdstuk: '{first_heading}'\nBegin:\n{chapter_md[:1500]}\n\n"
                                            f"Na welk sectienummer hoort dit, en op welk niveau (1 = hoofdstuk)? "
                                            f"Gebruik 'einde' voor achteraan (vóór bijlagen als die er zijn)."},
            ], key="positie", role="fast", schema=Placement, temperature=0.1, max_tokens=300)
            after, level = pl.na_sectie, level or pl.niveau
            choice = "einde" if after.lower() == "einde" else after
            ans = await ctx.ask(f"Voorstel: invoegen na **{choice}** als niveau {level}. {pl.reden}",
                                options=[{"value": "ok", "label": "Akkoord"}, {"value": "end", "label": "Achteraan"}],
                                editable={"kind": "text", "value": choice})
            if ans.get("value") == "end":
                after = "einde"
            elif isinstance(ans.get("edited"), str) and ans["edited"].strip():
                after = ans["edited"].strip()
        if str(after).lower() == "einde":
            appendix = next((n for n in model.outline() if n.level == 1 and
                             re.search(r"bijlage|appendi|annex", n.title, re.I)), None)
            after = None
            before = appendix.number if appendix else None
        else:
            before = None
        ctx.detail("positie", f"na {after}" if after else (f"vóór {before}" if before else "achteraan"))

    async with ctx.step("invoegen"):
        out = next_version_path(doc, "hoofdstuk toegevoegd")
        rep = insert_markdown(version["path"], chapter_md, out, after_section=after, before_section=before,
                              as_level=int(level) if level else None, images=images,
                              author=ctx.settings.author_name or None)

    async with ctx.step("controle"):
        problems = validate(DocxPackage.open(out))
        if problems:
            ctx.log("Controle: " + "; ".join(problems[:5]), "warn")
        v = save_version(ctx, doc, out, {"actie": "hoofdstuk toegevoegd", "positie": after or before or "einde"})
    new_model = DocModel.open(out)
    ctx.say(f"Hoofdstuk toegevoegd aan **{doc['name']}** (nieuwe versie {v['number']}). "
            f"De inhoudsopgave heeft nu {rep.toc_entries} items.\n\nNieuwe opbouw:\n```\n{outline_text(new_model, 1)}\n```",
            {"version_id": v["id"], "document_id": doc["id"]})
    return {"summary": "Hoofdstuk toegevoegd", "version_id": v["id"]}


# ---- enrich ---------------------------------------------------------------------------------

class EnrichTarget(BaseModel):
    sectie: str
    zoekvragen: list[str] = Field(default_factory=list)


async def enrich(ctx: JobContext) -> dict:
    ctx.plan([("doel", "Document en sectie bepalen"), ("onderzoek", "Informatie verzamelen"),
              ("schrijven", "Aanvulling schrijven"), ("invoegen", "Invoegen"), ("controle", "Controleren")],
             title="Document verrijken")
    instruction = ctx.params.get("text", "")
    async with ctx.step("doel"):
        target = current_document(ctx)
        if not target:
            raise RuntimeError("Geen document gevonden om te verrijken.")
        doc, version = target
        model = DocModel.open(version["path"])
        section = ctx.params.get("section")
        queries: list[str] = []
        if not section:
            t: EnrichTarget = await ctx.llm([
                {"role": "system", "content": "Je kiest de sectie die verrijkt moet worden. JSON."},
                {"role": "user", "content": f"Opbouw:\n{outline_text(model)}\n\nOpdracht: {instruction}\n\n"
                                            f"Geef het sectienummer en max 4 algemene zoekvragen (zonder namen)."},
            ], key="doel", role="fast", schema=EnrichTarget, temperature=0.1, max_tokens=300)
            section, queries = t.sectie, t.zoekvragen
        node = next((n for n in model.outline() if n.number == section or n.title.lower() == section.lower()), None)
        if not node:
            raise RuntimeError(f"Sectie '{section}' niet gevonden.")
        current = model.to_markdown(node.start, node.end)
        ctx.detail("doel", f"{node.number} {node.title}")

    sources: list[dict] = []
    async with ctx.step("onderzoek"):
        if internet_allowed(ctx):
            sources = await research(ctx, queries or [f"{node.title} {instruction}"], key="onderzoek",
                                     topic=node.title)
        hits = retrieve(ctx, f"{node.title} {instruction}", k=8)

    async with ctx.step("schrijven"):
        addition = await ctx.llm([
            {"role": "system", "content": "Je verrijkt een sectie van een architectuurdocument met nieuwe, "
                                          "onderbouwde informatie. " + STYLE_GUIDE},
            {"role": "user", "content": f"Huidige sectie:\n{current}\n\n"
                                        + (f"Bronfragmenten:\n{snippets_text(hits)}\n\n" if hits else "")
                                        + f"Opdracht: {instruction}\n\nSchrijf alleen de AANVULLING (nieuwe alinea's, "
                                          f"opsommingen of een tabel) die na de bestaande tekst komt. Herhaal niets. "
                                          f"Geen koppen. Markdown."},
        ], key="schrijven", temperature=0.4)
        addition = _clean_body(addition)
        if sources:
            addition += "\n\nBronnen: " + "; ".join(f"[{s['title']}]({s['url']})" for s in sources)

    async with ctx.step("invoegen"):
        out = next_version_path(doc, f"verrijkt {node.number}")
        insert_markdown(version["path"], addition, out, after_section=node.number,
                        author=ctx.settings.author_name or None)

    async with ctx.step("controle"):
        problems = validate(DocxPackage.open(out))
        if problems:
            ctx.log("Controle: " + "; ".join(problems[:5]), "warn")
        v = save_version(ctx, doc, out, {"actie": "verrijkt", "sectie": f"{node.number} {node.title}"})
    ctx.say(f"Sectie **{node.number} {node.title}** is aangevuld (versie {v['number']}).\n\n{addition[:1500]}",
            {"version_id": v["id"], "document_id": doc["id"]})
    return {"summary": "Verrijkt", "version_id": v["id"]}


__all__ = ["new_document", "add_chapter", "enrich", "file_hash"]
