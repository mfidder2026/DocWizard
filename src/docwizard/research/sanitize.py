"""Search-query firewall: strip sensitive terms and identifiers before a query leaves the machine."""

from __future__ import annotations

import re
from collections.abc import Iterable

_MONEY_WORDS = r"(?:euro|eur|mln|miljoen|mld|miljard|k€|dollar|usd)"
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}\s?(?:[A-Z0-9]{4}\s?){2,7}[A-Z0-9]{1,4}\b")),
    ("money", re.compile(
        rf"(?:[€$£]\s?\d[\d.,]*(?:\s?{_MONEY_WORDS})?|\b\d[\d.,]*\s?{_MONEY_WORDS}\b(?:\s?{_MONEY_WORDS}\b)?)",
        re.I)),
    ("phone", re.compile(r"(?<!\w)(?:\+|00)\d{2}[\s-]?(?:\(0\))?[\s-]?\d(?:[\s-]?\d){7,9}\b"
                         r"|\b0\d{1,3}[\s-]?\d(?:[\s-]?\d){5,7}\b")),
    ("bsn", re.compile(r"\b\d{9}\b")),
    ("number", re.compile(r"\b\d[\d.,]{5,}\d\b")),  # other long numbers (dossier-, order-, klantnummers)
]


def sanitize_query(q: str, sensitive_terms: Iterable[str] = ()) -> tuple[str, list[str]]:
    """Returns (clean_query, removed). Short numbers such as years or article numbers stay."""
    removed: list[str] = []
    out = q
    for _, pat in PATTERNS:
        for m in pat.findall(out):
            removed.append(m.strip())
        out = pat.sub(" ", out)
    for term in sorted({t.strip() for t in sensitive_terms if t and t.strip()}, key=len, reverse=True):
        pat = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.I)
        for m in pat.findall(out):
            removed.append(m)
        out = pat.sub(" ", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;:-")
    return out, removed
