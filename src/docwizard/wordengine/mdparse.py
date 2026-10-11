"""Markdown -> flat list of block specs (what the builder turns into OOXML)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt


@dataclass
class Inline:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    link: str | None = None
    br: bool = False


@dataclass
class BlockSpec:
    kind: str                      # heading | para | list | table | code | quote | image | pagebreak | caption
    inlines: list[Inline] = field(default_factory=list)
    level: int = 0
    ordered: bool = False
    list_id: int = 0
    header: list[list[Inline]] = field(default_factory=list)
    rows: list[list[list[Inline]]] = field(default_factory=list)
    text: str = ""
    src: str = ""
    alt: str = ""

    def plain(self) -> str:
        return "".join(i.text for i in self.inlines)


_PAGEBREAK = re.compile(r"<!--\s*(pagebreak|page-break|nieuwe pagina)\s*-->|\\(newpage|pagebreak)", re.I)
_md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")


def parse_markdown(text: str) -> list[BlockSpec]:
    text = _PAGEBREAK.sub("\n\n<!-- pagebreak -->\n\n", text or "")
    tokens = _md.parse(text)
    out: list[BlockSpec] = []
    list_stack: list[tuple[bool, int]] = []   # (ordered, list_id)
    list_counter = 0
    in_quote = 0
    table: BlockSpec | None = None
    row: list[list[Inline]] | None = None
    in_head = False
    item_has_para = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        tt = t.type
        if tt == "heading_open":
            inl = _inlines(tokens[i + 1])
            out.append(BlockSpec("heading", inl, level=int(t.tag[1])))
            i += 3
            continue
        if tt in ("bullet_list_open", "ordered_list_open"):
            list_counter += 1
            list_stack.append((tt == "ordered_list_open", list_counter))
        elif tt in ("bullet_list_close", "ordered_list_close"):
            list_stack.pop()
        elif tt == "list_item_open":
            item_has_para = False
        elif tt == "blockquote_open":
            in_quote += 1
        elif tt == "blockquote_close":
            in_quote -= 1
        elif tt == "paragraph_open":
            inline = tokens[i + 1]
            inl = _inlines(inline)
            images = [c for c in (inline.children or []) if c.type == "image"]
            if images and not "".join(x.text for x in inl).strip():
                for im in images:
                    out.append(BlockSpec("image", src=im.attrGet("src") or "", alt=im.content or "",
                                         text=im.attrGet("title") or ""))
            elif list_stack:
                ordered, lid = list_stack[-1]
                if item_has_para and out and out[-1].kind == "list":
                    out[-1].inlines += [Inline("", br=True)] + inl
                else:
                    out.append(BlockSpec("list", inl, level=len(list_stack) - 1, ordered=ordered, list_id=lid))
                item_has_para = True
            elif in_quote:
                out.append(BlockSpec("quote", inl))
            else:
                plain = "".join(x.text for x in inl).strip()
                if re.match(r"^(figuur|figure|afbeelding|tabel|table)\s+\d+[:.]", plain, re.I) and out and \
                        out[-1].kind in ("image", "table"):
                    out.append(BlockSpec("caption", inl))
                else:
                    out.append(BlockSpec("para", inl))
                for im in images:
                    out.append(BlockSpec("image", src=im.attrGet("src") or "", alt=im.content or ""))
            i += 3
            continue
        elif tt in ("fence", "code_block"):
            out.append(BlockSpec("code", text=t.content.rstrip("\n")))
        elif tt == "html_block":
            if "pagebreak" in t.content:
                out.append(BlockSpec("pagebreak"))
        elif tt == "table_open":
            table = BlockSpec("table")
        elif tt == "thead_open":
            in_head = True
        elif tt == "thead_close":
            in_head = False
        elif tt == "tr_open":
            row = []
        elif tt == "tr_close" and table is not None and row is not None:
            if in_head and not table.header:
                table.header = row
            else:
                table.rows.append(row)
            row = None
        elif tt in ("th_open", "td_open"):
            row.append(_inlines(tokens[i + 1]))
            i += 3
            continue
        elif tt == "table_close" and table is not None:
            out.append(table)
            table = None
        i += 1
    return out


def _inlines(tok) -> list[Inline]:
    out: list[Inline] = []
    bold = italic = strike = False
    link = None
    for c in tok.children or []:
        ct = c.type
        if ct == "text":
            out.append(Inline(c.content, bold, italic, False, strike, link))
        elif ct == "strong_open":
            bold = True
        elif ct == "strong_close":
            bold = False
        elif ct == "em_open":
            italic = True
        elif ct == "em_close":
            italic = False
        elif ct == "s_open":
            strike = True
        elif ct == "s_close":
            strike = False
        elif ct == "code_inline":
            out.append(Inline(c.content, bold, italic, True, strike, link))
        elif ct == "link_open":
            link = c.attrGet("href")
        elif ct == "link_close":
            link = None
        elif ct == "softbreak":
            out.append(Inline(" ", bold, italic, False, strike, link))
        elif ct == "hardbreak":
            out.append(Inline("", br=True))
        elif ct == "html_inline":
            if c.content.lower().startswith("<br"):
                out.append(Inline("", br=True))
    # Table cells exported by our reader use <br> for line breaks.
    return out
