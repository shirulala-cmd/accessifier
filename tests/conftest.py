"""Hebrew fixture PDFs.

Built with WeasyPrint from HTML, which gives us real Hebrew text in logical
order — exactly what a well-made source document looks like. We then strip the
structure tree off, so the fixtures resemble the untagged PDFs the tool is
actually given.
"""

from __future__ import annotations

import pytest

FONT_STACK = '"DejaVu Sans", "Rubik", "Heebo", sans-serif'

PLAIN_HTML = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>מסמך בדיקה</title>
<style>
  body {{ font-family: {FONT_STACK}; font-size: 11pt; direction: rtl; }}
  .title {{ font-size: 24pt; font-weight: bold; margin-bottom: 14pt; }}
  .h2 {{ font-size: 16pt; font-weight: bold; margin: 12pt 0 6pt; }}
  table {{ border-collapse: collapse; }}
  td, th {{ border: 1px solid #333; padding: 4pt 8pt; }}
  .runner {{ font-size: 8pt; color: #555; }}
</style></head>
<body>
<p class="runner">מדריך ההנגשה — עמוד 1</p>
<p class="title">נגישות מסמכים דיגיטליים</p>
<p>המסמך הזה נועד לבדוק את תהליך ההנגשה. הוא כולל כותרות, פסקאות,
רשימות וטבלה, כדי שניתן יהיה לוודא שכל סוג תוכן מזוהה כראוי.</p>
<p class="h2">מה נדרש על פי התקן</p>
<p>על פי התקן יש לוודא שלכל מסמך יש מבנה סמנטי, שפה מוגדרת וסדר קריאה
הגיוני. אלה שלושת הדברים שקורא מסך מסתמך עליהם.</p>
<p>• מבנה כותרות היררכי</p>
<p>• טקסט חלופי לכל תמונה</p>
<p>• ניגודיות צבעים מספקת</p>
<p class="h2">טבלת דוגמה</p>
<table>
  <tr><td>רמה</td><td>משמעות</td></tr>
  <tr><td>א</td><td>תיוג בלבד</td></tr>
  <tr><td>ב</td><td>שחזור מבנה</td></tr>
</table>
</body></html>
"""

LOW_CONTRAST_HTML = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8"><title>ניגודיות נמוכה</title>
<style>
  body {{ font-family: {FONT_STACK}; font-size: 11pt; direction: rtl;
          color: #bdbdbd; background: #ffffff; }}
</style></head>
<body>
<p>הטקסט הזה בצבע אפור בהיר על רקע לבן, ולכן הניגודיות שלו נמוכה מהנדרש
בתקן. מסמך כזה אמור להיות מסווג לרמה המחייבת בנייה מחדש של העיצוב,
כי אי אפשר לתקן ניגודיות בלי לשנות את הצבעים.</p>
<p>פסקה נוספת באותו הצבע, כדי שיהיה מספיק טקסט לבדיקה אמיתית של
אחוזי הכיסוי בעמוד ולא רק של שורה בודדת.</p>
</body></html>
"""


def _render(html: str, path, strip_tags: bool = True):
    from weasyprint import HTML

    HTML(string=html).write_pdf(path)
    if strip_tags:
        _untag(path)
    return path


def _untag(path) -> None:
    """Remove the structure tree, so the fixture looks like an ordinary PDF."""
    import pikepdf

    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for key in ("/StructTreeRoot", "/MarkInfo", "/Lang", "/ViewerPreferences"):
            if key in pdf.Root:
                del pdf.Root[key]
        pdf.save()


@pytest.fixture(scope="session")
def plain_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("fixtures") / "plain-he.pdf"
    return _render(PLAIN_HTML, path)


@pytest.fixture(scope="session")
def low_contrast_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("fixtures") / "low-contrast-he.pdf"
    return _render(LOW_CONTRAST_HTML, path)


@pytest.fixture(scope="session")
def scanned_pdf(tmp_path_factory):
    """A page that is one big image with no text layer."""
    import pymupdf

    path = tmp_path_factory.mktemp("fixtures") / "scanned.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 850))
    pix.set_rect(pix.irect, (230, 230, 230))
    page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    doc.close()
    return path
