"""Fields, placeholders and document properties.

Covers the ways house-style templates carry variable data:
- `{{veld}}`, `<<veld>>`, `«veld»` text placeholders (anywhere, incl. headers/footers)
- `[Veld]` bracket placeholders on the cover page
- content controls (w:sdt) identified by alias/tag
- custom document properties + DOCPROPERTY fields (e.g. "PSA Titel" in the header)
- core properties (title, author, ...) + TITLE/AUTHOR/SUBJECT fields
- cover lines like "Betreft<tab>:<tab>..." (label/value pairs)
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator

from lxml import etree

from .ns import NS, qn, wval
from .package import DocxPackage

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_BRACES = re.compile(r"\{\{\s*([^{}]{1,60}?)\s*\}\}|<<\s*([^<>]{1,60}?)\s*>>|«\s*([^«»]{1,60}?)\s*»")
_BRACKETS = re.compile(r"\[([^\[\]]{2,60})\]")
_LABEL_LINE = re.compile(r"^\s*([^\t:]{2,40}?)\s*\t*:\s*\t*(.*)$")
_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CORE_FIELDS = {"TITLE": "dc:title", "AUTHOR": "dc:creator", "SUBJECT": "dc:subject", "KEYWORDS": "cp:keywords",
                "LASTSAVEDBY": "cp:lastModifiedBy"}


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def text_parts(pkg: DocxPackage) -> list[str]:
    """Main document + headers + footers + foot/endnotes."""
    out = [pkg.main_part]
    for n in pkg.names():
        if re.match(r"word/(header|footer|footnotes|endnotes)\d*\.xml$", n):
            out.append(n)
    return out


# ---- run-spanning replace ------------------------------------------------------

def replace_in_paragraph(p: etree._Element, rx: re.Pattern, repl: Callable[[re.Match], str | None]) -> int:
    """Regex replace across all w:t of a paragraph; replacement takes the first run's formatting."""
    count = 0
    ts = list(p.iter(qn("w:t")))
    full = "".join(t.text or "" for t in ts)
    for m in reversed(list(rx.finditer(full))):
        new = repl(m)
        if new is None:
            continue
        count += 1
        _splice(ts, m.start(), m.end(), new)
    return count


def _splice(ts: list[etree._Element], start: int, end: int, new: str) -> None:
    """Replace [start, end) of the concatenated text of `ts` by `new` (placed in the first touched w:t)."""
    spans, pos = [], 0
    for t in ts:
        n = len(t.text or "")
        spans.append((t, pos, pos + n))
        pos += n
    first = next((s for s in spans if s[1] <= start < s[2]), None) or (spans[-1] if spans else None)
    if first is None:
        return
    for t, a, b in spans:
        txt = t.text or ""
        if t is first[0]:
            lo = start - a
            t.text = txt[:lo] + new + txt[max(min(end, b) - a, lo):]
        elif start <= a < end:
            t.text = txt[min(end, b) - a:]
        else:
            continue
        t.set(XML_SPACE, "preserve")


# ---- complex fields -------------------------------------------------------------

@dataclass
class FieldRef:
    instr: str
    result_ts: list[etree._Element] = field(default_factory=list)
    simple: etree._Element | None = None   # w:fldSimple

    @property
    def kind(self) -> str:
        return (self.instr.strip().split() or [""])[0].upper()

    @property
    def argument(self) -> str:
        m = re.match(r'\s*\S+\s+(?:"([^"]+)"|(\S+))', self.instr)
        return (m.group(1) or m.group(2)) if m else ""

    def set_result(self, value: str) -> None:
        if not self.result_ts:
            return
        self.result_ts[0].text = value
        self.result_ts[0].set(XML_SPACE, "preserve")
        for t in self.result_ts[1:]:
            t.text = ""


