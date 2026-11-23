"""בדיקות להחלטות המבנה — מה כותרת, מה כיתוב ומה סתם מספר עמוד."""

from accessifier import structure as st
from accessifier import triage
from accessifier.models import BlockType


def test_pagination_is_recognised():
    for text in ("עמוד 1 מתוך 2", "עמ' 4", "1 מתוך 2", "- 3 -", "7", "Page 1 of 2", "1/2"):
        assert st._is_pagination(text), text


def test_pagination_does_not_swallow_content():
    for text in ("סכום הביטוח 150,000", "תנאים והרחבות", "פוליסה 1982091700", "עמודי התווך של התוכנית"):
        assert not st._is_pagination(text), text


def test_page_number_is_not_accepted_as_a_caption():
    """זה הבאג שבאמת כאב: לוגו בתחתית עמוד קיבל "עמוד 1 מתוך 2" כתיאור חלופי.

    קורא מסך היה מכריז על הלוגו כ"עמוד 1 מתוך 2" — טעות שנשמעת כמו מידע,
    ובנוסף היא מסתירה את הפריט מהדוח ואף אחד לא היה נשלח לכתוב תיאור אמיתי.
    """
    assert not st._looks_like_caption("עמוד 1 מתוך 2")
    assert not st._looks_like_caption("12")
    assert not st._looks_like_caption("תאריך: 02/09/2026")
    assert not st._looks_like_caption("https://www.example.co.il/a")
    assert not st._looks_like_caption("")


def test_real_captions_are_accepted():
    assert st._looks_like_caption("תרשים 2: התפלגות הפרמיה לפי גיל")
    assert st._looks_like_caption("גרף התפתחות יתרת ההלוואה")


def test_field_label_is_not_a_heading():
    for text in ("תאריך תחילה: 02/09/2026", "מדד בסיסי: 2583.89", "מהדורה: 1"):
        assert st._is_field_label(text), text
    assert not st._is_field_label("תנאים והרחבות")


def test_sentence_is_not_a_heading():
    long_line = "היתרה המקסימלית והפרמיה ישתנו בהתאם למועדים ולסכומים המפורטים בטבלה"
    assert st._is_sentence(long_line)
    assert st._is_sentence("שלהלן, ובתוספת הצמדה למדד")
    assert not st._is_sentence("סכומי הביטוח והפרמיות העתידיים")


def test_table_cells_keep_hebrew_in_logical_order(plain_pdf):
    """תאי טבלה נקראים דרך מחלץ הטקסט ולא דרך מחלץ הטבלאות.

    מחלץ הטבלאות של PyMuPDF מחזיר את התווים בסדר הגלִיפים, בלי bidi, ולכן
    עברית יוצאת ממנו הפוכה גם במסמך שהפסקאות שלו מחולצות בצורה מושלמת.
    """
    doc = structure_of(plain_pdf)
    tables = [b for b in doc.content_blocks if b.type is BlockType.TABLE]
    assert tables, "הפיקסטורה אמורה לכלול טבלה"
    cells = [c for t in tables for row in t.rows for c in row if c.strip()]
    assert cells
    # "כיסוי" הפוך הוא "יוסיכ" — אם הוא מופיע, ה-bidi לא הופעל
    joined = " ".join(cells)
    assert "יוסיכ" not in joined
    assert any(any("א" <= ch <= "ת" for ch in c) for c in cells)


def structure_of(path):
    return st.extract(path, triage.diagnose(path))
