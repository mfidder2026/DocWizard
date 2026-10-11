# DocWizard — interne contracten (MVP, macOS eerst, Windows-klaar)

Vaste keuzes:
- Python 3.12 engine (FastAPI + uvicorn) op 127.0.0.1, UI = statische web-bestanden die de engine zelf serveert, native venster via **pywebview** (macOS WKWebView / Windows WebView2). Geen Node, geen Docker.
- **Alles sequentieel**: één job-worker; LLM-aanroepen nooit parallel.
- Data-map: macOS `~/Library/Application Support/DocWizard`, Windows `%APPDATA%\DocWizard`, Linux `~/.local/share/DocWizard`. Override via env `DOCWIZARD_HOME` (tests).
- Alle paden via `pathlib`, geen OS-specifieke shell-aanroepen behalve achter een helper in `docwizard/platform.py`.
- UI-taal Nederlands (Engels als tweede, via eenvoudige i18n-dict).

## Python-modules en eigenaarschap

| Module | Eigenaar | Inhoud |
|---|---|---|
| `docwizard/wordengine/` | lead | Word-engine (lezen, huisstijl, bouwen, TOC, velden, in-place, track changes, comments) |
| `docwizard/drawio/` | agent-drawio | spec → layout → .drawio/SVG/PNG |
| `docwizard/config.py`, `docwizard/platform.py` | agent-backend | Settings + paden |
| `docwizard/llm/` | agent-backend | OpenAI-compatibele client |
| `docwizard/store/` | agent-backend | SQLite-opslag |
| `docwizard/knowledge/` | agent-backend | kennisbank (FTS5 + optionele embeddings) |
| `docwizard/research/` | agent-backend | webzoeken + ophalen + egress-gateway |
| `docwizard/memory/` | agent-backend | contextopbouw + compactie |
| `docwizard/web/` (static) | agent-ui | HTML/CSS/JS UI |
| `docwizard/server/`, `docwizard/workflows/`, `docwizard/app.py` | lead | API, jobs, pipelines, desktop-launcher |

## config.py

```python
class Connection(BaseModel):
    id: str                      # slug, uniek
    name: str                    # "LM Studio"
    kind: Literal["lmstudio", "ollama", "mtplx", "openai"] = "openai"
    base_url: str                # "http://localhost:1234/v1"  (altijd incl. /v1)
    api_key: str = ""            # optioneel; wordt als Bearer meegestuurd als niet leeg
    model: str = ""              # model-id zoals de server het noemt
    context_limit: int | None = None   # handmatige override; None = auto-detect
    enabled: bool = True

class WebSearchSettings(BaseModel):
    provider: Literal["duckduckgo", "searxng", "brave"] = "duckduckgo"
    url: str = ""                # voor searxng
    api_key: str = ""            # voor brave
    confirm_queries: bool = True # zoekvragen eerst laten goedkeuren
    allow_domains: list[str] = []  # leeg = alles

class Settings(BaseModel):
    ui_language: Literal["nl", "en"] = "nl"
    doc_language: str = "nl"     # standaard documenttaal
    author_name: str = ""
    connections: list[Connection]          # default: LM Studio :1234, Ollama :11434, MTPLX :8000
    roles: dict[str, str]                  # {"writer": conn_id, "fast": conn_id, "embedding": conn_id|"" , "vision": conn_id|""}
    quality_context_tokens: int = 32000    # bovengrens effectief contextbudget
    max_output_tokens: int = 4096
    web: WebSearchSettings
    data_dir_override: str = ""

def load_settings() -> Settings; def save_settings(s: Settings) -> None
def app_paths() -> AppPaths  # .home, .db, .templates, .projects, .logs, .cache
```

## llm/