def iter_fields(root: etree._Element) -> Iterator[FieldRef]:
    """Yield simple and complex fields of a part in document order (nested fields included)."""
    stack: list[tuple[FieldRef, bool]] = []  # (field, in_result)
    for e in root.iter(qn("w:fldSimple"), qn("w:fldChar"), qn("w:instrText"), qn("w:t")):
        tag = etree.QName(e).localname
        if tag == "fldSimple":
            f = FieldRef(e.get(qn("w:instr"), ""), list(e.iter(qn("w:t"))), e)
            yield f
        elif tag == "fldChar":
            kind = e.get(qn("w:fldCharType"))
            if kind == "begin":
                stack.append((FieldRef(""), False))
            elif kind == "separate" and stack:
                stack[-1] = (stack[-1][0], True)
            elif kind == "end" and stack:
                f, _ = stack.pop()
                yield f
        elif tag == "instrText" and stack:
            stack[-1][0].instr += e.text or ""
        elif tag == "t" and stack and stack[-1][1]:
            # Skip text inside fldSimple handled above.
            if not any(etree.QName(a).localname == "fldSimple" for a in e.iterancestors()):
                stack[-1][0].result_ts.append(e)


# ---- properties -----------------------------------------------------------------

def custom_properties(pkg: DocxPackage) -> dict[str, str]:
    if not pkg.has("docProps/custom.xml"):
        return {}
    out = {}
    for p in pkg.xml("docProps/custom.xml"):
        name = p.get("name")
        val = "".join(p.itertext()).strip()
        if name and not name.startswith(("ContentTypeId", "MediaService", "_")):
            out[name] = val
    return out


def set_custom_property(pkg: DocxPackage, name: str, value: str) -> bool:
    if not pkg.has("docProps/custom.xml"):
        return False
    root = pkg.xml("docProps/custom.xml")
    for p in root:
        if p.get("name") == name:
            for c in p:
                c.text = value
            return True
    pid = max([int(p.get("pid", "1")) for p in root] + [1]) + 1
    p = etree.SubElement(root, f"{{{_CUSTOM_NS}}}property")
    p.set("fmtid", "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}")
    p.set("pid", str(pid))
    p.set("name", name)
    etree.SubElement(p, f"{{{_VT_NS}}}lpwstr").text = value
    return True


def core_properties(pkg: DocxPackage) -> dict[str, str]:
    if not pkg.has("docProps/core.xml"):
        return {}
    root = pkg.xml("docProps/core.xml")
    out = {}
    for tag in ("dc:title", "dc:subject", "dc:creator", "cp:keywords", "dc:description", "cp:lastModifiedBy"):
        e = root.find(qn(tag))
        if e is not None and e.text:
            out[tag.split(":")[1]] = e.text
    return out


def update_core_properties(pkg: DocxPackage, title: str | None = None, author: str | None = None,
                           subject: str | None = None) -> None:
    name = "docProps/core.xml"
    if not pkg.has(name):
        return
    root = pkg.xml(name)
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def setv(tag, value, w3cdtf=False):
        e = root.find(qn(tag))
        if e is None:
            e = etree.SubElement(root, qn(tag))
            if w3cdtf:
                e.set("{http://www.w3.org/2001/XMLSchema-instance}type", "dcterms:W3CDTF")
        e.text = value

    if title:
        setv("dc:title", title)
    if subject:
        setv("dc:subject", subject)
    if author:
        setv("cp:lastModifiedBy", author)
    setv("dcterms:modified", now, w3cdtf=True)


# ---- discovery & filling --------------------------------------------------------

@dataclass
class Placeholder:
    key: str
    label: str
    source: str        # braces | brackets | sdt | docproperty | coreproperty | label
    current: str = ""
    part: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def referenced_docproperties(pkg: DocxPackage) -> set[str]:
    out = set()
    for part in text_parts(pkg):
        for f in iter_fields(pkg.xml(part)):
            if f.kind == "DOCPROPERTY" and f.argument:
                out.add(f.argument.lower())
    return out


_NOISE_PROPS = re.compile(r"^(order|xd_|compliance|templateurl|triggerflow|mediaservice|contenttype|_|"
                          r"msip_|taxcatchall|lcf76f155ced4ddcb4097134ff3c332f)", re.I)


