"""Schema child order for the OOXML property containers we modify.

Word refuses to open documents whose property children are out of schema
order, so every insert into pPr/rPr/tcPr/settings goes through `put`.
"""
from __future__ import annotations

from lxml import etree

from .ns import qn, local

ORDER = {
    "pPr": "pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs "
           "suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi "
           "adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection "
           "textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange",
    "rPr": "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid "
           "vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs "
           "em lang eastAsianLayout specVanish oMath",
    "numPr": "ilvl numId numberingChange ins",
    "tcPr": "cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark",
    "tblPr": "tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc tblCellSpacing "
             "tblInd tblBorders shd tblLayout tblCellMar tblLook tblCaption tblDescription",
    "settings": "writeProtection view zoom removePersonalInformation removeDateAndTime doNotDisplayPageBoundaries "
                "displayBackgroundShape printPostScriptOverText printFractionalCharacterWidth printFormsData "
                "embedTrueTypeFonts embedSystemFonts saveSubsetFonts saveFormsData mirrorMargins alignBordersAndEdges "
                "bordersDoNotSurroundHeader bordersDoNotSurroundFooter gutterAtTop hideSpellingErrors "
                "hideGrammaticalErrors activeWritingStyle proofState formsDesign attachedTemplate linkStyles "
                "stylePaneFormatFilter stylePaneSortMethod documentType mailMerge revisionView trackRevisions "
                "doNotTrackMoves doNotTrackFormatting documentProtection autoFormatOverride styleLockTheme "
                "styleLockQFSet defaultTabStop autoHyphenation consecutiveHyphenLimit hyphenationZone "
                "doNotHyphenateCaps showEnvelope summaryLength clickAndTypeStyle defaultTableStyle evenAndOddHeaders "
                "bookFoldRevPrinting bookFoldPrinting bookFoldPrintingSheets drawingGridHorizontalSpacing "
                "drawingGridVerticalSpacing displayHorizontalDrawingGridEvery displayVerticalDrawingGridEvery "
                "doNotUseMarginsForDrawingGridOrigin drawingGridHorizontalOrigin drawingGridVerticalOrigin "
                "doNotShadeFormData noPunctuationKerning characterSpacingControl printTwoOnOne "
                "strictFirstAndLastChars noLineBreaksAfter noLineBreaksBefore savePreviewPicture "
                "doNotValidateAgainstSchema saveInvalidXml ignoreMixedContent alwaysShowPlaceholderText "
                "doNotDemarcateInvalidXml saveXmlDataOnly useXSLTWhenSaving saveThroughXslt showXMLTags "
                "alwaysMergeEmptyNamespace updateFields hdrShapeDefaults footnotePr endnotePr compat docVars rsids "
                "mathPr attachedSchema themeFontLang clrSchemeMapping doNotIncludeSubdocsInStats "
                "doNotAutoCompressPictures forceUpgrade captions readModeInkLockDown smartTagType schemaLibrary "
                "shapeDefaults doNotEmbedSmartTags decimalSymbol listSeparator",
}
_INDEX = {k: {n: i for i, n in enumerate(v.split())} for k, v in ORDER.items()}


def put(parent: etree._Element, child: etree._Element, replace: bool = True) -> etree._Element:
    """Insert `child` into a property container at its schema position."""
    idx = _INDEX[local(parent)]
    name = local(child)
    if replace:
        for old in parent.findall(child.tag):
            parent.remove(old)
    pos = idx.get(name, 10_000)
    for i, existing in enumerate(parent):
        if idx.get(local(existing), 10_000) > pos:
            parent.insert(i, child)
            return child
    parent.append(child)
    return child


def put_new(parent: etree._Element, tag: str, attrs: dict[str, str] | None = None) -> etree._Element:
    e = etree.Element(qn(tag))
    for k, v in (attrs or {}).items():
        e.set(qn(k), v)
    return put(parent, e)


def get_or_add(parent: etree._Element, tag: str) -> etree._Element:
    e = parent.find(qn(tag))
    if e is None:
        e = put(parent, etree.Element(qn(tag)))
    return e
