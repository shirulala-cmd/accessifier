"""Orchestration: one PDF in, three files out.

The stages are deliberately separate so each one can fail without taking the
others down. A document that cannot be OCR'd is still worth tagging; a PDF that
cannot be produced should not prevent the HTML from being written, since the
HTML is usually the better screen-reader experience anyway.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import ocr, report, structure, tag_pdf, to_html, triage, validate
from .models import Document, Finding, Level

log = logging.getLogger(__name__)

MIN_OCR_QUALITY = 0.75


@dataclass
class Result:
    source: Path
    out_dir: Path
    document: Document | None = None
    html_path: Path | None = None
    pdf_path: Path | None = None
    report_path: Path | None = None
    ocr_path: Path | None = None
    pdf_checks: dict[str, bool] = field(default_factory=dict)
    verapdf: dict | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def level(self) -> Level | None:
        return self.document.diagnosis.level if self.document and self.document.diagnosis else None

    @property
    def manual_review_required(self) -> bool:
        return bool(self.document and self.document.manual_review_required)


def process(
    src: str | Path,
    out_dir: str | Path,
    *,
    force_level: Level | None = None,
    skip_ocr: bool = False,
    skip_pdf: bool = False,
    run_verapdf: bool = False,
) -> Result:
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = Result(source=src, out_dir=out_dir)

    # Stage 0-1: diagnose, then OCR only the pages that need it.
    diagnosis = triage.diagnose(src)
    if force_level and force_level is not diagnosis.level:
        log.info("סיווג נכפה ידנית: %s (האבחון קבע %s)", force_level.value, diagnosis.level.value)
        diagnosis.reasons.insert(0, f"הסיווג נכפה ידנית לרמה {force_level.value}")
        diagnosis.level = force_level

    working = src
    if diagnosis.needs_ocr and not skip_ocr:
        working = _run_ocr(src, out_dir, diagnosis, result)

    # Stage 2: structure extraction.
    document = structure.extract(working, diagnosis)
    document.source_path = src
    result.document = document

    for finding in validate.check_document(document):
        document.add_finding(finding)

    # Stage 3: accessible HTML. This is the canonical output.
    html = to_html.render(document)
    result.html_path = out_dir / f"{src.stem}-accessible.html"
    result.html_path.write_text(html, encoding="utf-8")
    for finding in validate.check_html(html):
        document.add_finding(finding)

    # Stage 4: tagged PDF, rendered from the HTML above.
    if not skip_pdf:
        _build_pdf(html, out_dir, src, document, result, run_verapdf)

    # Stage 6: the report, which must be written even when earlier stages failed.
    result.report_path = out_dir / f"{src.stem}-accessibility-report.html"
    result.report_path.write_text(
        report.render(document, result.pdf_checks, result.verapdf), encoding="utf-8"
    )
    return result


def _run_ocr(src: Path, out_dir: Path, diagnosis, result: Result) -> Path:
    """OCR the scanned pages. Falls back to the original on any failure."""
    dest = out_dir / f"{src.stem}-ocr.pdf"
    try:
        ocr.run(src, dest, pages=diagnosis.scanned_pages or None)
    except ocr.OCRUnavailable as exc:
        result.errors.append(str(exc))
        log.warning("OCR לא זמין: %s", exc)
        return src
    except ocr.OCRFailed as exc:
        result.errors.append(f"OCR נכשל: {exc}")
        log.warning("OCR נכשל: %s", exc)
        return src

    quality = ocr.quality_estimate(dest)
    log.info("איכות OCR משוערת: %.2f", quality)
    if quality < MIN_OCR_QUALITY:
        result.errors.append(
            f"איכות ה-OCR נמוכה ({quality:.0%}) — ייתכן שהטקסט שחולץ שגוי וטעון בדיקה ידנית"
        )
    result.ocr_path = dest
    return dest


def _build_pdf(
    html: str, out_dir: Path, src: Path, document: Document, result: Result, want_verapdf: bool
) -> None:
    pdf_path = out_dir / f"{src.stem}-accessible.pdf"
    try:
        tag_pdf.html_to_tagged_pdf(html, pdf_path, title=document.title, lang=document.lang)
    except tag_pdf.TaggingError as exc:
        result.errors.append(str(exc))
        log.error("יצירת ה-PDF המתוייג נכשלה: %s", exc)
        return
    except Exception as exc:  # noqa: BLE001 - keep the HTML and report usable
        result.errors.append(f"יצירת ה-PDF נכשלה: {exc}")
        log.exception("יצירת ה-PDF נכשלה")
        return

    result.pdf_path = pdf_path
    result.pdf_checks = tag_pdf.verify_tagged(pdf_path)
    for finding in validate.check_pdf(pdf_path):
        document.add_finding(finding)

    if want_verapdf:
        if shutil.which("verapdf"):
            result.verapdf = validate.run_verapdf(pdf_path)
        else:
            result.errors.append("VeraPDF אינו מותקן — בדיקת התאימות הרשמית לא בוצעה")


def summarise(result: Result) -> str:
    """Short Hebrew summary for the CLI."""
    doc = result.document
    lines: list[str] = []
    if doc and doc.diagnosis:
        d = doc.diagnosis
        lines.append(f"סיווג: רמה {d.level.value} — {d.level.hebrew} ({d.pages} עמודים)")
        for reason in d.reasons:
            lines.append(f"  · {reason}")
    if doc:
        fixed = sum(1 for f in doc.findings if f.fixed)
        todo = sum(1 for f in doc.findings if not f.fixed)
        lines.append(f"תוקן אוטומטית: {fixed} · דורש טיפול ידני: {todo}")
    if result.pdf_checks:
        failed = [k for k, v in result.pdf_checks.items() if not v]
        lines.append("בדיקות PDF: " + ("הכל תקין" if not failed else f"חסר — {', '.join(failed)}"))
    for err in result.errors:
        lines.append(f"אזהרה: {err}")
    return "\n".join(lines)