def update_filename_fields(pkg: DocxPackage, filename: str) -> None:
    """Refresh cached FILENAME results (headers often show the file name)."""
    stem = filename.rsplit("/", 1)[-1]
    for part in text_parts(pkg):
        for f in iter_fields(pkg.xml(part)):
            if f.kind == "FILENAME":
                f.set_result(stem if "\\p" in f.instr.lower() else stem.rsplit(".", 1)[0])


def find_placeholders(pkg: DocxPackage, front_until: etree._Element | None = None) -> list[Placeholder]:
    found: dict[str, Placeholder] = {}
    used_props = referenced_docproperties(pkg)

    def add(label, source, part, current=""):
        k = norm_key(label)
        if k and k not in found:
            found[k] = Placeholder(k, label.strip(), source, current.strip(), part)

    for name, val in custom_properties(pkg).items():
        if name.lower() in used_props or not _NOISE_PROPS.match(name):
            add(name, "docproperty", "docProps/custom.xml", val)
    for part in text_parts(pkg):
        root = pkg.xml(part)
        for sdt in root.iter(qn("w:sdt")):
            pr = sdt.find(qn("w:sdtPr"))
            if pr is None or pr.find(qn("w:docPartObj")) is not None:
                continue
            label = wval(pr.find(qn("w:alias"))) or wval(pr.find(qn("w:tag")))
            if label:
                content = sdt.find(qn("w:sdtContent"))
                add(label, "sdt", part, "".join(content.itertext()) if content is not None else "")
        for f in iter_fields(root):
            if f.kind in _CORE_FIELDS:
                add(f.kind.lower(), "coreproperty", part, "".join(t.text or "" for t in f.result_ts))
        for p in root.iter(qn("w:p")):
            txt = "".join(t.text or "" for t in p.iter(qn("w:t")))
            for m in _BRACES.finditer(txt):
                add(next(g for g in m.groups() if g), "braces", part)
    if front_until is not None:
        for child in pkg.body:
            if child is front_until:
                break
            for p in child.iter(qn("w:p")):
                if any(etree.QName(a).localname == "sdt" for a in p.iterancestors()) and \
                        etree.QName(child).localname != "sdt":
                    continue
                txt = "".join(t.text or "" for t in p.iter(qn("w:t")) if not _in_sdt(t, p))
                for m in _BRACKETS.finditer(txt):
                    add(m.group(1), "brackets", pkg.main_part)
                tabbed = "".join(_run_text_with_tabs(p))
                lm = _LABEL_LINE.match(tabbed)
                if lm and "\t" in tabbed and len(tabbed) < 200:
                    add(lm.group(1), "label", pkg.main_part, lm.group(2).replace("\t", " "))
    return list(found.values())


def fill_placeholders(pkg: DocxPackage, values: dict[str, str], front_until: etree._Element | None = None) -> int:
    """Fill every kind of placeholder whose normalized key appears in `values`. Returns #replacements."""
    vals = {norm_key(k): str(v) for k, v in values.items() if v not in (None, "")}
    if not vals:
        return 0
    n = 0
    # Custom properties + their DOCPROPERTY fields.
    props = custom_properties(pkg)
    prop_values: dict[str, str] = {}
    for name in props:
        if norm_key(name) in vals:
            set_custom_property(pkg, name, vals[norm_key(name)])
            prop_values[name.lower()] = vals[norm_key(name)]
            n += 1
    core_updates = {k: vals[k.lower()] for k in _CORE_FIELDS if k.lower() in vals}
    if "title" in core_updates or "TITLE" in core_updates:
        update_core_properties(pkg, title=core_updates.get("TITLE"))

    for part in text_parts(pkg):
        root = pkg.xml(part)
        for f in iter_fields(root):
            if f.kind == "DOCPROPERTY" and f.argument.lower() in prop_values:
                f.set_result(prop_values[f.argument.lower()])
            elif f.kind in core_updates:
                f.set_result(core_updates[f.kind])
        for sdt in list(root.iter(qn("w:sdt"))):
            pr = sdt.find(qn("w:sdtPr"))
            if pr is None:
                continue
            key = norm_key(wval(pr.find(qn("w:alias"))) or "") or norm_key(wval(pr.find(qn("w:tag"))) or "")
            if key in vals:
                _set_sdt_text(sdt, vals[key])
                n += 1

        def braces(m):
            return vals.get(norm_key(next(g for g in m.groups() if g)))

        for p in root.iter(qn("w:p")):
            n += replace_in_paragraph(p, _BRACES, braces)

    if front_until is not None:
        def brackets(m):
            return vals.get(norm_key(m.group(1)))

        for child in pkg.body:
            if child is front_until:
                break
            for p in child.iter(qn("w:p")):
                n += replace_in_paragraph(p, _BRACKETS, brackets)
                n += _fill_label_line(p, vals)
    return n


