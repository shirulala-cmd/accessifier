"""End-to-end: a Hebrew PDF in, a tagged PDF and accessible HTML out."""

from __future__ import annotations

import re

import pytest

from accessifier import Level, pipeline, triage
from accessifier.tag_pdf import structure_summary, verify_tagged


def test_triage_plain_document(plain_pdf):
    diag = triage.diagnose(plain_pdf)
    assert diag.pages == 1
    assert not diag.needs_ocr
    assert not diag.has_struct_tree
    # No struct tree means the structure has to be reconstructed.
    assert diag.level in (Level.B, Level.C)


def test_triage_detects_low_contrast(low_contrast_pdf):
    diag = triage.diagnose(low_contrast_pdf)
    assert diag.low_contrast_pages
    assert diag.level is Level.C


def test_triage_detects_scanned_page(scanned_pdf):
    diag = triage.diagnose(scanned_pdf)
    assert diag.needs_ocr
    assert diag.level is Level.C


@pytest.fixture(scope="module")
def processed(plain_pdf, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    return pipeline.process(plain_pdf, out, skip_ocr=True)


def test_outputs_exist(processed):
    assert processed.html_path and processed.html_path.exists()
    assert processed.pdf_path and processed.pdf_path.exists()
    assert processed.report_path and processed.report_path.exists()


def test_html_is_accessible(processed):
    html = processed.html_path.read_text(encoding="utf-8")
    assert 'lang="he"' in html and 'dir="rtl"' in html
    assert "skip-link" in html
    assert '<main id="main"' in html
    assert html.count("<h1") == 1
    # No heading level is skipped.
    levels = [int(m) for m in re.findall(r"<h([1-6])", html)]
    assert all(b - a <= 1 for a, b in zip(levels, levels[1:]))
    # No positive tabindex, which would break the natural tab order.
    assert not re.search(r'tabindex="[1-9]', html)


def test_hebrew_survives_in_logical_order(processed):
    html = processed.html_path.read_text(encoding="utf-8")
    assert "נגישות" in html
    assert "מסמך" in html
    # The telltale sign of reversed extraction.
    assert "תושיגנ" not in html


def test_structure_was_extracted(processed):
    doc = processed.document
    assert doc is not None
    assert doc.headings, "אף כותרת לא זוהתה"
    assert doc.title


def test_running_header_marked_as_artifact(processed):
    html = processed.html_path.read_text(encoding="utf-8")
    # Single-page fixture, so the runner is not repeated and stays in the text;
    # what matters is that it never becomes a heading.
    assert "<h1>מדריך ההנגשה" not in html


def test_pdf_is_tagged(processed):
    checks = verify_tagged(processed.pdf_path)
    assert checks["struct_tree"], "אין עץ תיוג ב-PDF"
    assert checks["marked"], "המסמך אינו מסומן כ-Tagged"
    assert checks["lang"], "לא הוגדרה שפת מסמך"
    assert checks["display_doc_title"], "DisplayDocTitle כבוי"
    assert checks["title"], "אין כותרת במטא-דאטה"


def test_pdf_structure_has_semantic_tags(processed):
    tags = structure_summary(processed.pdf_path)
    assert tags, "עץ התיוג ריק"
    assert any(t.startswith("H") for t in tags), f"אין כותרות בעץ התיוג: {tags}"


def test_report_mentions_the_level(processed):
    report = processed.report_path.read_text(encoding="utf-8")
    assert processed.level is not None
    assert f"רמה {processed.level.value}" in report
    assert 'dir="rtl"' in report


def test_force_level_is_honoured(plain_pdf, tmp_path):
    result = pipeline.process(plain_pdf, tmp_path, force_level=Level.C, skip_ocr=True)
    assert result.level is Level.C
    assert any("נכפה" in r for r in result.document.diagnosis.reasons)


def test_missing_alt_is_reported_not_invented(processed):
    """A figure without alt text must be flagged, never described by guesswork."""
    html = processed.html_path.read_text(encoding="utf-8")
    for img in re.findall(r"<img\b[^>]*>", html):
        assert "alt=" in img
    doc = processed.document
    if any(b.needs_alt for b in doc.content_blocks):
        assert any(f.code == "IMAGE_NO_ALT" and not f.fixed for f in doc.findings)
        assert "דרוש תיאור חלופי" in html


def test_no_pdf_mode_still_writes_html_and_report(plain_pdf, tmp_path):
    result = pipeline.process(plain_pdf, tmp_path, skip_ocr=True, skip_pdf=True)
    assert result.html_path.exists()
    assert result.report_path.exists()
    assert result.pdf_path is None
