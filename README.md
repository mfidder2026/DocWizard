# DocWizard

Lokale documentenwizard voor Word-documenten in je eigen huisstijl, aangedreven door **lokale** LLM's
(LM Studio, Ollama, MTPLX of elke andere OpenAI-compatibele server). Er gaat geen documentinhoud naar de cloud.

Ontwerp: [ONTWERP.md](ONTWERP.md) · Interne contracten: [docs/CONTRACTS.md](docs/CONTRACTS.md)

## Wat kan het

| Actie | Wat er gebeurt |
|---|---|
| **Nieuw document** | Sjabloon (bijv. AQS) + meetingsamenvatting → analyse → opbouw (ter goedkeuring) → per sectie schrijven → voorblad/velden invullen → `.docx` in exact de huisstijl van het sjabloon, met bijgewerkte inhoudsopgave |
| **Hoofdstuk toevoegen** | Hoofdstuk (docx/tekst of laten schrijven) invoegen in een bestaand document, in de huisstijl van dat document; nummering en inhoudsopgave worden bijgewerkt |
| **Vertalen** | Tekst per alinea vervangen, opmaak, tabellen, velden en afbeeldingen blijven exact staan; termenlijst per project |
| **Corrigeren** | Correcties als *bijgehouden wijzigingen* (accepteren/weigeren in Word) |
| **Reviewen** | Bevindingen als Word-opmerkingen + scoretabel per hoofdstuk |
| **Verrijken** | Sectie aanvullen met informatie uit de kennisbank en (optioneel) internet, met bronvermelding |
| **Plaat maken** | ArchiMate / C4 / generieke architectuurplaat als bewerkbaar `.drawio` + PNG/SVG, optioneel in het document |

Per chat kies je **🔒 Privé / 📁 Project / 🗂 Alle projecten** en los daarvan **🌐 Internet**. Internetverkeer loopt via één
gateway die dit afdwingt; zoekvragen worden eerst ontdaan van gevoelige termen en (standaard) aan je voorgelegd.

## Starten (ontwikkelversie)

Vereist: [uv](https://docs.astral.sh/uv/) en een draaiende lokale modelserver.

```bash
uv sync --extra desktop
```

```bash
uv run docwizard
```

Opties: `--browser` (in je standaardbrowser i.p.v. eigen venster), `--server-only --port 8765`.

Instellingen → **AI-verbindingen**: voeg je eigen server toe met URL (bijv. `http://localhost:8000/v1`), API-key en model,
en wijs modellen toe aan de rollen *schrijver*, *snel*, *visie* en *embedding*. Onder **Taal** kies je UI- en documenttaal.

## App bouwen

macOS (Apple Silicon) → `dist/DocWizard.app`:

```bash
./scripts/build_mac.sh
```

Windows 10/11 (PowerShell) → `dist\DocWizard\DocWizard.exe`:

```powershell
./scripts/build_windows.ps1
```

Of laat GitHub Actions beide bouwen (`.github/workflows/build.yml`). Dezelfde code draait op beide platformen:
pywebview gebruikt WKWebView op macOS en WebView2 op Windows.

## Tests

```bash
uv run pytest -q
```

Eigen (vertrouwelijke) voorbeelddocumenten in `tests/private/` worden automatisch meegetest en nooit gecommit.

## Opbouw

```
src/docwizard/
  wordengine/   Word-engine: verliesvrij lezen/schrijven, huisstijl leren, Markdown→OOXML, TOC, velden,
                in-place bewerken, track changes, opmerkingen, validatie, voorvertoning
  drawio/       plaatgenerator: spec → layout → .drawio / SVG / PNG
  llm/          OpenAI-compatibele client (streaming, denkstappen, JSON-schema met reparatie)
  store/        SQLite (projecten, chats, berichten, documentversies, jobs, kennis)
  memory/       contextopbouw binnen budget + compactie van lange chats
  knowledge/    kennisbank (FTS5 + optionele embeddings), privacy-scopes
  research/     internet: egress-gateway, zoeken, ophalen, wetten.overheid.nl
  workflows/    job-engine (sequentieel) + pipelines
  server/       HTTP-API + SSE
  web/          UI (vanilla JS, offline)
  app.py        desktop-launcher
```
