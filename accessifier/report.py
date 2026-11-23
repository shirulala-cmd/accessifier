"""Stage 6: the accessibility report. Hebrew, RTL, and accessible itself."""

from __future__ import annotations

import html
from datetime import datetime

from .models import BlockType, Document, Level

CSS = """
:root { --ink:#141414; --muted:#4a4a4a; --line:#c9c4bc; --bg:#faf9f7; --accent:#7a3b1d;
        --ok:#1f5c34; --warn:#8a5a00; }
body { margin:0; background:var(--bg); color:var(--ink);
  font-family:"Rubik","Heebo","Assistant","DejaVu Sans",Arial,sans-serif; line-height:1.6; }
.page { max-width:48rem; margin:0 auto; padding:2rem 1.25rem 4rem; }
h1 { font-size:1.7rem; margin:0 0 .25rem; }
h2 { font-size:1.3rem; margin:2rem 0 .6rem; border-bottom:2px solid var(--line);
     padding-bottom:.3rem; }
.sub { color:var(--muted); margin:0 0 1.5rem; }
.level { border:2px solid var(--accent); padding:1rem 1.25rem; margin:1.5rem 0; background:#fff; }
.level p { margin:.35rem 0; }
table { border-collapse:collapse; width:100%; margin:1rem 0; background:#fff; }
caption { text-align:right; font-weight:600; padding-bottom:.5rem; }
th, td { border:1px solid var(--line); padding:.55rem .7rem; text-align:right;
         vertical-align:top; }
th { background:#efece6; }
.ok { color:var(--ok); font-weight:600; }
.todo { color:var(--warn); font-weight:600; }
a { color:var(--accent); }
a:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
ul { padding-inline-start:1.4rem; }
"""


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def render(document: Document, pdf_checks: dict[str, bool], verapdf: dict | None = None) -> str:
    diag = document.diagnosis
    fixed = [f for f in document.findings if f.fixed]
    todo = [f for f in document.findings if not f.fixed]

    counts = {
        "כותרות": len(document.headings),
        "פסקאות": sum(1 for b in document.content_blocks if b.type is BlockType.PARAGRAPH),
        "רשימות": sum(1 for b in document.content_blocks if b.type is BlockType.LIST),
        "טבלאות": sum(1 for b in document.content_blocks if b.type is BlockType.TABLE),
        "תמונות": sum(1 for b in document.content_blocks if b.type is BlockType.FIGURE),
        "אלמנטים שסומנו כקישוט (Artifact)": sum(
            1 for b in document.blocks if b.type is BlockType.ARTIFACT
        ),
    }

    level_note = ""
    if diag and diag.level is Level.C:
        level_note = (
            "<p><strong>שימי לב:</strong> המסמך סווג לרמה C, ולכן העיצוב שלו "
            "<strong>שונה</strong> כדי לאפשר נגישות. התוכן נשמר במלואו, אך הפריסה, "
            "הגופן והצבעים אינם זהים למקור.</p>"
        )

    rows_fixed = "".join(
        f"<tr><td>{_esc(f.message)}</td><td>{_esc(f.wcag or '—')}</td></tr>" for f in fixed
    ) or '<tr><td colspan="2">לא נדרשו תיקונים אוטומטיים</td></tr>'

    rows_todo = "".join(
        f"<tr><td>{_esc(f.message)}</td><td>{_esc(f.wcag or '—')}</td></tr>" for f in todo
    ) or '<tr><td colspan="2">לא נותרו פריטים לטיפול ידני שזוהו אוטומטית</td></tr>'

    rows_counts = "".join(
        f"<tr><th scope='row'>{_esc(k)}</th><td>{v}</td></tr>" for k, v in counts.items()
    )

    pdf_labels = {
        "struct_tree": "עץ תיוג (StructTreeRoot)",
        "marked": "מסומן כ-Tagged PDF",
        "lang": "שפת מסמך מוגדרת",
        "display_doc_title": "הצגת כותרת המסמך",
        "title": "כותרת במטא-דאטה",
        "xmp_pdfua": "סימון PDF/UA ב-XMP",
    }
    rows_pdf = "".join(
        f"<tr><th scope='row'>{_esc(label)}</th>"
        f"<td class='{'ok' if pdf_checks.get(key) else 'todo'}'>"
        f"{'תקין' if pdf_checks.get(key) else 'חסר'}</td></tr>"
        for key, label in pdf_labels.items()
    )

    vera = ""
    if verapdf is None:
        vera = (
            "<p>VeraPDF לא הורץ (אינו מותקן בסביבה). הבדיקות שלמעלה הן בדיקות מבנה "
            "פנימיות ואינן תחליף לבדיקת תאימות PDF/UA רשמית.</p>"
        )
    else:
        status = "עבר" if verapdf.get("passed") else "נכשל"
        cls = "ok" if verapdf.get("passed") else "todo"
        vera = f"<p>תוצאת VeraPDF: <span class='{cls}'>{status}</span></p>"

    reasons = (
        "".join(f"<li>{_esc(r)}</li>" for r in diag.reasons)
        if diag and diag.reasons
        else "<li>לא נמצאו בעיות מבניות</li>"
    )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>דוח נגישות — {_esc(document.title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
<header>
  <h1>דוח נגישות</h1>
  <p class="sub">{_esc(document.title)} · נוצר ב-{datetime.now():%d/%m/%Y %H:%M}</p>
</header>
<main>

<section class="level" aria-labelledby="lvl">
  <h2 id="lvl" style="border:0;margin-top:0">סיווג המסמך</h2>
  <p><strong>רמה {_esc(diag.level.value) if diag else '—'}</strong> —
     {_esc(diag.level.hebrew) if diag else ''}</p>
  <p>מספר עמודים: {_esc(diag.pages) if diag else '—'}</p>
  <p>הסיבות לסיווג:</p>
  <ul>{reasons}</ul>
  {level_note}
</section>

<section aria-labelledby="fixed-h">
  <h2 id="fixed-h">מה תוקן אוטומטית</h2>
  <table>
    <caption>תיקונים שבוצעו על ידי הכלי</caption>
    <thead><tr><th scope="col">התיקון</th><th scope="col">קריטריון WCAG</th></tr></thead>
    <tbody>{rows_fixed}</tbody>
  </table>
</section>

<section aria-labelledby="todo-h">
  <h2 id="todo-h">מה דורש התערבות אנושית</h2>
  <table>
    <caption>פריטים שלא ניתן לתקן אוטומטית</caption>
    <thead><tr><th scope="col">הפריט</th><th scope="col">קריטריון WCAG</th></tr></thead>
    <tbody>{rows_todo}</tbody>
  </table>
</section>

<section aria-labelledby="struct-h">
  <h2 id="struct-h">מה חולץ מהמסמך</h2>
  <table>
    <caption>ספירת אלמנטים סמנטיים</caption>
    <thead><tr><th scope="col">סוג</th><th scope="col">כמות</th></tr></thead>
    <tbody>{rows_counts}</tbody>
  </table>
</section>

<section aria-labelledby="pdf-h">
  <h2 id="pdf-h">בדיקת ה-PDF שנוצר</h2>
  <table>
    <caption>בדיקות מבנה על קובץ הפלט</caption>
    <thead><tr><th scope="col">הבדיקה</th><th scope="col">תוצאה</th></tr></thead>
    <tbody>{rows_pdf}</tbody>
  </table>
  {vera}
</section>

<section aria-labelledby="next-h">
  <h2 id="next-h">מה לבדוק ידנית</h2>
  <ul>
    <li>פתחי את גרסת ה-HTML ב-NVDA ועברי על הכותרות במקש H — הסדר צריך להיות הגיוני.</li>
    <li>בדקי שטבלאות נקראות עם שמות העמודות, ולא כרצף תאים.</li>
    <li>ודאי שכל תמונה שסומנה כ"דרוש תיאור חלופי" קיבלה תיאור.</li>
    <li>השוויחי את התוצר למקור וודאי שלא אבד תוכן.</li>
  </ul>
  <p>גרסת ה-HTML נקראת בדרך כלל טוב יותר מה-PDF בקוראי מסך, גם כש-PDF מתוייג כראוי.</p>
</section>

</main>
</div>
</body>
</html>
"""