```python
@dataclass class ModelInfo: id: str; context_length: int | None; vision: bool | None
@dataclass class ChatResult: text: str; reasoning: str; prompt_tokens: int; completion_tokens: int; seconds: float; model: str

class LLMClient:
    def __init__(self, conn: Connection): ...
    async def list_models(self) -> list[ModelInfo]
    async def detect_context(self) -> int | None
    async def chat(self, messages: list[dict], *, temperature=0.4, max_tokens: int | None = None,
                   json_schema: dict | None = None, on_token: Callable[[str], Awaitable[None] | None] | None = None,
                   on_reasoning: Callable[[str], ...] | None = None, cancel: asyncio.Event | None = None) -> ChatResult
    async def chat_json(self, messages, schema: type[BaseModel], **kw) -> BaseModel   # validatie + reparatie + retry
    async def embed(self, texts: list[str]) -> list[list[float]]
    async def test(self) -> dict  # {"ok": bool, "models": [...], "latency_ms": int, "context": int|None, "error": str|None}

def client_for_role(role: str, settings: Settings | None = None) -> LLMClient
def count_tokens(text: str) -> int    # heuristiek: ceil(len/3.2)
```
`<think>…</think>` en `reasoning_content` worden uit `text` gehaald en naar `reasoning`/`on_reasoning` gestuurd.

## store/ (SQLite, WAL)

Tabellen (zie ONTWERP.md §12.2): project, template, chat, message, memory_item, document, doc_version, job, job_event, source, chunk (+ FTS5), glossary, file.
Repo-API in `store/repo.py` met simpele functies die dicts teruggeven, o.a.:
`create_project`, `list_projects`, `get_project`, `update_project`, `delete_project`,
`add_template`, `list_templates`, `get_template`,
`create_chat(project_id, title, privacy="project", internet=False)`, `list_chats`, `get_chat`, `update_chat`,
`add_message(chat_id, role, content, meta=None)`, `list_messages(chat_id, include_compacted=True)`,
`add_file(owner_kind, owner_id, name, path, mime)`, `get_file`, `list_files`,
`create_document(project_id, name, kind)`, `add_version(document_id, path, job_id, chat_id, changes)`, `list_documents(project_id)`, `get_version`,
`create_job(chat_id, pipeline, params)`, `update_job(...)`, `add_job_event(job_id, event: dict)`, `list_job_events(job_id, after_id=0)`,
`add_memory_item`, `list_memory_items`, `set_chat_summary`.

## Privacy

`chat.privacy ∈ {"private", "project", "all"}`, `chat.internet: bool` (in private altijd False).

## HTTP-API (engine)

Basis `http://127.0.0.1:<port>`; header `X-DocWizard-Token: <token>` verplicht (de UI krijgt het token via `window.DOCWIZARD_TOKEN`, ingevoegd in index.html door de server).

| Methode | Pad | Body / respons |
|---|---|---|
| GET | `/api/health` | `{ok, version}` |
| GET/PUT | `/api/settings` | Settings-JSON |
| POST | `/api/connections/test` | body `Connection` → `{ok, models:[{id,context_length}], latency_ms, context, error}` |
| GET | `/api/projects` | `[{id,name,client,template_id,privacy_default,created_at,updated_at}]` |
| POST | `/api/projects` | `{name, client?, template_id?, privacy_default?}` |
| PATCH/DELETE | `/api/projects/{id}` | |
| GET | `/api/templates` | `[{id,name,version,created_at,profile_summary}]` |
| POST | `/api/templates` | multipart `file`, `name` → template incl. `profile` |
| GET | `/api/templates/{id}` | incl. `profile` (outline, velden, huisstijl) |
| GET | `/api/projects/{id}/chats` | `[{id,title,privacy,internet,updated_at}]` |
| POST | `/api/projects/{id}/chats` | `{title?, privacy?, internet?}` |
| PATCH | `/api/chats/{id}` | `{title?, privacy?, internet?, roles?}` |
| GET | `/api/chats/{id}/messages` | `[{id,role,content,created_at,meta}]` — role ∈ user/assistant/system/event; `meta.job_id`, `meta.compacted_count` |
| POST | `/api/chats/{id}/messages` | `{text, file_ids:[], action:"auto"\|"chat"\|"new_document"\|"add_chapter"\|"translate"\|"correct"\|"review"\|"enrich"\|"diagram", params:{}}` → `{message, job_id?}` |
| POST | `/api/files` | multipart `file`, `chat_id` of `project_id` → `{id,name,size}` |
| GET | `/api/projects/{id}/documents` | `[{id,name,kind,versions:[{id,number,created_at,changes}]}]` |
| GET | `/api/versions/{id}/download` | .docx |
| GET | `/api/versions/{id}/preview` | HTML-voorvertoning (fragment) |
| GET | `/api/versions/{id}/outline` | `[{number,title,level}]` |
| POST | `/api/versions/{id}/open` | opent in Word (OS-default app) |
| GET | `/api/jobs/{id}` | `{id,pipeline,status,progress,steps:[{key,label,status,detail,started_at,ended_at}],error}` |
| POST | `/api/jobs/{id}/cancel` | |
| POST | `/api/jobs/{id}/answer` | `{question_id, answer}` — goedkeuring/keuze |
| GET | `/api/diagrams/{id}.svg` / `.drawio` / `.png` | |
| GET | `/api/events?chat_id=` | **SSE**-stroom, zie hieronder |

