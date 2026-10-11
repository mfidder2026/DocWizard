"""Turn block specs (from Markdown) into OOXML elements in the house style."""
from __future__ import annotations

import copy
import io
import re
from typing import Callable

from lxml import etree

from .housestyle import HouseStyle, ParaFormat, text_width_twips
from .mdparse import BlockSpec, Inline, parse_markdown
from .ns import NS, REL_HYPERLINK, REL_IMAGE, REL_NUMBERING, CT_NUMBERING, qn, wval
from .order import put, put_new, get_or_add
from .reader import DocModel

# src -> (bytes, extension) ; used for images referenced from Markdown.
ImageResolver = Callable[[str], "tuple[bytes, str] | None"]

_LEADING_NUMBER = re.compile(r"^\s*(?:(?:hoofdstuk|chapter|bijlage)\s+)?\d+(?:\.\d+)*\.?\s+", re.I)
_IMG_CT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
           "bmp": "image/bmp", "svg": "image/svg+xml", "emf": "image/x-emf", "wmf": "image/x-wmf"}
EMU_PER_TWIP = 635


class Builder:
    def __init__(self, model: DocModel, image_resolver: ImageResolver | None = None):
        self.model = model
        self.pkg = model.pkg
        self.style = HouseStyle(model)
        self.images = image_resolver
        self.numbering = NumberingManager(model)
        self._bookmark_id = _max_attr(self.pkg.document, "//w:bookmarkStart/@w:id") + 1
        self._docpr_id = _max_attr(self.pkg.document, "//wp:docPr/@id") + 1
        self._list_nums: dict[int, str] = {}
        self._figure_no = 0
        self.width_twips = text_width_twips(model)
        self.headings: list[tuple[etree._Element, str, int]] = []  # (p, bookmark, level)

    # ---- public -------------------------------------------------------------
    def build_markdown(self, md: str) -> list[etree._Element]:
        return self.build(parse_markdown(md))

    def build(self, specs: list[BlockSpec]) -> list[etree._Element]:
        out: list[etree._Element] = []
        for s in specs:
            fn = getattr(self, f"_b_{s.kind}", None)
            if fn is None:
                continue
            res = fn(s)
            if res is None:
                continue
            out.extend(res if isinstance(res, list) else [res])
        return out

    # ---- blocks ---------------------------------------------------------------
    def _b_heading(self, s: BlockSpec):
        fmt = self.style.heading(s.level)
        inl = s.inlines
        if self.style.headings_numbered and inl:
            inl = [Inline(_LEADING_NUMBER.sub("", inl[0].text, count=1), inl[0].bold, inl[0].italic,
                          inl[0].code, inl[0].strike, inl[0].link)] + inl[1:]
        p = self._para(fmt, inl, plain_runs=True)
        name = f"_TocDW{self._bookmark_id:05d}"
        bs = etree.Element(qn("w:bookmarkStart"))
        bs.set(qn("w:id"), str(self._bookmark_id))
        bs.set(qn("w:name"), name)
        be = etree.Element(qn("w:bookmarkEnd"))
        be.set(qn("w:id"), str(self._bookmark_id))
        self._bookmark_id += 1
        ppr = p.find(qn("w:pPr"))
        p.insert(p.index(ppr) + 1 if ppr is not None else 0, bs)
        p.append(be)
        self.headings.append((p, name, s.level))
        return p

    def _b_para(self, s: BlockSpec):
        return self._para(self.style.body, s.inlines)

    def _b_list(self, s: BlockSpec):
        fmt = self.style.number if s.ordered else self.style.bullet
        if fmt is None:
            fmt = ParaFormat(self.model.styles.find_role("list_paragraph") or self.style.body.style_id,
                             None, self.style.body.rpr)
        p = self._para(fmt, s.inlines)
        ppr = p.find(qn("w:pPr"))
        num_id = self._list_num(s, fmt)
        if num_id:
            numpr = etree.Element(qn("w:numPr"))
            put_new(numpr, "w:ilvl", {"w:val": str(min(s.level, 8))})
            n = etree.SubElement(numpr, qn("w:numId"))
            n.set(qn("w:val"), num_id)
            put(ppr, numpr)
            if s.level and fmt.ppr is not None and fmt.ppr.find(qn("w:ind")) is not None:
                ppr.remove(ppr.find(qn("w:ind")))  # let the numbering level drive indentation
        return p

    def _b_quote(self, s: BlockSpec):
        if self.style.quote:
            return self._para(self.style.quote, s.inlines)
        p = self._para(self.style.body, [Inline(i.text, i.bold, True, i.code, i.strike, i.link, i.br)
                                          for i in s.inlines])
        put_new(p.find(qn("w:pPr")), "w:ind", {"w:left": "567"})
        return p

    def _b_code(self, s: BlockSpec):
        out = []
        fmt = self.style.code or ParaFormat(self.style.body.style_id)
        for line in s.text.split("\n") or [""]:
            p = self._para(fmt, [Inline(line, code=True)])
            ppr = p.find(qn("w:pPr"))
            put_new(ppr, "w:spacing", {"w:before": "0", "w:after": "0"})
            put_new(ppr, "w:shd", {"w:val": "clear", "w:color": "auto", "w:fill": "F2F2F2"})
            out.append(p)
        return out

    def _b_caption(self, s: BlockSpec):
        fmt = self.style.caption or ParaFormat(self._ensure_caption_style())
        return self._para(fmt, s.inlines)

    def _b_pagebreak(self, s: BlockSpec):
        p = etree.Element(qn("w:p"))
        r = etree.SubElement(p, qn("w:r"))
        br = etree.SubElement(r, qn("w:br"))
        br.set(qn("w:type"), "page")
        return p

    def _b_image(self, s: BlockSpec):
        got = self.images(s.src) if self.images else None
        if not got:
            return self._para(self.style.body, [Inline(f"[Afbeelding niet gevonden: {s.alt or s.src}]", italic=True)])
        data, ext = got
        p = self._para(self.style.body, [])
        ppr = p.find(qn("w:pPr"))
        put_new(ppr, "w:keepNext")
        put_new(ppr, "w:jc", {"w:val": "center"})
        p.append(self._drawing_run(data, ext, s.alt))
        out = [p]
        if s.alt:
            self._figure_no += 1
            out.append(self._caption_para("Figuur", s.alt))
        return out

    def _b_table(self, s: BlockSpec):
        return TableBuilder(self).build(s)

    # ---- helpers -------------------------------------------------------------
    def _para(self, fmt: ParaFormat, inlines: list[Inline], plain_runs: bool = False) -> etree._Element:
        p = etree.Element(qn("w:p"))
        p.append(fmt.new_ppr())
        for inl in inlines:
            self._append_inline(p, inl, fmt.new_rpr(), plain_runs)
        return p

    def _append_inline(self, parent, inl: Inline, base_rpr, plain=False):
        if inl.br:
            r = etree.SubElement(parent, qn("w:r"))
            etree.SubElement(r, qn("w:br"))
            return
        if not inl.text:
            return
        r = make_run(inl.text, base_rpr, bold=inl.bold and not plain, italic=inl.italic and not plain,
                     code=inl.code, strike=inl.strike)
        if inl.link and not plain:
            h = etree.SubElement(parent, qn("w:hyperlink"))
            if inl.link.startswith("#"):
                h.set(qn("w:anchor"), inl.link[1:])
            else:
                h.set(qn("r:id"), self.pkg.add_rel(self.pkg.main_part, REL_HYPERLINK, inl.link, external=True))
            h.set(qn("w:history"), "1")
            rpr = r.find(qn("w:rPr"))
            if rpr is None:
                rpr = etree.Element(qn("w:rPr"))
                r.insert(0, rpr)
            hs = self.model.styles.find_role("hyperlink")
            if hs:
                put_new(rpr, "w:rStyle", {"w:val": hs})
            else:
                put_new(rpr, "w:color", {"w:val": "0563C1"})
                put_new(rpr, "w:u", {"w:val": "single"})
            h.append(r)
        else:
            parent.append(r)

    def _caption_para(self, label: str, text: str) -> etree._Element:
        fmt = self.style.caption or ParaFormat(self._ensure_caption_style())
        p = self._para(fmt, [Inline(f"{label} ")])
        fld = etree.SubElement(p, qn("w:fldSimple"))
        fld.set(qn("w:instr"), f" SEQ {label} \\* ARABIC ")
        r = etree.SubElement(fld, qn("w:r"))
        t = etree.SubElement(r, qn("w:t"))
        t.text = str(self._figure_no)
        self._append_inline(p, Inline(f": {text}"), fmt.new_rpr())
        return p

    def _ensure_caption_style(self) -> str:
        rpr = etree.Element(qn("w:rPr"))
        put_new(rpr, "w:i")
        put_new(rpr, "w:sz", {"w:val": "18"})
        return self.model.styles.ensure_style("Caption", "caption", self.style.body.style_id, rpr=rpr)

    def _drawing_run(self, data: bytes, ext: str, alt: str) -> etree._Element:
        ext = ext.lower().lstrip(".")
        name = self.pkg.unique_media_name(ext)
        self.pkg.set_raw(name, data)
        self.pkg.ensure_default_ct(ext, _IMG_CT.get(ext, "application/octet-stream"))
        rid = self.pkg.add_rel(self.pkg.main_part, REL_IMAGE, name.split("word/", 1)[1])
        cx, cy = _image_size_emu(data, self.width_twips * EMU_PER_TWIP)
        did = self._docpr_id
        self._docpr_id += 1
        alt_x = _xml_attr(alt or f"Afbeelding {did}")
        xml = f"""<w:r xmlns:w="{NS['w']}" xmlns:wp="{NS['wp']}" xmlns:a="{NS['a']}" xmlns:pic="{NS['pic']}" xmlns:r="{NS['r']}">
<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>
<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{did}" name="Afbeelding {did}" descr="{alt_x}"/>
<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
<pic:nvPicPr><pic:cNvPr id="{did}" name="{name.rsplit('/', 1)[1]}"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"""
        return etree.fromstring(re.sub(r">\s+<", "><", xml))

    def _list_num(self, s: BlockSpec, fmt: ParaFormat) -> str | None:
        """numId for this list item. Ordered lists restart per Markdown list."""
        styles = self.model.styles
        base = None
        if fmt.ppr is not None and fmt.ppr.find(qn("w:numPr")) is not None:
            base = wval(fmt.ppr.find(f"{qn('w:numPr')}/{qn('w:numId')}"))
        elif styles.is_numbered(fmt.style_id):
            for st in styles.chain(fmt.style_id):
                n = st.element.find(f"{qn('w:pPr')}/{qn('w:numPr')}/{qn('w:numId')}")
                if n is not None:
                    base = wval(n)
                    break
        if s.list_id in self._list_nums:
            return self._list_nums[s.list_id]
        if base and base != "0":
            num = self.numbering.restart(base) if s.ordered else base
        else:
            num = self.numbering.new_list(s.ordered)
        self._list_nums[s.list_id] = num
        return num


