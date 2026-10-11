"""Fetch a web page and extract its main text (trafilatura, fallback BeautifulSoup)."""

from __future__ import annotations

from datetime import datetime, timezone

import trafilatura
from bs4 import BeautifulSoup

from docwizard.research.egress import Egress
from docwizard.research.search import SearchError, make_egress


def extract_text(html: str, url: str = "") -> tuple[str, str]:
    """(title, text) from HTML."""
    title = ""
    text = trafilatura.extract(html, url=url or None, include_comments=False, include_tables=True,
                               favor_recall=True) or ""
    meta = trafilatura.extract_metadata(html)
    if meta and meta.title:
        title = meta.title
    if not text.strip() or not title:
        soup = BeautifulSoup(html, "html.parser")
        if not title and soup.title:
            title = soup.title.get_text(strip=True)
        if not text.strip():
            for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
                tag.decompose()
            text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return title.strip(), text.strip()


async def fetch(url: str, *, chat: dict | None, egress: Egress | None = None) -> dict:
    """{url, title, text, fetched_at}. Only GET; HTML and plain text are supported."""
    egress = egress or make_egress()
    r = await egress.get(url, chat)
    if r.status_code != 200:
        raise SearchError(f"ophalen mislukt: HTTP {r.status_code} voor {url}")
    ctype = r.headers.get("content-type", "").lower()
    if "html" in ctype or "xml" in ctype or not ctype:
        title, text = extract_text(r.text, str(r.url))
    elif ctype.startswith("text/"):
        title, text = "", r.text
    else:
        raise SearchError(f"inhoudstype niet ondersteund: {ctype}")
    return {
        "url": str(r.url),
        "title": title or url,
        "text": text,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