### SSE-events (`data: <json>`), altijd met `type`, `chat_id`, `job_id?`, `ts`

| type | velden |
|---|---|
| `job_started` | `pipeline`, `title`, `steps:[{key,label}]` |
| `step` | `key`, `label`, `status ∈ pending/running/done/failed/skipped`, `detail?`, `progress?` (0..1 totaal) |
| `token` | `key` (stap), `text` (delta, gegenereerde tekst) |
| `reasoning` | `key`, `text` (delta, denkstappen) |
| `log` | `level`, `text` |
| `source` | `title`, `url?`, `kind ∈ web/file/project/knowledge`, `snippet?` |
| `question` | `question_id`, `text`, `options:[{value,label}]`, `editable?: {kind:"text"\|"json", value}` — UI toont knoppen/editor en POST naar `/answer` |
| `artifact` | `kind ∈ document/diagram`, `id`, `name`, `version_id?`, `url?` |
| `message` | `message` (nieuw chatbericht object) |
| `job_done` | `summary` |
| `job_failed` | `error` |
| `stats` | `tokens_per_s`, `eta_s?` |

## Afwijkingen backend

Alleen aanvullingen; bestaande signaturen blijven werken.

- **config**: `settings.json` staat altijd in de basis-datamap (`DOCWIZARD_HOME` of de OS-map), ook als `data_dir_override` is gezet. De override verplaatst alleen db/templates/projects/logs/cache. `app_paths(settings=None)`. Extra: `Settings.add_connection(name, base_url, *, api_key, model, kind, context_limit) -> Connection` (unieke slug-id), `Settings.remove_connection(id)`, `Settings.connection(id)`. Een `base_url` zonder pad krijgt automatisch `/v1`. Standaardrollen: `{"writer": "lmstudio", "fast": "mtplx", "embedding": "", "vision": ""}`.
- **llm**: `LLMClient(conn, *, num_ctx=None, transport=None)`. `chat(...)` heeft extra kwargs `schema_name` en `think: bool | None` (`False` = denken uit: Ollama `reasoning_effort:"none"`, andere servers `chat_template_kwargs.enable_thinking=false`; live geverifieerd op Ollama en MTPLX). Fouten: `LLMError(status)`, `LLMCancelled(partial: ChatResult)` bij annuleren, `LLMJSONError(last_text)` als `chat_json` opgeeft. `model()` (async) geeft het ingestelde model of het eerste niet-embeddingmodel van de server. `client_for_role`: rollen zonder verbinding vallen terug op `writer`, behalve `embedding` en `vision` (die geven `LLMError`).
- **Ollama-contextlengte**: het OpenAI-endpoint van Ollama negeert `options.num_ctx`. We sturen het wel mee, maar de echte context is die waarmee de server het model laadt (`OLLAMA_CONTEXT_LENGTH`, of `num_ctx` in de Modelfile). `detect_context()` leest daarom eerst `/api/ps` (de geladen context), dan `/api/show`.
- **store**: `message` heeft een oplopende kolom `seq` voor de volgorde. `job_event.id` is een integer (voor `after_id`). `doc_version.number` is een string `"0.1"`, `"0.2"`, … Soorten geheugenitems: `fact|decision|preference|assumption|question`. Bij `privacy="private"` is `chat.internet` altijd False (afgedwongen in de repo). Extra functies: `delete_chat`, `touch_chat`, `get_message`, `update_message`, `mark_messages_compacted`, `delete_file`, `get_document`, `update_document`, `delete_document`, `list_versions`, `get_job`, `list_jobs`, `update_memory_item`, `delete_memory_item`, `update_template`, `delete_template`, `add_glossary_term`, `list_glossary`, `add_audit`, `list_audit`. Voor tests: `store.set_db_path(path)` en `store.close_all()`.
- **knowledge**: `search(...)` is synchroon (BM25, met optioneel `query_vec`). `search_hybrid(...)` is async en voegt embeddings toe (RRF) als de rol `embedding` is ingesteld en bereikbaar is. `embed_pending(client=None)` is async en embedt nieuwe chunks. Kennispakketten (`scope_kind="global"`) zijn in elke modus zichtbaar, ook privé, want ze staan lokaal. Bronnen van *andere* privéchats zijn nooit zichtbaar.
- **research**: `search(query, n=8, *, chat, web=None, egress=None)`, `fetch(url, *, chat, egress=None)` en `search_wetten(query, n=8, *, chat, on=None, ...)` vragen allemaal de chat-dict (voor de egress-check). `make_egress(web)` bouwt een gateway met de allowlist uit de instellingen; zoek-API-hosts zijn vrijgesteld van de allowlist. Localhost is altijd toegestaan en wordt niet geaudit; elk ander verzoek komt in `audit` (kind `network`). Redirects worden per hop gecontroleerd.

