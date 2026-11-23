"""Stage 0: diagnose the document and decide how far remediation has to go."""

from __future__ import annotations

import logging
from pathlib import Path

import pikepdf
import pymupdf

from . import rtl
from .models import Diagnosis, Level

log = logging.getLogger(__name__)

# A page with fewer characters than this, that is mostly covered by an image,
# is treated as a scan rather than as a text page.
MIN_CHARS_PER_PAGE = 60
IMAGE_COVERAGE_SCANNED = 0.5
CONTRAST_MIN = 4.5


class EncryptedPDF(Exception):
    """Password-protected input. We refuse rather than guess."""


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _int_to_rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


def _image_coverage(page: pymupdf.Page) -> float:
    """Share of the page area covered by raster images."""
    page_area = abs(page.rect.get_area()) or 1.0
    covered = 0.0
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:  # image block
            x0, y0, x1, y1 = block["bbox"]
            covered += abs((x1 - x0) * (y1 - y0))
    return min(covered / page_area, 1.0)


def _count_columns(page: pymupdf.Page) -> int:
    """Rough column count from the horizontal spread of text block centres."""
    centres = []
    width = page.rect.width or 1.0
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        x0, _, x1, _ = block["bbox"]
        if (x1 - x0) > width * 0.6:
            # A block spanning most of the width is a full-width block, which
            # tells us nothing about columns.
            continue
        centres.append(((x0 + x1) / 2) / width)
    if len(centres) < 4:
        return 1
    centres.sort()
    clusters = [[centres[0]]]
    for c in centres[1:]:
        if c - clusters[-1][-1] > 0.18:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    # Ignore clusters that hold only a stray block or two.
    return max(1, sum(1 for cl in clusters if len(cl) >= 2))


def _fonts_without_unicode(pdf: pikepdf.Pdf) -> list[str]:
    """Embedded fonts with no ToUnicode map cannot be reliably extracted."""
    bad: set[str] = set()
    for page in pdf.pages:
        fonts = page.get("/Resources", {}).get("/Font", {})
        if not isinstance(fonts, pikepdf.Dictionary):
            continue
        for font in fonts.values():
            if not isinstance(font, pikepdf.Dictionary):
                continue
            subtype = str(font.get("/Subtype", ""))
            if subtype == "/Type0" and "/ToUnicode" not in font:
                bad.add(str(font.get("/BaseFont", "לא ידוע")))
            elif subtype in ("/TrueType", "/Type1") and "/ToUnicode" not in font:
                # Standard encodings are extractable without ToUnicode.
                enc = font.get("/Encoding")
                if enc is None or (isinstance(enc, pikepdf.Dictionary) and "/Differences" in enc):
                    bad.add(str(font.get("/BaseFont", "לא ידוע")))
    return sorted(bad)


def _low_contrast_pages(doc: pymupdf.Document) -> list[int]:
    """Pages where body text fails the 4.5:1 ratio against a white background.

    We assume a white page. That is true for the overwhelming majority of
    documents, and a wrong guess here only produces a report warning.
    """
    white = (1.0, 1.0, 1.0)
    failing = []
    for i, page in enumerate(doc, start=1):
        worst = 21.0
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if not span.get("text", "").strip():
                        continue
                    ratio = contrast_ratio(_int_to_rgb(span.get("color", 0)), white)
                    worst = min(worst, ratio)
        if worst < CONTRAST_MIN:
            failing.append(i)
    return failing


def diagnose(path: str | Path) -> Diagnosis:
    """Inspect a PDF and classify it into level A, B or C."""
    path = Path(path)

    with pikepdf.open(path) as pdf:
        if pdf.is_encrypted:
            raise EncryptedPDF("הקובץ מוצפן או מוגן בסיסמה")
        has_struct = "/StructTreeRoot" in pdf.Root
        bad_fonts = _fonts_without_unicode(pdf)
        meta_title = ""
        try:
            with pdf.open_metadata() as meta:
                meta_title = str(meta.get("dc:title", "") or "")
        except Exception:  # noqa: BLE001 - metadata is optional
            pass

    doc = pymupdf.open(path)
    try:
        pages = doc.page_count
        total_chars = 0
        scanned: list[int] = []
        max_columns = 1

        for i, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            total_chars += len(text)
            if len(text) < MIN_CHARS_PER_PAGE and _image_coverage(page) > IMAGE_COVERAGE_SCANNED:
                scanned.append(i)
            max_columns = max(max_columns, _count_columns(page))

        low_contrast = _low_contrast_pages(doc)
        chars_per_page = total_chars / pages if pages else 0.0
        title = meta_title or doc.metadata.get("title", "") or path.stem
        sample = "\n".join(doc[i].get_text() for i in range(min(pages, 3)))
        legacy = rtl.looks_legacy_encoded(sample)
    finally:
        doc.close()

    reasons: list[str] = []
    level = Level.A

    def escalate(to: Level, why: str) -> None:
        nonlocal level
        reasons.append(why)
        order = {Level.A: 0, Level.B: 1, Level.C: 2}
        if order[to] > order[level]:
            level = to

    if scanned:
        escalate(
            Level.C,
            f"{len(scanned)} עמודים סרוקים ללא שכבת טקסט (עמודים {', '.join(map(str, scanned[:6]))})",
        )
    if bad_fonts:
        escalate(
            Level.C,
            f"{len(bad_fonts)} גופנים ללא מיפוי ToUnicode — הטקסט אינו ניתן לחילוץ אמין",
        )
    if legacy:
        # The design cannot be preserved: every string has to be re-decoded and
        # re-ordered, so the document is effectively rebuilt.
        escalate(
            Level.C,
            "העברית במסמך מקודדת בתקן מיושן (Windows-1255) ללא Unicode — "
            "הטקסט פוענח מחדש והמסמך נבנה מחדש",
        )
    if low_contrast:
        escalate(
            Level.C,
            f"ניגודיות נמוכה מ-4.5:1 בעמודים {', '.join(map(str, low_contrast[:6]))}",
        )
    if max_columns > 2:
        escalate(Level.C, f"פריסה של {max_columns} טורים — סדר הקריאה אינו אמין")
    elif max_columns == 2:
        escalate(Level.B, "פריסה דו-טורית — נדרש שחזור סדר הקריאה")
    if not has_struct:
        escalate(Level.B, "אין עץ תיוג (StructTreeRoot) במסמך")
    if 0 < chars_per_page < MIN_CHARS_PER_PAGE and not scanned:
        escalate(Level.B, "כמות טקסט נמוכה מאוד לעמוד")

    diag = Diagnosis(
        level=level,
        reasons=reasons,
        pages=pages,
        chars_per_page=chars_per_page,
        scanned_pages=scanned,
        has_struct_tree=has_struct,
        fonts_without_unicode=bad_fonts,
        max_columns=max_columns,
        low_contrast_pages=low_contrast,
        title=title.strip(),
        legacy_encoding=legacy,
    )
    log.info("סיווג: רמה %s — %s", diag.level.value, diag.reason_text)
    return diag
