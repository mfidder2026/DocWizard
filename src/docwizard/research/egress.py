"""Egress gateway: the only path for outbound HTTP to the internet (ONTWERP §15.1)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
USER_AGENT = "Mozilla/5.0 (compatible; DocWizard/0.1; +https://localhost)"
MAX_BYTES = 8 * 1024 * 1024


class EgressDenied(PermissionError):
    pass


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _domain_ok(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in (d.lower().lstrip(".") for d in domains))


class Egress:
    """`await egress.get(url, chat)` / `post(...)`. Local hosts are always allowed;
    internet only if chat["internet"] and chat["privacy"] != "private" and the domain
    passes `allow_domains` (empty = all). `service_domains` (search APIs) skip the allowlist.
    Every attempt is recorded in `self.log` and, with `persist=True`, in the audit table."""

    def __init__(
        self,
        allow_domains: Iterable[str] = (),
        *,
        service_domains: Iterable[str] = (),
        persist: bool = True,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.allow_domains = [d for d in allow_domains if d]
        self.service_domains = [d for d in service_domains if d]
        self.persist = persist
        self.timeout = timeout
        self.transport = transport
        self.log: list[dict] = []

    def check(self, url: str, chat: dict | None) -> None:
        scheme = urlsplit(url).scheme
        host = _host(url)
        if scheme not in ("http", "https") or not host:
            raise EgressDenied(f"ongeldige URL: {url}")
        if host in LOCAL_HOSTS:
            return
        if not chat or not chat.get("internet"):
            raise EgressDenied("internet staat uit voor deze chat")
        if chat.get("privacy") == "private":
            raise EgressDenied("privémodus: geen internet")
        if self.allow_domains and not (_domain_ok(host, self.allow_domains) or _domain_ok(host, self.service_domains)):
            raise EgressDenied(f"domein niet toegestaan: {host}")

    def _record(self, method: str, url: str, chat: dict | None, *, allowed: bool, status: int | None = None,
                reason: str = "") -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "method": method, "url": url, "host": _host(url),
            "chat_id": (chat or {}).get("id"), "allowed": allowed, "status": status, "reason": reason,
        }
        self.log.append(entry)
        if self.persist and _host(url) not in LOCAL_HOSTS:
            try:
                from docwizard.store import repo

                repo.add_audit("network", entry)
            except Exception as e:  # noqa: BLE001 - auditing must not break research
                log.warning("audit schrijven mislukt: %s", e)

    async def request(self, method: str, url: str, chat: dict | None, **kw: Any) -> httpx.Response:
        try:
            self.check(url, chat)
        except EgressDenied as e:
            self._record(method, url, chat, allowed=False, reason=str(e))
            raise
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "nl,en;q=0.8", **kw.pop("headers", {})}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, transport=self.transport) as h:
            # Follow redirects manually so every hop passes the policy.
            for _ in range(6):
                resp = await h.request(method, url, headers=headers, **kw)
                self._record(method, url, chat, allowed=True, status=resp.status_code)
                if resp.is_redirect and resp.headers.get("location"):
                    url = str(resp.url.join(resp.headers["location"]))
                    try:
                        self.check(url, chat)
                    except EgressDenied as e:
                        self._record(method, url, chat, allowed=False, reason=str(e))
                        raise
                    if resp.status_code in (301, 302, 303):
                        method, kw = "GET", {k: v for k, v in kw.items() if k not in ("data", "json", "content")}
                    continue
                if len(resp.content) > MAX_BYTES:
                    raise EgressDenied(f"antwoord te groot: {len(resp.content)} bytes")
                return resp
        raise EgressDenied("te veel redirects")

    async def get(self, url: str, chat: dict | None, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, chat, **kw)

    async def post(self, url: str, chat: dict | None, **kw: Any) -> httpx.Response:
        """Only for search forms (the query itself); document content never leaves the machine."""
        return await self.request("POST", url, chat, **kw)