def _fill_label_line(p: etree._Element, vals: dict[str, str]) -> int:
    """'Betreft<tab>:<tab>oude waarde' → replace the value part, keep label, tabs and formatting."""
    ts = list(p.iter(qn("w:t")))
    full = "".join(t.text or "" for t in ts)
    # Tabs are separate w:tab elements, so w:t text looks like "Betreft" ":" "waarde".
    runs_text = "".join(_run_text_with_tabs(p))
    lm = _LABEL_LINE.match(runs_text)
    if not lm or "\t" not in runs_text:
        return 0
    key = norm_key(lm.group(1))
    if key not in vals:
        return 0
    old_value = lm.group(2)
    label_len = len(full) - len(old_value.replace("\t", ""))
    if old_value:
        _splice(ts, label_len, len(full), vals[key])
    else:
        # Empty value: append a run after the last run, cloning its formatting.
        runs = p.findall(f".//{qn('w:r')}")
        if not runs:
            return 0
        import copy
        r = copy.deepcopy(runs[-1])
        for c in list(r):
            if etree.QName(c).localname != "rPr":
                r.remove(c)
        t = etree.SubElement(r, qn("w:t"))
        t.text = vals[key]
        t.set(XML_SPACE, "preserve")
        runs[-1].addnext(r)
    return 1


def _in_sdt(t: etree._Element, stop: etree._Element) -> bool:
    for a in t.iterancestors():
        if a is stop:
            return False
        if etree.QName(a).localname == "sdt":
            return True
    return False


def _run_text_with_tabs(p: etree._Element) -> Iterator[str]:
    for e in p.iter(qn("w:t"), qn("w:tab")):
        if etree.QName(e).localname == "tab":
            if etree.QName(e.getparent()).localname == "r":
                yield "\t"
        else:
            yield e.text or ""


def _set_sdt_text(sdt: etree._Element, value: str) -> None:
    pr = sdt.find(qn("w:sdtPr"))
    for e in pr.findall(qn("w:showingPlcHdr")):
        pr.remove(e)
    content = sdt.find(qn("w:sdtContent"))
    if content is None:
        return
    ts = list(content.iter(qn("w:t")))
    if not ts:
        return
    ts[0].text = value
    ts[0].set(XML_SPACE, "preserve")
    for t in ts[1:]:
        t.text = ""
    rpr = ts[0].getparent().find(qn("w:rPr"))
    if rpr is not None:
        for e in rpr.findall(qn("w:rStyle")):
            v = (wval(e) or "").lower()
            if "placeholder" in v or "tijdelijke" in v:
                rpr.remove(e)
        for e in rpr.findall(qn("w:color")):
            rpr.remove(e)


def set_update_fields_on_open(pkg: DocxPackage) -> None:
    """Ask Word to refresh TOC page numbers and other fields when the document opens."""
    from .ns import REL_SETTINGS
    from .order import put_new
    part = pkg.related_part(REL_SETTINGS)
    if not part or not pkg.has(part):
        return
    put_new(pkg.xml(part), "w:updateFields", {"w:val": "true"})


__all__ = ["Placeholder", "find_placeholders", "fill_placeholders", "replace_in_paragraph", "iter_fields",
           "custom_properties", "set_custom_property", "core_properties", "update_core_properties",
           "set_update_fields_on_open", "norm_key", "text_parts", "NS"]