## Afwijkingen UI

Aanvullingen die de UI (`src/docwizard/web/`) gebruikt, afgestemd op `server/app.py`. De mock (`scripts/mock_server.py`) doet hetzelfde. *Optioneel* = de UI werkt ook zonder.

- **Token als queryparameter**: naast `X-DocWizard-Token` werkt ook `?token=`. Dat is nodig voor `GET /api/events` (EventSource kan geen headers zetten), voor downloads (`/api/versions/{id}/download`, `/api/diagrams/{id}.drawio|png`) en voor `<img src>` (`/api/diagrams/{id}.svg`).
- **Jobs bij een chat**: de engine zet `meta.job_id` op het **gebruikersbericht**; de UI zet de jobkaart direct daaronder. Bij het openen van een chat haalt de UI `GET /api/chats/{id}/jobs` op. Jobs zonder bericht (bv. hervatte jobs) worden op `created_at` tussen de berichten gezet. Daarna, per job: `GET /api/jobs/{id}` (status, stappen, `pending_question`) en `GET /api/jobs/{id}/events?after=<id>` (opgeslagen events: stappen, logs, bronnen, vragen, artefacten, berichten; geen tokens/reasoning). Dat gebeurt ook na het herverbinden van SSE. Dubbele events filtert de UI weg op een handtekening (`type`+`ts`+inhoud), omdat SSE-events geen id hebben en het `id` van een opgeslagen `artifact`-event het artefact-id is.
- **`pending_question`** in `GET /api/jobs/{id}`: hiermee toont de UI een openstaande vraag opnieuw na herladen.
- **Antwoord**: `POST /api/jobs/{id}/answer` `{"question_id", "answer": <option value>, "edited": <object|string|null>}`. Bij `editable.kind:"json"` stuurt de UI de geparste JSON als `edited`, bij `kind:"text"` een string.
- **Hervatten**: bij status `failed`/`cancelled` toont de jobkaart de knop "Hervatten". Die roept `POST /api/jobs/{id}/retry` → `{job_id}` aan en zet de nieuwe jobkaart direct onder de oude.
- **Annuleren herkennen**: `job_failed` met een `error` als "Geannuleerd"/"Gestopt"/"cancel" toont de UI als *Gestopt* in plaats van *Mislukt*.
- **Stappenvolgorde**: een herhaald `job_started` (bv. na `add_steps`) en `GET /api/jobs/{id}.steps` bepalen de volgorde. `title:null` laat de bestaande titel staan. Tijden komen uit `started_at`/`ended_at` van de stap, of anders uit `ts` (ISO of epoch).
- **Pauzeren** (*optioneel*, alleen in de mock): `POST /api/jobs/{id}/pause` en `POST /api/jobs/{id}/resume`, event `job_status {status}`. De UI toont de knop alleen als `GET /api/jobs/{id}` `pausable: true` teruggeeft.
- **Chatberichten met `role:"event"`** (bv. compactie, `meta.compacted_count`, `meta.summary`) toont de UI als een rustige scheidingslijn. Het `assistant`-bericht van een `chat`-job (`meta.job_id`) vervangt de streaming antwoordballon. Jobs met pipeline `chat` krijgen geen stappenkaart maar een ballon. Jobs met pipeline `auto` krijgen een normale kaart, die na afloop inklapt.
- **`artifact`-event**: de UI toont `version` (bijv. `"0.2"`) als die er is. Voor diagrammen is `url` de SVG; anders gebruikt de UI `/api/diagrams/{id}.svg`.
- **`POST /api/versions/{id}/open`** met `{"reveal": true}`: de knop "Toon in Finder" (Windows: "Toon in Verkenner").
- **Chat-object**: `roles` (`{writer?: conn_id}`; `{}` = standaard) wordt gelezen voor de modelkeuze per chat.
- **Projectbestanden** (*optioneel*, scherm Kennis): `GET /api/projects/{id}/files` → `[{id,name,size,created_at}]`. Uploaden gaat via `POST /api/files` met `project_id`.
- **Voorvertoning**: het HTML-fragment staat in een volledig gesandboxt iframe (`sandbox=""` + CSP) op een papierachtig vel. Scripts en externe bronnen werken daar niet; afbeeldingen alleen als `data:`.
- **Sjabloonprofiel** (`profile`), gelezen met Engelse of Nederlandse sleutels (§8.1): `doc_type`, `language`, `outline:[{number,title,level,guidance?,fixed_text?,length?,table?:{columns},research?,children?}]`, `fields:[{key,label,source,default?}]`, `house_style:{…}`, `writing_guide:{…}`. `profile_summary` is een korte tekst voor de lijst.
- **Fouten**: de UI toont `detail` uit foutantwoorden letterlijk (string of FastAPI-validatielijst). Houd die teksten Nederlands.
- **UI-voorkeuren** in `localStorage`: thema (systeem/licht/donker), ingeklapte panelen, de open chat en uitgeklapte projecten. `ui_language` komt uit de instellingen.
- **pywebview**: zet `webview.settings['ALLOW_DOWNLOADS'] = True`, anders werken de downloadlinks niet in WKWebView.

## Presentaties (pptengine) — toegevoegd 11-10-2026

```python
from docwizard.pptengine import (DeckSpec, normalize_deck, build_pptx, render_previews, analyze_pptx_template,
                                 Theme, THEMES, theme_from_docx, DECK_PROMPT_GUIDE, DECK_JSON_SCHEMA, SLIDE_TYPES)
deck: DeckSpec = normalize_deck(raw)                # lenient, fixes typical local-LLM mistakes
report: dict = build_pptx(deck, out_path, *, theme: Theme | str = "helder", template: str | None = None,
                          images: Callable[[str], tuple[bytes, str] | None] | None = None)
                                                    # -> {"slides": n, "warnings": [...], "split": n}
pngs: list[bytes] = render_previews(deck, *, theme=..., template=None, images=None, width=1280)
profile: dict = analyze_pptx_template(path)         # layouts, placeholders, theme colours/fonts, slide size
theme: Theme = theme_from_docx(docx_path)           # house colours + fonts taken from a Word template
```
Slide types: title, agenda, section, bullets, two_column, kpi, quote, timeline, table, chart, diagram, image, comparison, closing.
Image refs in the spec: `diagram:<id>` (resolved by the caller) or media part names from a source .docx.
