"""Stage 1: OCR for scanned pages, via ocrmypdf and Tesseract."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pymupdf

from . import rtl

log = logging.getLogger(__name__)

OCR_TIMEOUT_SECONDS = 900


class OCRUnavailable(Exception):
    """Tesseract, the Hebrew language pack or ocrmypdf is missing."""


class OCRFailed(Exception):
    pass


def available_languages() -> list[str]:
    if not shutil.which("tesseract"):
        return []
    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    lines = proc.stdout.splitlines()[1:]
    return [ln.strip() for ln in lines if ln.strip()]


def check_environment() -> None:
    """Fail loudly and early if the Hebrew pack is missing.

    This is the dependency that is most often forgotten, and without it Hebrew
    scans come out as gibberish rather than as an obvious error.
    """
    if not shutil.which("ocrmypdf"):
        raise OCRUnavailable("ocrmypdf אינו מותקן. התקיני: pip install ocrmypdf")
    langs = available_languages()
    if not langs:
        raise OCRUnavailable("Tesseract אינו מותקן או שאינו נגיש ב-PATH")
    if "heb" not in langs:
        raise OCRUnavailable(
            "חבילת השפה העברית של Tesseract חסרה. "
            "בלעדיה מסמכים סרוקים בעברית ייצאו כג'יבריש. "
            "התקיני: apt-get install tesseract-ocr-heb "
            f"(שפות מותקנות: {', '.join(langs)})"
        )


def run(src: str | Path, dest: str | Path, pages: list[int] | None = None) -> Path:
    """OCR a PDF and return the path to the searchable copy."""
    check_environment()
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ocrmypdf",
        "--language", "heb+eng",
        "--deskew",
        "--clean",
        "--rotate-pages",
        "--output-type", "pdf",
        "--quiet",
    ]
    if pages:
        # Only the pages that actually need it; re-OCR of good text degrades it.
        cmd += ["--pages", ",".join(str(p) for p in pages)]
        cmd += ["--redo-ocr"]
    else:
        cmd += ["--skip-text"]
    cmd += [str(src), str(dest)]

    log.info("מריץ OCR על %s עמודים", len(pages) if pages else "כל ה")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=OCR_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRFailed("ה-OCR חרג ממגבלת הזמן") from exc

    if proc.returncode != 0 or not dest.exists():
        raise OCRFailed(f"ocrmypdf נכשל (קוד {proc.returncode}): {proc.stderr[-400:]}")
    return dest


def quality_estimate(path: str | Path) -> float:
    """Rough confidence in the OCR result, between 0 and 1.

    Based on how much of the extracted text is plausible Hebrew or Latin rather
    than stray symbols. Real confidence scores need Tesseract's TSV output; this
    is a cheap sanity check that catches the catastrophic cases.
    """
    doc = pymupdf.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    text = rtl.clean(text)
    if len(text) < 40:
        return 0.0
    meaningful = sum(1 for c in text if c.isalnum() or c.isspace() or c in ".,:;!?()-–—\"'")
    return meaningful / len(text)
