"""Approximate HTML preview of a .docx for the document panel (not pixel-exact; Word stays the reference)."""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

from .ns import qn, wval
from .reader import Block, DocModel, Span

_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
         "svg": "image/svg+xml"}


def _theme_fonts_and_colors(model: DocModel) -> dict:
    """Pick heading colour/font from the heading 1 style so the preview resembles the house style."""
    out = {"heading_color": "#1f3864", "font": "Calibri, Arial, sans-serif"}
    from .housestyle import HouseStyle
    h1 = HouseStyle(model).heading(1)
    for st in model.styles.chain(h1.style_id):
        rpr = st.element.find(qn("w:rPr"))
        if rpr is None:
            continue
        c = rpr.find(qn("w:color"))
        if c is not None and wval(c) not in (None, "auto") and len(wval(c)) == 6:
            out["heading_color"] = "#" + wval(c)
            break
    return out


def _spans_html(spans: list[Span]) -> str:
    out = []
    for s in spans:
        t = html.escape(s.text).replace("\n", "<br>").replace("\t", "&emsp;")
        if s.bold:
            t = f"<strong>{t}</strong>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.code:
            t = f"<code>{t}</code>"
        if s.link and not s.link.startswith("#"):
            t = f'<a href="{html.escape(s.link)}" target="_blank" rel="noopener">{t}</a>'
        out.append(t)
    return "".join(out)


def _image_html(model: DocModel, part: str) -> str:
    ext = part.rsplit(".", 1)[-1].lower()
    data = model.pkg.raw(part)
    if ext in ("emf", "wmf", "tif", "tiff"):
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "PNG")
                data, ext = buf.getvalue(), "png"
        except Exception:
            return f'<div class="dw-img-missing">[afbeelding: {html.escape(Path(part).name)}]</div>'
    if len(data) > 4_000_000:
        return f'<div class="dw-img-missing">[grote afbeelding: {html.escape(Path(part).name)}]</div>'
    b64 = base64.b64encode(data).decode()
    return f'<img src="data:{_MIME.get(ext, "image/png")};base64,{b64}" alt="">'


def to_html(path_or_model: str | Path | DocModel, *, max_blocks: int = 4000) -> str:
    model = path_or_model if isinstance(path_or_model, DocModel) else DocModel.open(path_or_model)
    theme = _theme_fonts_and_colors(model)
    numbers = {id(n.block): n.number for n in model.outline()}
    parts = [f'<div class="dw-doc" style="font-family:{theme["font"]}">',
             f'<style>.dw-doc h1,.dw-doc h2,.dw-doc h3,.dw-doc h4{{color:{theme["heading_color"]}}}</style>']
    list_stack: list[str] = []

    def close_lists(level=-1):
        while len(list_stack) > level + 1:
            parts.append(f"</{list_stack.pop()}>")

    for b in model.blocks[:max_blocks]:
        if b.kind != "list":
            close_lists()
        if b.kind == "heading":
            lvl = min(b.level, 6)
            num = numbers.get(id(b), "")
            parts.append(f'<h{lvl}><span class="dw-num">{num}</span> {_spans_html(b.spans)}</h{lvl}>')
        elif b.kind == "paragraph":
            inner = _spans_html(b.spans)
            imgs = "".join(_image_html(model, p) for p in b.images)
            if inner.strip() or imgs:
                parts.append(f"<p>{inner}{imgs}</p>")
        elif b.kind == "list":
            tag = "ol" if b.ordered else "ul"
            while len(list_stack) < b.level + 1:
                parts.append(f"<{tag}>")
                list_stack.append(tag)
            close_lists(b.level)
            parts.append(f"<li>{_spans_html(b.spans)}</li>")
        elif b.kind == "table":
            rows = b.rows
            if not rows:
                continue
            h = "".join(f"<th>{html.escape(c).replace(chr(10), '<br>')}</th>" for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{html.escape(c).replace(chr(10), '<br>')}</td>" for c in r) + "</tr>"
                           for r in rows[1:])
            parts.append(f'<table class="dw-table"><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>')
        elif b.kind == "image":
            parts.append('<p class="dw-figure">' + "".join(_image_html(model, p) for p in b.images) + "</p>")
        elif b.kind == "toc":
            if not parts[-1].startswith('<div class="dw-toc"'):
                items = "".join(f'<div class="dw-toc-l{n.level}">{n.number} {html.escape(n.title)}</div>'
                                for n in model.outline() if n.level <= 3)
                parts.append(f'<div class="dw-toc"><div class="dw-toc-title">Inhoudsopgave</div>{items}</div>')
        elif b.kind == "pagebreak" or b.has_section_break:
            parts.append('<hr class="dw-pagebreak">')
        elif b.kind == "other" and b.text.strip():
            parts.append(f"<p>{_spans_html(b.spans)}</p>")
    close_lists()
    parts.append("</div>")
    return "\n".join(parts)
