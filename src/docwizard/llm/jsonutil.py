"""Extract JSON from model output (fenced, loose or with surrounding prose)."""

from __future__ import annotations

import json
import re
from typing import Any

from docwizard.llm.thinking import split_think

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)


def _balanced(text: str, start: int) -> str | None:
    """Return the balanced {...}/[...] starting at `start`, respecting strings."""
    stack: list[str] = []
    in_str = esc = False
    pairs = {"{": "}", "[": "]"}
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
            if not stack:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Any:
    """Parse the first JSON value found in `text`. Raises ValueError if none."""
    text, _ = split_think(text)
    candidates = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(text)]
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            pass
    for i, ch in enumerate(text):
        if ch in "{[":
            frag = _balanced(text, i)
            if frag:
                try:
                    return json.loads(frag)
                except ValueError:
                    # Common local-model slip: trailing commas.
                    try:
                        return json.loads(re.sub(r",\s*([}\]])", r"\1", frag))
                    except ValueError:
                        continue
    raise ValueError("geen JSON gevonden in het antwoord")
