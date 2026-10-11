"""Split Markdown-ish text into chunks of ~300–800 tokens carrying their heading path."""

from __future__ import annotations

import re
from dataclasses import dataclass

from docwizard.llm.client import count_tokens

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
MAX_TOKENS = 800


@dataclass
class Chunk:
    heading_path: str
    text: str
    tokens: int


def _sections(text: str) -> list[tuple[list[str], str]]:
    stack: list[tuple[int, str]] = []
    out: list[tuple[list[str], str]] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            out.append(([t for _, t in stack], body))
        buf.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack = [(lv, t) for lv, t in stack if lv < level] + [(level, m.group(2).strip())]
        else:
            buf.append(line)
    flush()
    return out


def _pieces(body: str) -> list[str]:
    """Paragraphs; paragraphs longer than MAX_TOKENS are split on sentences (then hard)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if count_tokens(para) <= MAX_TOKENS:
            out.append(para)
            continue
        cur = ""
        for sent in SENTENCE.split(para):
            while count_tokens(sent) > MAX_TOKENS:  # no sentence boundaries: hard cut
                cut = int(MAX_TOKENS * 3.2)
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(sent[:cut])
                sent = sent[cut:]
            if cur and count_tokens(cur + " " + sent) > MAX_TOKENS:
                out.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}".strip()
        if cur:
            out.append(cur)
    return out


def chunk_text(text: str, title: str = "") -> list[Chunk]:
    """One or more chunks per section; paragraphs are packed up to MAX_TOKENS."""
    chunks: list[Chunk] = []
    for path, body in _sections(text):
        hp = " > ".join([title, *path] if title else path)
        cur = ""
        for piece in _pieces(body):
            if cur and count_tokens(cur + "\n\n" + piece) > MAX_TOKENS:
                chunks.append(Chunk(hp, cur, count_tokens(cur)))
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
        if cur:
            chunks.append(Chunk(hp, cur, count_tokens(cur)))
    return chunks
