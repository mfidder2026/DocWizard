"""Research: egress policy, providers (mocked), sanitizer. Live tests: DOCWIZARD_NET_TESTS=1."""

import asyncio
import os

import httpx
import pytest

from docwizard.config import WebSearchSettings
from docwizard.research import Egress, EgressDenied, fetch, make_egress, sanitize_query, search, search_wetten
from docwizard.store import close_all, repo, set_db_path

ONLINE = {"id": "c1", "internet": True, "privacy": "project"}
NET = os.environ.get("DOCWIZARD_NET_TESTS") == "1"


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWIZARD_HOME", str(tmp_path))
    set_db_path(tmp_path / "test.db")
    yield
    close_all()
    set_db_path(None)


def run(coro):
    return asyncio.run(coro)


def mock(handler, **kw):
    return Egress(transport=httpx.MockTransport(handler), **kw)


# ---- egress ----------------------------------------------------------------
def test_egress_policy():
    e = mock(lambda r: httpx.Response(200, text="ok"))
    for chat in (None, {"internet": False, "privacy": "project"}, {"internet": True, "privacy": "private"}):
        with pytest.raises(EgressDenied):
            run(e.get("https://example.org/", chat))
    assert run(e.get("http://localhost:8888/search", None)).text == "ok"  # local always ok
    assert run(e.get("https://example.org/", ONLINE)).status_code == 200
    with pytest.raises(EgressDenied):
        run(e.get("file:///etc/passwd", ONLINE))
    assert [x["allowed"] for x in e.log] == [False, False, False, True, True, False]
    audit = repo.list_audit("network")
    assert len(audit) == 5 and all(a["details"]["host"] != "localhost" for a in audit)  # local not audited


def test_egress_allowlist_and_redirects():
    def handler(req):
        if req.url.host == "www.overheid.nl" and req.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://evil.example.com/x"})
        if req.url.path == "/hop":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(200, text=f"hier {req.url}")

    e = mock(handler, allow_domains=["overheid.nl"], service_domains=["duckduckgo.com"])
    assert run(e.get("https://wetten.overheid.nl/x", ONLINE)).status_code == 200
    assert run(e.get("https://html.duckduckgo.com/html/", ONLINE)).status_code == 200
    with pytest.raises(EgressDenied):
        run(e.get("https://example.org/", ONLINE))
    with pytest.raises(EgressDenied):  # redirect leaving the allowlist
        run(e.get("https://www.overheid.nl/go", ONLINE))
    r = run(e.get("https://www.overheid.nl/hop", ONLINE))
    assert r.text.endswith("/final")


# ---- providers (mocked) ------------------------------------------------------
DDG_HTML = """
<div class="result results_links result--ad"><a class="result__a" href="https://ad.example/">Ad</a></div>
<div class="result results_links"><h2><a class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.nationaalarchief.nl%2Farchiefwet&amp;rut=x">Archiefwet | NA</a></h2>
  <a class="result__snippet">De <b>Archiefwet</b> verplicht…</a></div>
<div class="result results_links"><a class="result__a" href="https://wetten.overheid.nl/BWBR0007376/">Archiefwet 1995</a></div>
<div class="result results_links"><a class="result__a" href="https://wetten.overheid.nl/BWBR0007376">dubbel</a></div>
"""


def test_duckduckgo_parsing():
    seen = {}

    def handler(req):
        seen["method"], seen["body"] = req.method, req.content.decode()
        return httpx.Response(200, text=DDG_HTML)

    res = run(search("archiefwet", chat=ONLINE, egress=mock(handler)))
    assert seen["method"] == "POST" and "q=archiefwet" in seen["body"]
    assert [r["url"] for r in res] == ["https://www.nationaalarchief.nl/archiefwet",
                                       "https://wetten.overheid.nl/BWBR0007376/"]
    assert res[0]["title"] == "Archiefwet | NA" and "verplicht" in res[0]["snippet"]


def test_searxng_and_brave():
    def handler(req):
        if req.url.host == "searx.local":
            assert req.url.params["format"] == "json"
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://a/", "content": "S"}]})
        assert req.headers["x-subscription-token"] == "key"
        return httpx.Response(200, json={"web": {"results": [{"title": "B", "url": "https://b/",
                                                              "description": "D"}]}})

    sx = WebSearchSettings(provider="searxng", url="http://searx.local")
    assert run(search("q", chat=ONLINE, web=sx, egress=mock(handler)))[0]["snippet"] == "S"
    br = WebSearchSettings(provider="brave", api_key="key")
    assert run(search("q", chat=ONLINE, web=br, egress=mock(handler)))[0]["url"] == "https://b/"


