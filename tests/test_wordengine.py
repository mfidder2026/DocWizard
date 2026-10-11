import io
import re
import zipfile
from pathlib import Path

import pytest

from docwizard.wordengine.compose import insert_markdown, new_from_template, replace_section, restyle
from docwizard.wordengine.fields import find_placeholders
from docwizard.wordengine.inplace import Comments, RevisionIds, apply_text, extract_segments
from docwizard.wordengine.package import DocxPackage
from docwizard.wordengine.preview import to_html
from docwizard.wordengine.profile import analyze_template
from docwizard.wordengine.reader import DocModel
from docwizard.wordengine.validate import validate

FIX = Path(__file__).parent / "fixtures"
PRIVATE = Path(__file__).parent / "private"
SAMPLE = FIX / "aqs_template.docx"
REAL = sorted(PRIVATE.glob("*.docx")) if PRIVATE.exists() else []

MD = """# Inleiding
## 1.1 Aanleiding
Tekst met **vet**, *cursief*, `code` en een [link](https://example.org).

- punt
  - subpunt
- punt twee

1. eerste
2. tweede

# Bevindingen
| Thema | Bevinding |
|---|---|
| IAM | Geen MFA |

![Plaat](plaat.png)

> Citaat

```
code regel
```
"""


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PNG")
    return buf.getvalue()


def _resolver(src):
    return (_png(), "png") if src == "plaat.png" else None


@pytest.fixture(scope="session", autouse=True)
def sample_template():
    if not SAMPLE.exists():
        import subprocess, sys
        subprocess.check_call([sys.executable, "scripts/make_sample_template.py", str(SAMPLE)])


def test_reader_outline_and_markdown():
    m = DocModel.open(SAMPLE)
    titles = [(o.number, o.title) for o in m.outline()]
    assert titles[0] == ("1", "Inleiding") and ("1.2", "Doel en scope") in titles
    md = m.to_markdown()
    assert "# Inleiding" in md and "| Thema | Bevinding | Risico | Advies |" in md


def test_lossless_roundtrip():
    for f in [SAMPLE, *REAL]:
        a = zipfile.ZipFile(f)
        b = zipfile.ZipFile(io.BytesIO(DocxPackage.open(f).to_bytes()))
        assert [n for n in a.namelist() if a.read(n) != b.read(n)] == []


def test_new_from_template(tmp_path):
    out = tmp_path / "new.docx"
    rep = new_from_template(SAMPLE, MD, out, images=_resolver,
                            fields={"klant": "RIVM", "versie": "0.1", "datum": "1 jan", "auteur": "MF"})
    assert validate(DocxPackage.open(out)) == []
    m = DocModel.open(out)
    assert [o.title for o in m.outline()][:2] == ["Inleiding", "Aanleiding"]   # manual number stripped
    text = "\n".join(b.text for b in m.blocks)
    assert "RIVM" in text and "{{" not in text and "[Beschrijf" not in text
    assert rep.toc_entries == 3
    assert any(b.kind == "image" for b in m.blocks)
    kinds = {b.kind for b in m.blocks}
    assert {"heading", "list", "table", "paragraph", "toc"} <= kinds
    header = DocxPackage.open(out).xml("word/header2.xml") if DocxPackage.open(out).has("word/header2.xml") else None
    if header is not None:
        assert "{{klant}}" not in "".join(header.itertext())


def test_insert_and_replace(tmp_path):
    out = tmp_path / "ins.docx"
    insert_markdown(SAMPLE, "# Nieuw hoofdstuk\nInhoud.\n## Sub\nMeer.", out, after_section="3")
    m = DocModel.open(out)
    top = [o.title for o in m.outline() if o.level == 1]
    assert top.index("Nieuw hoofdstuk") == top.index("Bevindingen") + 1
    assert validate(DocxPackage.open(out)) == []
    out2 = tmp_path / "rep.docx"
    replace_section(SAMPLE, "Aanleiding", "Nieuwe tekst.", out2)
    m2 = DocModel.open(out2)
    node = next(o for o in m2.outline() if o.title == "Aanleiding")
    assert "Nieuwe tekst." in m2.blocks[node.start + 1].text


def test_inplace_tracked_and_comments(tmp_path):
    pkg = DocxPackage.open(SAMPLE)
    segs = extract_segments(pkg)
    seg = next(s for s in segs if s.plain.startswith("Deze quick scan"))
    apply_text(seg, seg.plain.replace("interviews", "gesprekken"), track=True, rev_ids=RevisionIds(pkg))
    Comments(pkg).add(seg.paragraph, "Bron toevoegen")
    out = tmp_path / "tracked.docx"
    pkg.save(out)
    assert validate(DocxPackage.open(out)) == []
    xml = DocxPackage.open(out).raw("word/document.xml").decode()
    assert "<w:del " in xml and "<w:ins " in xml and "commentReference" in xml


def test_tags_keep_tabs():
    pkg = DocxPackage.open(SAMPLE)
    for s in extract_segments(pkg):
        if s.atoms:
            new = re.sub(r"(<[^>]+>)|([^<]+)", lambda m: m.group(1) or m.group(2).upper(), s.tagged)
            assert apply_text(s, new)
    assert validate(pkg) == []


def test_profile_and_preview():
    p = analyze_template(SAMPLE)
    assert p["outline"][1]["guidance"]
    assert {f["key"] for f in p["fields"]} >= {"klant", "versie"}
    assert "<h1>" in to_html(SAMPLE).replace('<h1><span', "<h1>")


@pytest.mark.skipif(not REAL, reason="geen privé-voorbeelddocumenten")
@pytest.mark.parametrize("doc", REAL, ids=lambda p: p.name)
def test_real_documents(doc, tmp_path):
    out = tmp_path / "new.docx"
    new_from_template(doc, MD, out, images=_resolver)
    assert validate(DocxPackage.open(out)) == []
    out2 = tmp_path / "restyled.docx"
    restyle(SAMPLE, doc, out2)
    assert validate(DocxPackage.open(out2)) == []
    pkg = DocxPackage.open(doc)
    for s in extract_segments(pkg):
        apply_text(s, s.tagged)
    assert validate(pkg) == []
    assert find_placeholders(pkg) is not None
