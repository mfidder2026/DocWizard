"""Lossless .docx package access.

Every part is kept as raw bytes; only parts we touch are parsed and
re-serialized. Everything else (theme, fonts, headers, footers, custom XML,
glossary, embedded objects) is written back byte-for-byte, which is what keeps
the house style intact.
"""
from __future__ import annotations

import copy
import io
import posixpath
import zipfile
from pathlib import Path

from lxml import etree

from .ns import NS, REL_OFFICE_DOC, qn

_PARSER = etree.XMLParser(remove_blank_text=False, huge_tree=True)


class DocxPackage:
    def __init__(self, data: bytes | None = None, path: str | Path | None = None):
        if path is not None:
            data = Path(path).read_bytes()
        if data is None:
            raise ValueError("data or path required")
        self._order: list[str] = []
        self._raw: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                self._order.append(info.filename)
                self._raw[info.filename] = zf.read(info.filename)
        self._xml: dict[str, etree._Element] = {}
        self.main_part = self._find_main_part()

    # ---- generic part access -------------------------------------------
    @classmethod
    def open(cls, path: str | Path) -> "DocxPackage":
        return cls(path=path)

    def copy(self) -> "DocxPackage":
        clone = object.__new__(DocxPackage)
        clone._order = list(self._order)
        clone._raw = dict(self._raw)
        clone._xml = {k: copy.deepcopy(v) for k, v in self._xml.items()}
        clone.main_part = self.main_part
        return clone

    def has(self, name: str) -> bool:
        return name in self._raw or name in self._xml

    def names(self) -> list[str]:
        return list(self._order)

    def raw(self, name: str) -> bytes:
        if name in self._xml:
            return self._serialize(name)
        return self._raw[name]

    def set_raw(self, name: str, data: bytes) -> None:
        self._xml.pop(name, None)
        if name not in self._raw:
            self._order.append(name)
        self._raw[name] = data

    def xml(self, name: str) -> etree._Element:
        if name not in self._xml:
            self._xml[name] = etree.fromstring(self._raw[name], _PARSER)
        return self._xml[name]

    def set_xml(self, name: str, root: etree._Element) -> None:
        if name not in self._raw:
            self._order.append(name)
            self._raw[name] = b""
        self._xml[name] = root

    def remove(self, name: str) -> None:
        self._raw.pop(name, None)
        self._xml.pop(name, None)
        if name in self._order:
            self._order.remove(name)

    def _serialize(self, name: str) -> bytes:
        return etree.tostring(self._xml[name], xml_declaration=True, encoding="UTF-8", standalone=True)

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # [Content_Types].xml first, as Word expects.
            order = sorted(self._order, key=lambda n: n != "[Content_Types].xml")
            for name in order:
                zf.writestr(name, self.raw(name))
        return buf.getvalue()

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        return path

    # ---- relationships ---------------------------------------------------
    @staticmethod
    def rels_name(part: str) -> str:
        d, f = posixpath.split(part)
        return posixpath.join(d, "_rels", f + ".rels")

    def rels(self, part: str) -> etree._Element:
        name = self.rels_name(part)
        if not self.has(name):
            root = etree.Element(qn("pr:Relationships"), nsmap={None: NS["pr"]})
            self.set_xml(name, root)
        return self.xml(name)

    def rel_map(self, part: str) -> dict[str, dict[str, str]]:
        out = {}
        for r in self.rels(part):
            out[r.get("Id")] = {"type": r.get("Type"), "target": r.get("Target"), "mode": r.get("TargetMode", "")}
        return out

    def resolve_target(self, part: str, target: str) -> str:
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))

    def part_for_rel(self, part: str, rid: str) -> str | None:
        info = self.rel_map(part).get(rid)
        if not info or info["mode"] == "External":
            return None
        return self.resolve_target(part, info["target"])

    def find_rel(self, part: str, rel_type: str) -> str | None:
        for rid, info in self.rel_map(part).items():
            if info["type"] == rel_type:
                return rid
        return None

    def add_rel(self, part: str, rel_type: str, target: str, external: bool = False) -> str:
        root = self.rels(part)
        existing = {r.get("Id") for r in root}
        for r in root:
            if r.get("Type") == rel_type and r.get("Target") == target:
                return r.get("Id")
        n = 1
        while f"rId{n}" in existing:
            n += 1
        rid = f"rId{n}"
        rel = etree.SubElement(root, qn("pr:Relationship"))
        rel.set("Id", rid)
        rel.set("Type", rel_type)
        rel.set("Target", target)
        if external:
            rel.set("TargetMode", "External")
        return rid

    # ---- content types ---------------------------------------------------
    def ensure_default_ct(self, ext: str, content_type: str) -> None:
        ct = self.xml("[Content_Types].xml")
        for d in ct.findall(qn("ct:Default")):
            if d.get("Extension", "").lower() == ext.lower():
                return
        e = etree.SubElement(ct, qn("ct:Default"))
        e.set("Extension", ext)
        e.set("ContentType", content_type)
        # Defaults must precede Overrides for strict consumers.
        ct.remove(e)
        ct.insert(0, e)

    def ensure_override_ct(self, part: str, content_type: str) -> None:
        ct = self.xml("[Content_Types].xml")
        pn = "/" + part
        for o in ct.findall(qn("ct:Override")):
            if o.get("PartName") == pn:
                return
        e = etree.SubElement(ct, qn("ct:Override"))
        e.set("PartName", pn)
        e.set("ContentType", content_type)

    def unique_media_name(self, ext: str) -> str:
        n = 1
        while self.has(f"word/media/dw_image{n}.{ext}"):
            n += 1
        return f"word/media/dw_image{n}.{ext}"

    # ---- main document ---------------------------------------------------
    def _find_main_part(self) -> str:
        root = etree.fromstring(self._raw["_rels/.rels"])
        for r in root:
            if r.get("Type") == REL_OFFICE_DOC:
                return r.get("Target").lstrip("/")
        return "word/document.xml"

    @property
    def document(self) -> etree._Element:
        return self.xml(self.main_part)

    @property
    def body(self) -> etree._Element:
        return self.document.find(qn("w:body"))

    def related_part(self, rel_type: str) -> str | None:
        rid = self.find_rel(self.main_part, rel_type)
        return self.part_for_rel(self.main_part, rid) if rid else None
