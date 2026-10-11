"""Template profile: everything a workflow needs to know about a house-style template."""
from __future__ import annotations

from pathlib import Path

from .compose import body_start
from .fields import find_placeholders
from .housestyle import HouseStyle, is_guidance
from .reader import DocModel, block_markdown


def analyze_template(path: str | Path) -> dict:
    model = DocModel.open(path)
    start = body_start(model)
    front_until = model.blocks[start].element if start < len(model.blocks) else None
    sections = []
    for node in model.outline():
        # Direct content of this section (until the first subsection).
        nxt = next((n for n in model.outline() if n.start > node.start), None)
        end = min(node.end, nxt.start) if nxt else node.end
        guidance, sample, tables = [], [], []
        for b in model.blocks[node.start + 1:end]:
            if is_guidance(b):
                guidance.append(b.text.strip().strip("[]<>{} "))
            elif b.kind == "table":
                tables.append({"columns": b.rows[0] if b.rows else [], "rows": len(b.rows)})
                sample.append(block_markdown(b) or "")
            else:
                md = block_markdown(b)
                if md:
                    sample.append(md)
        sample_md = "\n\n".join(sample)
        sections.append({
            "number": node.number, "title": node.title, "level": node.level,
            "guidance": guidance, "sample_markdown": sample_md[:4000], "sample_words": len(sample_md.split()),
            "tables": tables,
        })
    style = HouseStyle(model)
    front_md = "\n\n".join(m for m in (block_markdown(b) for b in model.blocks[:start]) if m)
    return {
        "name": Path(path).stem,
        "stats": model.stats(),
        "front_matter_markdown": front_md[:3000],
        "has_toc": any(b.kind == "toc" for b in model.blocks),
        "outline": sections,
        "fields": [p.as_dict() for p in find_placeholders(model.pkg, front_until)],
        "house_style": style.summary(),
    }


def outline_markdown(profile: dict, max_level: int = 3) -> str:
    lines = []
    for s in profile["outline"]:
        if s["level"] > max_level:
            continue
        g = f" — {'; '.join(s['guidance'])}" if s["guidance"] else ""
        lines.append(f"{'  ' * (s['level'] - 1)}- {s['number']} {s['title']}{g}")
    return "\n".join(lines)
