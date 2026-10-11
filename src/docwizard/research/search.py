"""Web search providers (DuckDuckGo HTML, SearXNG, Brave) and wetten.overheid.nl (SRU)."""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from docwizard.config import WebSearchSettings
from docwizard.research.egress import Egress

log = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
SRU_URL = "https://zoekservice.overheid.nl/sru/Search"
SERVICE_DOMAINS = ["duckduckgo.com", "api.search.brave.com", "zoekservice.overheid.nl"]


class SearchError(RuntimeError):
    pass


def make_egress(web: WebSearchSettings | None = None, **kw) -> Egress:
    web = web or WebSearchSettings()
    services = SERVICE_DOMAINS + ([urlsplit(web.url).hostname or ""] if web.url else [])
    return Egress(web.allow_domains, service_domains=services, **kw)


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<url>."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [""])[0]
        return unquote(target) if target else href
    return href


def parse_ddg_html(html: str, n: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for res in soup.select("div.result"):
        if "result--ad" in (res.get("class") or []):
            continue
        a = res.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        url = _ddg_target(a["href"])
        if "duckduckgo.com/y.js" in url:  # ads
            continue
        snippet = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True), "url": url,
                    "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
        if len(out) >= n:
            break
    return out


async def _ddg(query: str, n: int, egress: Egress, chat: dict | None) -> list[dict]:
    r = await egress.post(DDG_URL, chat, data={"q": query, "kl": "nl-nl"})
    if r.status_code != 200:
        raise SearchError(f"DuckDuckGo gaf HTTP {r.status_code} (mogelijk tijdelijk geblokkeerd)")
    results = parse_ddg_html(r.text, n)
    if not results and "anomaly" in r.text.lower():
        raise SearchError("DuckDuckGo vraagt om een botcontrole; probeer later opnieuw of gebruik SearXNG")
    return results


async def _searxng(query: str, n: int, web: WebSearchSettings, egress: Egress, chat: dict | None) -> list[dict]:
    if not web.url:
        raise SearchError("SearXNG-URL ontbreekt in de instellingen")
    r = await egress.get(f"{web.url.rstrip('/')}/search", chat, params={"q": query, "format": "json"})
    if r.status_code != 200:
        raise SearchError(f"SearXNG gaf HTTP {r.status_code}")
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:n]]


async def _brave(query: str, n: int, web: WebSearchSettings, egress: Egress, chat: dict | None) -> list[dict]:
    if not web.api_key:
        raise SearchError("Brave API-sleutel ontbreekt in de instellingen")
    r = await egress.get(BRAVE_URL, chat, params={"q": query, "count": min(n, 20)},
                         headers={"X-Subscription-Token": web.api_key, "Accept": "application/json"})
    if r.status_code != 200:
        raise SearchError(f"Brave gaf HTTP {r.status_code}")
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
            for x in (r.json().get("web") or {}).get("results", [])[:n]]


async def search(
    query: str,
    n: int = 8,
    *,
    chat: dict | None,
    web: WebSearchSettings | None = None,
    egress: Egress | None = None,
) -> list[dict]:
    """[{title, url, snippet}] via the configured provider. Sanitize the query first!"""
    web = web or WebSearchSettings()
    egress = egress or make_egress(web)
    if web.provider == "searxng":
        results = await _searxng(query, n, web, egress, chat)
    elif web.provider == "brave":
        results = await _brave(query, n, web, egress, chat)
    else:
        results = await _ddg(query, n, egress, chat)
    seen, out = set(), []
    for r in results:
        key = r["url"].rstrip("/")
        if r["url"] and key not in seen:
            seen.add(key)
            out.append(r)
    return out


# ---- wetten.overheid.nl ----------------------------------------------------
_NS = {
    "sru": "http://docs.oasis-open.org/ns/search-ws/sruResponse",
    "dcterms": "http://purl.org/dc/terms/",
    "overheidbwb": "http://standaarden.overheid.nl/bwb/terms/",
}


def _cql(query: str, on: date, mode: str = "all") -> str:
    words = [w for w in re.findall(r"[\w-]+", query) if len(w) > 1]
    terms = " ".join(words).replace('"', "")
    return f'overheidbwb.titel {mode} "{terms}" and overheidbwb.geldigheidsdatum={on.isoformat()}'


def parse_sru(xml: str, n: int, query: str = "") -> list[dict]:
    root = ET.fromstring(xml)
    out, seen = [], set()
    for rec in root.iter(f"{{{_NS['sru']}}}record"):
        ident = rec.findtext(".//dcterms:identifier", default="", namespaces=_NS)
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append({
            "title": rec.findtext(".//dcterms:title", default="", namespaces=_NS),
            "url": f"https://wetten.overheid.nl/{ident}/",
            "snippet": rec.findtext(".//dcterms:type", default="", namespaces=_NS),
            "bwb_id": ident,
            "valid_from": rec.findtext(".//overheidbwb:geldigheidsperiode_startdatum", default="", namespaces=_NS),
        })
    # Most query words in the title, then formal acts (wet), then shorter titles.
    words = {w.lower() for w in re.findall(r"\w+", query) if len(w) > 2}

    def key(x: dict) -> tuple:
        hits = sum(w in x["title"].lower() for w in words)
        return (-hits, x["snippet"] != "wet", not x["snippet"].startswith("wet"), len(x["title"]))

    out.sort(key=key)
    return out[:n]


async def search_wetten(
    query: str,
    n: int = 8,
    *,
    chat: dict | None,
    on: date | None = None,
    web: WebSearchSettings | None = None,
    egress: Egress | None = None,
) -> list[dict]:
    """Current Dutch legislation by title via the BWB SRU service ([{title, url, snippet,
    bwb_id, valid_from}]); falls back to a DuckDuckGo `site:wetten.overheid.nl` search
    when the SRU service fails."""
    egress = egress or make_egress(web)
    sru_ok = False
    try:
        for mode in ("all", "any"):  # all words in the title first, then any
            r = await egress.get(SRU_URL, chat, params={
                "operation": "searchRetrieve", "version": "2.0", "x-connection": "BWB",
                "maximumRecords": str(max(n * 3, 20)), "query": _cql(query, on or date.today(), mode),
            }, headers={"Accept": "application/xml, text/xml;q=0.9, */*;q=0.1"})
            # The service answers 406 for some Accept headers but still sends a valid body.
            if r.status_code in (200, 406) and r.text.lstrip().startswith("<?xml"):
                sru_ok = True
                results = parse_sru(r.text, n, query)
                if results:
                    return results
    except (ET.ParseError, httpx.HTTPError) as e:
        log.info("SRU niet bruikbaar, val terug op zoekmachine: %s", e)
    if sru_ok:  # the service works, there simply is no matching act
        return []
    return await _ddg(f"site:wetten.overheid.nl {query}", n, egress, chat)
