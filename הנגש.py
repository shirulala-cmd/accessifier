#!/usr/bin/env python3
"""הנגשת כל קובצי ה-PDF בתיקייה אחת.

שימוש:

    python הנגש.py                    # מנגיש כל PDF בתיקיית "מסמכים"
    python הנגש.py ~/Downloads        # מנגיש כל PDF בתיקייה שנתנו
    python הנגש.py קובץ.pdf           # קובץ בודד

הפלט נשמר בתיקייה "פלט" ליד הקבצים, ובסוף מודפס סיכום של מה יצא ומה
דורש בדיקה ידנית.

הסקריפט הזה הוא נוחות בלבד — הוא עוטף את accessifier ולא מוסיף לו לוגיקה.
מי שמעדיף שורת פקודה יכול להריץ ישירות: python -m accessifier קובץ.pdf -o פלט/
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from accessifier import pipeline

IN_DIR = "מסמכים"
OUT_DIR = "פלט"


def collect(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    # rglob כדי שגם תיקיות משנה ייכנסו; מדלגים על פלט של ריצה קודמת
    return sorted(
        p
        for p in target.rglob("*.pdf")
        if OUT_DIR not in p.parts and not p.name.startswith(".")
    )


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for noisy in ("weasyprint", "fontTools", "PIL", "pikepdf", "ocrmypdf"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)

    here = Path(__file__).parent
    target = Path(argv[1]).expanduser() if len(argv) > 1 else here / IN_DIR

    if not target.exists():
        if target == here / IN_DIR:
            target.mkdir(parents=True)
            print(f'נוצרה התיקייה "{IN_DIR}". שימי בה קובצי PDF והריצי שוב.')
            return 0
        print(f"לא נמצא: {target}", file=sys.stderr)
        return 2

    files = collect(target)
    if not files:
        print(f"לא נמצאו קובצי PDF ב: {target}")
        return 0

    out_root = (target if target.is_dir() else target.parent) / OUT_DIR
    found = "נמצא קובץ אחד" if len(files) == 1 else f"נמצאו {len(files)} קבצים"
    print(f"{found}. הפלט ייכתב ל: {out_root}\n")

    ok, failed, manual = 0, 0, []
    for i, src in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] {src.name} ... ", end="", flush=True)
        try:
            result = pipeline.process(src, out_root / src.stem)
        except Exception as exc:  # noqa: BLE001 - קובץ אחד לא מפיל את כל האצווה
            failed += 1
            print(f"נכשל: {exc}")
            continue
        ok += 1
        level = result.level.value if result.level else "?"
        note = " — דורש בדיקה ידנית" if result.manual_review_required else ""
        print(f"רמה {level}{note}")
        if result.manual_review_required:
            manual.append(src.name)

    print(f"\nהצליחו: {ok} · נכשלו: {failed}")
    if manual:
        print("\nדורשים בדיקה ידנית (ראי את דוח הנגישות של כל קובץ):")
        for name in manual:
            print(f"  · {name}")
    # קובץ שדורש בדיקה ידנית הוא המצב הנורמלי ולא שגיאה, ולכן הוא לא מחזיר כשל
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
