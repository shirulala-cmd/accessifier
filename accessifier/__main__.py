"""Command line entry point:  python -m accessifier doc.pdf -o out/"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import pipeline, triage
from .models import Level


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="accessifier",
        description="הנגשת מסמכי PDF: PDF מתוייג (PDF/UA), HTML נגיש ודוח נגישות.",
    )
    p.add_argument("input", type=Path, help="קובץ ה-PDF להנגשה")
    p.add_argument("-o", "--out", type=Path, default=Path("out"), help="תיקיית הפלט")
    p.add_argument(
        "--force-level",
        choices=[lvl.value for lvl in Level],
        help="כפיית רמת טיפול (A/B/C) במקום האבחון האוטומטי",
    )
    p.add_argument("--diagnose-only", action="store_true", help="אבחון בלבד, בלי ליצור פלט")
    p.add_argument("--skip-ocr", action="store_true", help="לא להריץ OCR גם אם זוהו עמודים סרוקים")
    p.add_argument("--skip-pdf", action="store_true", help="לייצר HTML ודוח בלבד, בלי PDF")
    p.add_argument("--verapdf", action="store_true", help="להריץ VeraPDF אם הוא מותקן")
    p.add_argument("--json", action="store_true", help="פלט בפורמט JSON")
    p.add_argument("-q", "--quiet", action="store_true", help="להציג שגיאות בלבד")
    p.add_argument("-v", "--verbose", action="store_true", help="לוג מפורט")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    # WeasyPrint and fontTools log every layout step and every subset glyph at
    # INFO, which buries our own messages under thousands of lines.
    for noisy in ("weasyprint", "fontTools", "PIL", "pikepdf", "ocrmypdf"):
        logging.getLogger(noisy).setLevel(logging.ERROR if args.verbose else logging.CRITICAL)

    if not args.input.is_file():
        print(f"הקובץ לא נמצא: {args.input}", file=sys.stderr)
        return 2

    try:
        if args.diagnose_only:
            diagnosis = triage.diagnose(args.input)
            if args.json:
                print(json.dumps(diagnosis.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"רמה {diagnosis.level.value} — {diagnosis.level.hebrew}")
                print(f"עמודים: {diagnosis.pages}")
                for reason in diagnosis.reasons or ["לא נמצאו בעיות מבניות"]:
                    print(f"  · {reason}")
            return 0

        result = pipeline.process(
            args.input,
            args.out,
            force_level=Level(args.force_level) if args.force_level else None,
            skip_ocr=args.skip_ocr,
            skip_pdf=args.skip_pdf,
            run_verapdf=args.verapdf,
        )
    except triage.EncryptedPDF as exc:
        print(f"{exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - the CLI should not show a traceback
        logging.getLogger(__name__).debug("", exc_info=True)
        print(f"העיבוד נכשל: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_as_dict(result), ensure_ascii=False, indent=2))
    else:
        _print_human(result)

    # Exit 0 when the outputs exist. A document needing manual review is the
    # normal case, not an error, so it must not fail a build or a cron job.
    return 0 if result.html_path else 1


def _as_dict(result: pipeline.Result) -> dict:
    doc = result.document
    return {
        "source": str(result.source),
        "level": result.level.value if result.level else None,
        "diagnosis": doc.diagnosis.as_dict() if doc and doc.diagnosis else None,
        "manual_review_required": result.manual_review_required,
        "findings": [f.as_dict() for f in doc.findings] if doc else [],
        "pdf_checks": result.pdf_checks,
        "verapdf_passed": (result.verapdf or {}).get("passed"),
        "outputs": {
            "html": str(result.html_path) if result.html_path else None,
            "pdf": str(result.pdf_path) if result.pdf_path else None,
            "report": str(result.report_path) if result.report_path else None,
            "ocr": str(result.ocr_path) if result.ocr_path else None,
        },
        "errors": result.errors,
    }


def _print_human(result: pipeline.Result) -> None:
    print()
    print(pipeline.summarise(result))
    print()
    print("קבצים שנוצרו:")
    for label, path in (
        ("HTML נגיש", result.html_path),
        ("PDF מתוייג", result.pdf_path),
        ("דוח נגישות", result.report_path),
    ):
        if path:
            print(f"  {label}: {path}")
    if result.manual_review_required:
        print()
        print("יש פריטים שדורשים בדיקה ידנית — ראי את הדוח.")


if __name__ == "__main__":
    raise SystemExit(main())