class TableBuilder:
    """Clone the template's table look (borders, header shading, fonts) for new tables."""

    def __init__(self, b: Builder):
        self.b = b
        self.ex = b.style.table_exemplar

    def build(self, s: BlockSpec) -> list[etree._Element]:
        rows = ([s.header] if s.header else []) + s.rows
        ncols = max((len(r) for r in rows), default=0)
        if not ncols:
            return []
        tbl = etree.Element(qn("w:tbl"))
        ex_rows = self.ex.findall(qn("w:tr")) if self.ex is not None else []
        tblpr = copy.deepcopy(self.ex.find(qn("w:tblPr"))) if self.ex is not None else None
        if tblpr is None:
            tblpr = etree.Element(qn("w:tblPr"))
            ts = self.b.model.styles.find_role("table_grid") or self.b.model.styles.default_table
            if ts:
                put_new(tblpr, "w:tblStyle", {"w:val": ts})
        put_new(tblpr, "w:tblW", {"w:w": "5000", "w:type": "pct"})
        tbl.append(tblpr)

        total = self.b.width_twips
        if self.ex is not None:
            g = [int(c.get(qn("w:w"), "0") or 0) for c in self.ex.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")]
            if sum(g) > 1000:
                total = min(sum(g), total)
        widths = _col_widths(rows, ncols, total)
        grid = etree.SubElement(tbl, qn("w:tblGrid"))
        for w in widths:
            etree.SubElement(grid, qn("w:gridCol")).set(qn("w:w"), str(w))

        header_ex = ex_rows[0] if ex_rows else None
        body_ex = ex_rows[1:] if len(ex_rows) > 1 else ([] if s.header else ex_rows)
        for ri, row in enumerate(rows):
            is_header = bool(s.header) and ri == 0
            if is_header:
                rex = header_ex
            elif body_ex:
                rex = body_ex[(ri - (1 if s.header else 0)) % len(body_ex)]
            else:
                rex = None
            tbl.append(self._row(row, ncols, widths, rex, is_header))
        # Keep a paragraph after the table, as Word does; otherwise adjacent tables merge.
        spacer = self.b._para(self.b.style.body, [])
        return [tbl, spacer]

    def _row(self, cells, ncols, widths, rex, is_header):
        tr = etree.Element(qn("w:tr"))
        ex_cells = rex.findall(qn("w:tc")) if rex is not None else []
        if rex is not None and rex.find(qn("w:trPr")) is not None:
            trpr = copy.deepcopy(rex.find(qn("w:trPr")))
            for t in ("w:trHeight", "w:gridBefore", "w:gridAfter", "w:wBefore", "w:wAfter"):
                for e in trpr.findall(qn(t)):
                    trpr.remove(e)
            tr.append(trpr)
        elif is_header:
            trpr = etree.SubElement(tr, qn("w:trPr"))
            etree.SubElement(trpr, qn("w:tblHeader"))
        for ci in range(ncols):
            inl = cells[ci] if ci < len(cells) else []
            exc = ex_cells[min(ci, len(ex_cells) - 1)] if ex_cells else None
            tr.append(self._cell(inl, widths[ci], exc, is_header))
        return tr

    def _cell(self, inlines, width, exc, is_header):
        tc = etree.Element(qn("w:tc"))
        tcpr = copy.deepcopy(exc.find(qn("w:tcPr"))) if exc is not None and exc.find(qn("w:tcPr")) is not None \
            else etree.Element(qn("w:tcPr"))
        for t in ("w:gridSpan", "w:vMerge", "w:hMerge", "w:tcW"):
            for e in tcpr.findall(qn(t)):
                tcpr.remove(e)
        put_new(tcpr, "w:tcW", {"w:w": str(width), "w:type": "dxa"})
        tc.append(tcpr)
        ex_p = exc.find(qn("w:p")) if exc is not None else None
        ppr = copy.deepcopy(ex_p.find(qn("w:pPr"))) if ex_p is not None and ex_p.find(qn("w:pPr")) is not None \
            else None
        ex_r = None
        if ex_p is not None:
            for r in ex_p.iter(qn("w:r")):
                if r.find(qn("w:t")) is not None:
                    ex_r = r
                    break
        rpr = copy.deepcopy(ex_r.find(qn("w:rPr"))) if ex_r is not None and ex_r.find(qn("w:rPr")) is not None \
            else None
        if exc is None and is_header:
            rpr = etree.Element(qn("w:rPr"))
            put_new(rpr, "w:b")
        # Split cell content on line breaks into separate paragraphs.
        lines: list[list[Inline]] = [[]]
        for i in inlines:
            if i.br:
                lines.append([])
            else:
                lines[-1].append(i)
        for line in lines:
            p = etree.SubElement(tc, qn("w:p"))
            if ppr is not None:
                p.append(copy.deepcopy(ppr))
            for inl in line:
                self.b._append_inline(p, inl, copy.deepcopy(rpr) if rpr is not None else None)
        return tc


class NumberingManager:
    def __init__(self, model: DocModel):
        self.pkg = model.pkg
        self.part = self.pkg.related_part(REL_NUMBERING)

    def _root(self):
        if not self.part:
            self.part = "word/numbering.xml"
            root = etree.Element(qn("w:numbering"), nsmap={"w": NS["w"]})
            self.pkg.set_xml(self.part, root)
            self.pkg.add_rel(self.pkg.main_part, REL_NUMBERING, "numbering.xml")
            self.pkg.ensure_override_ct(self.part, CT_NUMBERING)
        return self.pkg.xml(self.part)

    def _next(self, root, xp):
        vals = [int(v) for v in root.xpath(xp, namespaces=NS) if str(v).isdigit()]
        return max(vals, default=0) + 1

    def restart(self, num_id: str) -> str:
        root = self._root()
        src = root.xpath(f"w:num[@w:numId='{num_id}']", namespaces=NS)
        if not src:
            return num_id
        aid = wval(src[0].find(qn("w:abstractNumId")))
        new_id = str(self._next(root, "w:num/@w:numId"))
        n = etree.SubElement(root, qn("w:num"))
        n.set(qn("w:numId"), new_id)
        etree.SubElement(n, qn("w:abstractNumId")).set(qn("w:val"), aid)
        o = etree.SubElement(n, qn("w:lvlOverride"))
        o.set(qn("w:ilvl"), "0")
        etree.SubElement(o, qn("w:startOverride")).set(qn("w:val"), "1")
        return new_id

    def new_list(self, ordered: bool) -> str:
        root = self._root()
        aid = str(self._next(root, "w:abstractNum/@w:abstractNumId"))
        a = etree.Element(qn("w:abstractNum"))
        a.set(qn("w:abstractNumId"), aid)
        etree.SubElement(a, qn("w:multiLevelType")).set(qn("w:val"), "hybridMultilevel")
        bullets = ["•", "◦", "▪"]
        fmts = ["decimal", "lowerLetter", "lowerRoman"]
        for i in range(9):
            lvl = etree.SubElement(a, qn("w:lvl"))
            lvl.set(qn("w:ilvl"), str(i))
            etree.SubElement(lvl, qn("w:start")).set(qn("w:val"), "1")
            etree.SubElement(lvl, qn("w:numFmt")).set(qn("w:val"), fmts[i % 3] if ordered else "bullet")
            etree.SubElement(lvl, qn("w:lvlText")).set(qn("w:val"), f"%{i + 1}." if ordered else bullets[i % 3])
            etree.SubElement(lvl, qn("w:lvlJc")).set(qn("w:val"), "left")
            ppr = etree.SubElement(lvl, qn("w:pPr"))
            ind = etree.SubElement(ppr, qn("w:ind"))
            ind.set(qn("w:left"), str(360 * (i + 1)))
            ind.set(qn("w:hanging"), "360")
        existing = root.findall(qn("w:abstractNum"))
        if existing:
            existing[-1].addnext(a)
        else:
            first_num = root.find(qn("w:num"))
            if first_num is not None:
                first_num.addprevious(a)
            else:
                root.append(a)
        nid = str(self._next(root, "w:num/@w:numId"))
        n = etree.SubElement(root, qn("w:num"))
        n.set(qn("w:numId"), nid)
        etree.SubElement(n, qn("w:abstractNumId")).set(qn("w:val"), aid)
        return nid


def make_run(text: str, base_rpr=None, bold=False, italic=False, code=False, strike=False) -> etree._Element:
    r = etree.Element(qn("w:r"))
    rpr = base_rpr if base_rpr is not None else etree.Element(qn("w:rPr"))
    if bold:
        put_new(rpr, "w:b")
    if italic:
        put_new(rpr, "w:i")
    if strike:
        put_new(rpr, "w:strike")
    if code:
        put_new(rpr, "w:rFonts", {"w:ascii": "Consolas", "w:hAnsi": "Consolas", "w:cs": "Consolas"})
    if len(rpr):
        r.append(rpr)
    parts = text.split("\t")
    for i, part in enumerate(parts):
        if i:
            etree.SubElement(r, qn("w:tab"))
        if part:
            t = etree.SubElement(r, qn("w:t"))
            t.text = part
            if part != part.strip():
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _col_widths(rows, ncols, total) -> list[int]:
    lens = [0] * ncols
    for r in rows:
        for i in range(ncols):
            txt = "".join(x.text for x in r[i]) if i < len(r) else ""
            lens[i] = max(lens[i], min(len(txt), 60))
    lens = [max(l, 10) for l in lens]
    s = sum(lens)
    ws = [int(total * l / s) for l in lens]
    ws[-1] += total - sum(ws)
    return ws


def _image_size_emu(data: bytes, max_w: int) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            dpi = im.info.get("dpi", (96, 96))[0] or 96
    except Exception:
        return max_w, int(max_w * 0.6)
    cx, cy = int(w / dpi * 914400), int(h / dpi * 914400)
    max_h = int(max_w * 1.1)
    scale = min(1.0, max_w / cx if cx else 1, max_h / cy if cy else 1)
    return int(cx * scale), int(cy * scale)


def _max_attr(root, xp) -> int:
    vals = [int(v) for v in root.xpath(xp, namespaces=NS) if str(v).lstrip("-").isdigit()]
    return max(vals, default=0)


def _xml_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
