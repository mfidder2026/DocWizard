"""High-level document operations: new from template, insert chapter, replace section, restyle."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .builder import Builder, ImageResolver
from .fields import fill_placeholders, update_core_properties, update_filename_fields
from .housestyle import is_guidance
from .mdparse import parse_markdown
from .ns import qn
from .package import DocxPackage
from .reader import DocModel
from .toc import rebuild_toc


@dataclass
class ComposeReport:
    path: str = ""
    inserted_blocks: int = 0
    fields_filled: int = 0
    toc_entries: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def body_start(model: DocModel) -> int:
    """Index (in model.blocks) where the body starts: the first real heading after the cover/TOC."""
    for i, b in enumerate(model.blocks):
        if b.kind == "heading":
            return i
    # No headings: start after the last TOC / section-break block of the front matter, else at 0.
    last = -1
    for i, b in enumerate(model.blocks):
        if b.kind == "toc" or b.has_section_break:
            last = i
    return last + 1


def _final_sectpr(pkg: DocxPackage) -> etree._Element | None:
    return pkg.body.find(qn("w:sectPr"))


def _insert_before(pkg: DocxPackage, ref: etree._Element | None, elements: list[etree._Element]) -> None:
    body = pkg.body
    if ref is None:
        ref = _final_sectpr(pkg)
    if ref is None:
        body.extend(elements)
        return
    for e in elements:
        ref.addprevious(e)


def _shift_headings(md: str, target_top_level: int) -> str:
    """Shift Markdown heading levels so the shallowest heading becomes `target_top_level`."""
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,9})\s", md, re.M)]
    if not levels:
        return md
    delta = target_top_level - min(levels)
    if not delta:
        return md
    return re.sub(r"^(#{1,9})(\s)", lambda m: "#" * max(1, min(9, len(m.group(1)) + delta)) + m.group(2), md,
                  flags=re.M)


def _finish(pkg: DocxPackage, report: ComposeReport, title: str | None, author: str | None,
            out: str | Path | None = None) -> DocModel:
    model = DocModel(pkg)
    if out is not None:
        update_filename_fields(pkg, Path(out).name)
    report.toc_entries = rebuild_toc(model)
    update_core_properties(pkg, title=title, author=author)
    return model


def new_from_template(template: str | Path | DocxPackage, body_markdown: str, out: str | Path, *,
                      fields: dict[str, str] | None = None, title: str | None = None, author: str | None = None,
                      images: ImageResolver | None = None) -> ComposeReport:
    """Keep cover, TOC, sections, headers/footers of the template; replace the body with Markdown content."""
    pkg = template.copy() if isinstance(template, DocxPackage) else DocxPackage.open(template)
    model = DocModel(pkg)
    report = ComposeReport()
    builder = Builder(model, images)          # learns the house style before we remove anything
    start = body_start(model)
    front_end = model.blocks[start].element if start < len(model.blocks) else None

    removed_sections = 0
    for b in model.blocks[start:]:
        if b.kind == "section":
            continue
        if b.has_section_break:
            removed_sections += 1
        b.element.getparent().remove(b.element)
    if removed_sections:
        report.warnings.append(f"{removed_sections} sectie-einde(n) in de sjabloonbody zijn vervallen; "
                               "de laatste sectie-opmaak is behouden.")
    elements = builder.build_markdown(body_markdown)
    _insert_before(pkg, None, elements)
    report.inserted_blocks = len(elements)

    # Template guidance on the cover ("[Klantnaam]") disappears only when filled; other placeholders stay visible.
    first_new = elements[0] if elements else None
    if fields:
        report.fields_filled = fill_placeholders(pkg, fields, front_until=first_new)
    _finish(pkg, report, title, author, out)
    report.path = str(pkg.save(out))
    return report


def insert_markdown(doc: str | Path | DocxPackage, markdown: str, out: str | Path, *,
                    after_section: str | None = None, before_section: str | None = None,
                    as_level: int | None = None, images: ImageResolver | None = None,
                    author: str | None = None) -> ComposeReport:
    """Insert Markdown content (e.g. a new chapter) in the document's own house style.

    after_section / before_section: a section number ("4", "3.2") or exact heading title.
    as_level: heading level the top heading of the inserted content should get.
    Default position: at the end of the body.
    """
    pkg = doc.copy() if isinstance(doc, DocxPackage) else DocxPackage.open(doc)
    model = DocModel(pkg)
    report = ComposeReport()
    outline = model.outline()
    ref: etree._Element | None = None
    level = as_level
    if after_section or before_section:
        key = (after_section or before_section).strip()
        node = next((n for n in outline if n.number == key or n.title.lower() == key.lower()), None)
        if node is None:
            raise ValueError(f"Sectie '{key}' niet gevonden. Beschikbaar: " +
                             ", ".join(f"{n.number} {n.title}" for n in outline[:40]))
        if after_section:
            idx = node.end
            level = level or node.level
        else:
            idx = node.start
            level = level or node.level
        ref = model.blocks[idx].element if idx < len(model.blocks) else None
        if ref is not None and etree.QName(ref).localname == "sectPr":
            ref = None
    else:
        level = level or 1
    if level:
        markdown = _shift_headings(markdown, level)
    builder = Builder(model, images)
    elements = builder.build_markdown(markdown)
    _insert_before(pkg, ref, elements)
    report.inserted_blocks = len(elements)
    _finish(pkg, report, None, author, out)
    report.path = str(pkg.save(out))
    return report


def replace_section(doc: str | Path | DocxPackage, section: str, markdown: str, out: str | Path, *,
                    keep_heading: bool = True, keep_subsections: bool = False,
                    images: ImageResolver | None = None, author: str | None = None) -> ComposeReport:
    """Replace the content of one section (identified by number or title)."""
    pkg = doc.copy() if isinstance(doc, DocxPackage) else DocxPackage.open(doc)
    model = DocModel(pkg)
    report = ComposeReport()
    outline = model.outline()
    node = next((n for n in outline if n.number == section or n.title.lower() == section.lower()), None)
    if node is None:
        raise ValueError(f"Sectie '{section}' niet gevonden.")
    end = node.end
    if keep_subsections:
        nxt = next((n for n in outline if n.start > node.start), None)
        if nxt and nxt.start < end:
            end = nxt.start
    builder = Builder(model, images)
    first = node.start + (1 if keep_heading else 0)
    ref = model.blocks[end].element if end < len(model.blocks) else None
    if ref is not None and etree.QName(ref).localname == "sectPr":
        ref = None
    if keep_heading:
        markdown = re.sub(r"^\s*#{1,9}\s+.*\n", "", markdown, count=1) if markdown.lstrip().startswith("#") and \
            _first_heading_matches(markdown, node.title) else markdown
        markdown = _shift_headings(markdown, node.level + 1)
    for b in model.blocks[first:end]:
        if b.kind != "section":
            b.element.getparent().remove(b.element)
    elements = builder.build_markdown(markdown)
    _insert_before(pkg, ref, elements)
    report.inserted_blocks = len(elements)
    _finish(pkg, report, None, author, out)
    report.path = str(pkg.save(out))
    return report


def _first_heading_matches(md: str, title: str) -> bool:
    m = re.match(r"\s*#{1,9}\s+(.*)", md)
    if not m:
        return False
    t = re.sub(r"^[\d.]+\s+", "", m.group(1)).strip().lower()
    return t == title.strip().lower()


def docx_to_markdown(src: str | Path | DocxPackage, *, skip_front: bool = True) -> tuple[str, ImageResolver]:
    """Read another .docx as Markdown + an image resolver for its media (for insert/restyle)."""
    pkg = src if isinstance(src, DocxPackage) else DocxPackage.open(src)
    model = DocModel(pkg)
    start = body_start(model) if skip_front else 0
    blocks = [b for b in model.blocks[start:] if not is_guidance(b)]
    from .reader import block_markdown
    md = "\n\n".join(m for m in (block_markdown(b) for b in blocks) if m)

    def resolver(ref: str):
        if pkg.has(ref):
            ext = ref.rsplit(".", 1)[-1].lower()
            return pkg.raw(ref), ext
        return None

    return md, resolver


def restyle(src: str | Path, template: str | Path, out: str | Path, *, fields: dict[str, str] | None = None,
            title: str | None = None, author: str | None = None) -> ComposeReport:
    """Pour any document into the house style of a template."""
    md, resolver = docx_to_markdown(src)
    return new_from_template(template, md, out, fields=fields, title=title, author=author, images=resolver)


def merge(docs: list[str | Path], out: str | Path, *, template: str | Path | None = None,
          author: str | None = None) -> ComposeReport:
    """Merge documents: the first (or a template) provides the house style, others are appended."""
    base = template or docs[0]
    rest = docs if template else docs[1:]
    pkg = DocxPackage.open(base)
    report = ComposeReport()
    total = 0
    for d in rest:
        md, resolver = docx_to_markdown(d)
        model = DocModel(pkg)
        builder = Builder(model, resolver)
        elements = builder.build_markdown(_shift_headings(md, 1))
        _insert_before(pkg, None, elements)
        total += len(elements)
    report.inserted_blocks = total
    _finish(pkg, report, None, author, out)
    report.path = str(pkg.save(out))
    return report


__all__ = ["new_from_template", "insert_markdown", "replace_section", "restyle", "merge", "docx_to_markdown",
           "body_start", "ComposeReport", "parse_markdown"]
