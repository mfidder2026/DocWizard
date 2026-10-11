"""Split `<think>…</think>` blocks out of a streamed text."""

from __future__ import annotations

OPEN, CLOSE = "<think>", "</think>"


def _partial_suffix(buf: str, tag: str) -> int:
    """Length of the longest suffix of `buf` that is a proper prefix of `tag`."""
    for n in range(min(len(tag) - 1, len(buf)), 0, -1):
        if buf.endswith(tag[:n]):
            return n
    return 0


class ThinkFilter:
    """Incremental splitter. `feed()` returns (content, reasoning) deltas."""

    def __init__(self) -> None:
        self.in_think = False
        self.buf = ""
        self._content_started = False

    def feed(self, chunk: str) -> tuple[str, str]:
        self.buf += chunk
        content, reasoning = [], []
        while self.buf:
            tag = CLOSE if self.in_think else OPEN
            i = self.buf.find(tag)
            if i >= 0:
                (reasoning if self.in_think else content).append(self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                self.in_think = not self.in_think
                continue
            keep = _partial_suffix(self.buf, tag)
            out, self.buf = self.buf[: len(self.buf) - keep], self.buf[len(self.buf) - keep:]
            (reasoning if self.in_think else content).append(out)
            break
        return self._clean("".join(content)), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        rest, self.buf = self.buf, ""
        if self.in_think:
            return "", rest
        return self._clean(rest), ""

    def _clean(self, text: str) -> str:
        # Drop the whitespace models put between </think> and the answer.
        if not self._content_started:
            text = text.lstrip()
            if text:
                self._content_started = True
        return text


def split_think(text: str) -> tuple[str, str]:
    """Non-streaming split. Also handles a lone `</think>` (opening tag in the prompt)."""
    reasoning = ""
    if CLOSE in text and OPEN not in text.split(CLOSE, 1)[0]:
        reasoning, text = text.split(CLOSE, 1)
    f = ThinkFilter()
    c1, r1 = f.feed(text)
    c2, r2 = f.flush()
    return (c1 + c2).strip(), (reasoning + r1 + r2).strip()