SRU_XML = """<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://docs.oasis-open.org/ns/search-ws/sruResponse"><records>
<record><recordData><gzd xmlns="http://standaarden.overheid.nl/sru" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:overheidbwb="http://standaarden.overheid.nl/bwb/terms/"><originalData><overheidbwb:meta><owmskern>
 <dcterms:identifier>BWBR0028369</dcterms:identifier><dcterms:title>Archiefwet BES</dcterms:title>
 <dcterms:type>wet-BES</dcterms:type></owmskern></overheidbwb:meta></originalData></gzd></recordData></record>
<record><recordData><gzd xmlns="http://standaarden.overheid.nl/sru" xmlns:dcterms="http://purl.org/dc/terms/">
 <originalData><owmskern><dcterms:identifier>BWBR0007376</dcterms:identifier>
 <dcterms:title>Archiefwet 1995</dcterms:title><dcterms:type>wet</dcterms:type></owmskern></originalData></gzd>
</recordData></record></records></searchRetrieveResponse>"""


def test_search_wetten_sru_and_fallback():
    def ok(req):
        assert req.url.host == "zoekservice.overheid.nl"
        assert "overheidbwb.titel all" in req.url.params["query"]
        return httpx.Response(406, text=SRU_XML)  # the service sometimes answers 406 with a valid body

    res = run(search_wetten("archiefwet", chat=ONLINE, egress=mock(ok)))
    assert [r["bwb_id"] for r in res] == ["BWBR0007376", "BWBR0028369"]  # formal 'wet' first
    assert res[0]["url"] == "https://wetten.overheid.nl/BWBR0007376/"

    def broken(req):
        if req.url.host == "zoekservice.overheid.nl":
            return httpx.Response(500, text="kapot")
        assert "site%3Awetten.overheid.nl" in req.content.decode()
        return httpx.Response(200, text=DDG_HTML)

    assert run(search_wetten("archiefwet", chat=ONLINE, egress=mock(broken)))


def test_fetch_extracts_text():
    html = """<html><head><title>Bewaartermijnen</title></head><body><nav>menu menu</nav>
    <article><h1>Bewaartermijnen</h1><p>De Archiefwet bepaalt dat overheidsorganisaties hun archiefbescheiden
    in goede, geordende en toegankelijke staat moeten brengen en bewaren. Dit geldt voor alle
    overheidsorganen.</p><p>Vernietiging gebeurt volgens een selectielijst.</p></article>
    <footer>cookies</footer></body></html>"""
    e = mock(lambda r: httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"}))
    page = run(fetch("https://example.org/bt", chat=ONLINE, egress=e))
    assert page["title"] == "Bewaartermijnen" and "selectielijst" in page["text"]
    assert page["url"] == "https://example.org/bt" and page["fetched_at"]
    with pytest.raises(EgressDenied):
        run(fetch("https://example.org/bt", chat={"internet": False}, egress=e))


# ---- sanitizer ---------------------------------------------------------------
def test_sanitize_query():
    q, removed = sanitize_query("RIVM migratie zaaksysteem Corsa budget 2,3 mln euro", ["RIVM", "Corsa"])
    assert q == "migratie zaaksysteem budget"
    assert set(removed) == {"RIVM", "Corsa", "2,3 mln euro"}
    q, removed = sanitize_query("mail jan@rivm.nl of bel 06-12345678 of +31 20 123 4567", ["rivm"])
    assert "@" not in q and "0612" not in q.replace("-", "") and "31" not in q
    assert "jan@rivm.nl" in removed
    q, removed = sanitize_query("IBAN NL91 ABNA 0417 1643 00 BSN 123456782 € 450.000")
    assert q == "IBAN BSN" and len(removed) == 3
    q, removed = sanitize_query("Archiefwet 1995 artikel 5 bewaartermijn 2024")
    assert q == "Archiefwet 1995 artikel 5 bewaartermijn 2024" and removed == []


# ---- live --------------------------------------------------------------------
@pytest.mark.skipif(not NET, reason="netwerktests: zet DOCWIZARD_NET_TESTS=1")
def test_live_search_fetch_wetten():
    e = make_egress()
    wet = run(search_wetten("archiefwet", 3, chat=ONLINE, egress=e))
    assert any("Archiefwet" in r["title"] for r in wet)
    page = run(fetch(wet[0]["url"], chat=ONLINE, egress=e))
    assert len(page["text"]) > 500
    try:
        res = run(search("archiefwet bewaartermijn", 5, chat=ONLINE, egress=e))
        assert res and res[0]["url"].startswith("http")
    except Exception as ex:  # DuckDuckGo may rate-limit (HTTP 202)
        pytest.skip(f"DuckDuckGo niet beschikbaar: {ex}")
