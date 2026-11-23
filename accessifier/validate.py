"""Stage 5: check the outputs and collect what still needs a human."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .models import BlockType, Document, Finding
from .tag_pdf import verify_tagged

log = logging.getLogger(__name__)


def check_document(document: Document) -> list[Finding]:
    """Content-level issues that automation cannot resolve on its own."""
    findings: list[Finding] = []

    missing_alt = [b for b in document.content_blocks if b.needs_alt]
    if missing_alt:
        pages = sorted({b.page for b in missing_alt})
        findings.append(
            Finding(
                "IMAGE_NO_ALT",
                f"{len(missing_alt)} תמונות ללא טקסט חלופי (עמודים {', '.join(map(str, pages))}). "
                "יש לכתוב תיאור ידנית — תיאור שנוצר אוטומטית ללא אימות עלול להטעות.",
                wcag="1.1.1",
                fixed=False,
            )
        )

    for block in document.content_blocks:
        if block.type is BlockType.TABLE and block.rows:
            width = len(block.rows[0])
            if any(len(r) != width for r in block.rows):
                findings.append(
                    Finding(
                        "TABLE_IRREGULAR",
                        f"טבלה בעמוד {block.page} עם מספר עמודות לא אחיד — "
                        "ייתכן שיש תאים ממוזגים שדורשים בדיקה ידנית.",
                        wcag="1.3.1",
                        page=block.page,
                        fixed=False,
                    )
                )
                break

    if not document.headings:
        findings.append(
            Finding(
                "NO_HEADINGS",
                "לא זוהו כותרות במסמך. ניווט בקורא מסך יהיה קשה — "
                "כדאי לוודא שהמקור אכן חסר כותרות ולא שהן לא זוהו.",
                wcag="1.3.1",
                fixed=False,
            )
        )

    diag = document.diagnosis
    if diag and diag.low_contrast_pages:
        findings.append(
            Finding(
                "CONTRAST_REBUILT",
                f"ניגודיות נמוכה זוהתה בעמודים {', '.join(map(str, diag.low_contrast_pages))} "
                "ותוקנה בגרסה החדשה על ידי החלפת צבעי הטקסט.",
                wcag="1.4.3",
                fixed=True,
            )
        )
    return findings


def check_html(html: str) -> list[Finding]:
    """Structural checks on the generated HTML."""
    findings: list[Finding] = []

    if 'lang="he"' not in html or 'dir="rtl"' not in html:
        findings.append(Finding("HTML_LANG", "חסרה הגדרת שפה או כיוון ב-HTML", "3.1.1"))

    levels = [int(m) for m in re.findall(r"<h([1-6])", html)]
    gaps = [(a, b) for a, b in zip(levels, levels[1:]) if b - a > 1]
    if gaps:
        findings.append(
            Finding("HTML_HEADING_GAP", f"דילוג בהיררכיית הכותרות: {gaps}", "1.3.1")
        )
    if levels.count(1) > 1:
        findings.append(Finding("HTML_MULTIPLE_H1", "יותר מכותרת H1 אחת בעמוד", "1.3.1"))

    imgs = re.findall(r"<img\b[^>]*>", html)
    without_alt = [i for i in imgs if "alt=" not in i]
    if without_alt:
        findings.append(
            Finding("HTML_IMG_NO_ALT", f"{len(without_alt)} תמונות ללא מאפיין alt", "1.1.1")
        )

    if "skip-link" not in html:
        findings.append(Finding("HTML_NO_SKIP", "חסר קישור 'דלג לתוכן המרכזי'", "2.4.1"))

    for table in re.findall(r"<table\b.*?</table>", html, re.S):
        if "<th" not in table:
            findings.append(Finding("HTML_TABLE_NO_TH", "טבלה ללא שורת כותרות", "1.3.1"))
            break

    if re.search(r'tabindex="[1-9]', html):
        findings.append(Finding("HTML_POSITIVE_TABINDEX", "שימוש ב-tabindex חיובי", "2.4.3"))

    for f in findings:
        f.fixed = False
    return findings


def check_pdf(path: str | Path) -> list[Finding]:
    """Structural checks on the produced PDF."""
    checks = verify_tagged(path)
    labels = {
        "struct_tree": ("PDF_NO_STRUCT", "אין עץ תיוג במסמך", "1.3.1"),
        "marked": ("PDF_NOT_MARKED", "המסמך אינו מסומן כ-Tagged", "1.3.1"),
        "lang": ("PDF_NO_LANG", "לא הוגדרה שפת מסמך", "3.1.1"),
        "display_doc_title": ("PDF_NO_DISPLAY_TITLE", "הצגת כותרת המסמך אינה מופעלת", "2.4.2"),
        "title": ("PDF_NO_TITLE", "למסמך אין כותרת במטא-דאטה", "2.4.2"),
        "xmp_pdfua": ("PDF_NO_UAID", "חסר סימון pdfuaid ב-XMP", "1.3.1"),
    }
    return [
        Finding(code, msg, wcag, fixed=False)
        for key, (code, msg, wcag) in labels.items()
        if not checks.get(key)
    ]


def run_verapdf(path: str | Path) -> dict[str, object] | None:
    """Run VeraPDF if it is installed. Returns None when unavailable."""
    binary = shutil.which("verapdf")
    if not binary:
        log.info("VeraPDF אינו מותקן — מדלג על בדיקת התאימות הרשמית")
        return None
    try:
        proc = subprocess.run(
            [binary, "--flavour", "ua1", "--format", "text", str(path)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("הרצת VeraPDF נכשלה: %s", exc)
        return None
    passed = "PASS" in proc.stdout.upper() and "FAIL" not in proc.stdout.upper()
    return {"passed": passed, "output": proc.stdout[-4000:]}
