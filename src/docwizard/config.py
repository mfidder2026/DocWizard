"""Settings (JSON in the data dir) and application paths."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from docwizard.platform import default_data_dir

ConnectionKind = Literal["lmstudio", "ollama", "mtplx", "openai"]


class Connection(BaseModel):
    id: str
    name: str
    kind: ConnectionKind = "openai"
    base_url: str
    api_key: str = ""
    model: str = ""
    context_limit: int | None = None
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _normalize_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and "://" not in v:
            v = "http://" + v
        if v and urlsplit(v).path in ("", "/"):
            v += "/v1"
        return v


class WebSearchSettings(BaseModel):
    provider: Literal["duckduckgo", "searxng", "brave"] = "duckduckgo"
    url: str = ""
    api_key: str = ""
    confirm_queries: bool = True
    allow_domains: list[str] = Field(default_factory=list)


def default_connections() -> list[Connection]:
    return [
        Connection(id="lmstudio", name="LM Studio", kind="lmstudio", base_url="http://localhost:1234/v1"),
        Connection(id="ollama", name="Ollama", kind="ollama", base_url="http://localhost:11434/v1"),
        Connection(id="mtplx", name="MTPLX", kind="mtplx", base_url="http://localhost:8000/v1"),
    ]


def default_roles() -> dict[str, str]:
    return {"writer": "lmstudio", "fast": "mtplx", "embedding": "", "vision": ""}


class Settings(BaseModel):
    ui_language: Literal["nl", "en"] = "nl"
    doc_language: str = "nl"
    author_name: str = ""
    connections: list[Connection] = Field(default_factory=default_connections)
    roles: dict[str, str] = Field(default_factory=default_roles)
    quality_context_tokens: int = 32000
    max_output_tokens: int = 4096
    allow_thinking: bool = False   # reasoning models: thinking costs many tokens and can leave no answer
    web: WebSearchSettings = Field(default_factory=WebSearchSettings)
    data_dir_override: str = ""

    def connection(self, conn_id: str) -> Connection | None:
        return next((c for c in self.connections if c.id == conn_id), None)

    def add_connection(
        self,
        name: str,
        base_url: str,
        *,
        api_key: str = "",
        model: str = "",
        kind: ConnectionKind = "openai",
        context_limit: int | None = None,
    ) -> Connection:
        """Add a custom OpenAI-compatible connection with a unique slug id."""
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "server"
        cid, n = base, 2
        while self.connection(cid):
            cid, n = f"{base}-{n}", n + 1
        conn = Connection(
            id=cid, name=name, kind=kind, base_url=base_url,
            api_key=api_key, model=model, context_limit=context_limit,
        )
        self.connections.append(conn)
        return conn

    def remove_connection(self, conn_id: str) -> None:
        self.connections = [c for c in self.connections if c.id != conn_id]
        for role, cid in list(self.roles.items()):
            if cid == conn_id:
                self.roles[role] = ""


@dataclass(frozen=True)
class AppPaths:
    home: Path
    db: Path
    templates: Path
    projects: Path
    logs: Path
    cache: Path


def settings_path() -> Path:
    """settings.json always lives in the base data dir (not the override)."""
    return default_data_dir() / "settings.json"


def load_settings() -> Settings:
    path = settings_path()
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        # Keep a copy of the unreadable file and fall back to defaults.
        try:
            shutil.copy2(path, path.with_suffix(".json.bad"))
        except OSError:
            pass
        return Settings()


def save_settings(s: Settings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def app_paths(settings: Settings | None = None) -> AppPaths:
    s = settings if settings is not None else load_settings()
    home = Path(s.data_dir_override).expanduser() if s.data_dir_override else default_data_dir()
    paths = AppPaths(
        home=home,
        db=home / "docwizard.db",
        templates=home / "templates",
        projects=home / "projects",
        logs=home / "logs",
        cache=home / "cache",
    )
    for d in (paths.home, paths.templates, paths.projects, paths.logs, paths.cache):
        d.mkdir(parents=True, exist_ok=True)
    return paths
