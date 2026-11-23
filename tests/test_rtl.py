"""בדיקות לשכבת העברית — זה המקום שבו דברים נשברים בשקט."""

from accessifier import rtl


def test_hebrew_ratio():
    assert rtl.hebrew_ratio("שלום עולם") > 0.9
    assert rtl.hebrew_ratio("hello world") == 0.0
    assert 0.0 < rtl.hebrew_ratio("שלום world") < 1.0


def test_detect_lang():
    assert rtl.detect_lang("זה משפט בעברית עם כמה מילים") == "he"
    assert rtl.detect_lang("this is an english sentence") == "en"


def test_clean_removes_bidi_controls():
    dirty = "‫שלום‬­ עולם"
    out = rtl.clean(dirty)
    assert "‫" not in out
    assert "‬" not in out
    assert "­" not in out
    assert "שלום" in out


def test_clean_is_idempotent():
    once = rtl.clean("שלום‏ עולם")
    assert rtl.clean(once) == once


def test_looks_reversed_detects_mirrored_text():
    # ההיוריסטיקה דורשת כמות טקסט סבירה, ולכן המשפט ארוך בכוונה
    good = "זה טקסט רגיל של המסמך והוא כולל כמה מילים נפוצות כדי שניתן יהיה לזהות את הכיוון"
    bad = good[::-1]
    assert not rtl.looks_reversed(good)
    assert rtl.looks_reversed(bad)


def test_repair_restores_reversed_text():
    # ההיוריסטיקה דורשת כמות טקסט סבירה, ולכן המשפט ארוך בכוונה
    good = "זה טקסט רגיל של המסמך והוא כולל כמה מילים נפוצות כדי שניתן יהיה לזהות את הכיוון"
    repaired, changed = rtl.repair(good[::-1])
    assert changed
    assert repaired == good


def test_repair_leaves_good_text_alone():
    # ההיוריסטיקה דורשת כמות טקסט סבירה, ולכן המשפט ארוך בכוונה
    good = "זה טקסט רגיל של המסמך והוא כולל כמה מילים נפוצות כדי שניתן יהיה לזהות את הכיוון"
    repaired, changed = rtl.repair(good)
    assert not changed
    assert repaired == good


def test_join_wrapped_lines():
    out = rtl.join_wrapped_lines(["שורה ראשונה", "שממשיכה כאן.", "", "פסקה חדשה"])
    assert "שורה ראשונה שממשיכה כאן." in out


def test_strip_bullet():
    text, is_item, ordered = rtl.strip_bullet("• פריט ראשון")
    assert is_item and not ordered and text == "פריט ראשון"

    text, is_item, ordered = rtl.strip_bullet("3. פריט שלישי")
    assert is_item and ordered and text == "פריט שלישי"

    text, is_item, _ = rtl.strip_bullet("סתם שורה")
    assert not is_item and text == "סתם שורה"


# ---------------------------------------------------------------------------
# קידוד מיושן (Windows-1255) וסדר חזותי
#
# הדרך לבנות דוגמה אמיתית היא להפוך את התהליך: לוקחים עברית תקינה,
# מקודדים ל-cp1255 ומפענחים כ-latin-1 — בדיוק מה שקורה בקבצים האמיתיים.
# ---------------------------------------------------------------------------


def _as_legacy(text: str) -> str:
    """מדמה טקסט עברי שנקרא בטעות כ-latin-1 במקום כ-cp1255."""
    return text.encode("cp1255").decode("latin-1")


def test_decode_legacy_round_trip():
    original = "בעל הפוליסה"
    assert rtl.decode_legacy(_as_legacy(original)) == original


def test_looks_legacy_encoded():
    original = "זה טקסט בעברית שנשמר בקידוד מיושן של חלונות ולא ב-Unicode"
    assert rtl.looks_legacy_encoded(_as_legacy(original))
    # עברית תקינה לא אמורה להיחשב מיושנת, וגם לא אנגלית רגילה
    assert not rtl.looks_legacy_encoded(original)
    assert not rtl.looks_legacy_encoded("a plain english sentence with no hebrew")


def test_reverse_visual_restores_hebrew_order():
    assert rtl.reverse_visual("םולש הז") == "זה שלום"


def test_reverse_visual_keeps_numbers_dates_and_urls():
    """מספרים, תאריכים וכתובות נשמרים בסדר קריאה גם בשורה הפוכה.

    היפוך שלהם משנה את המשמעות בשקט — מיקוד או תאריך פירעון הפוכים
    גרועים בהרבה מטקסט שלא תוקן בכלל.
    """
    assert rtl.reverse_visual("02/09/2026 :הלחתה ךיראת") == "תאריך התחלה: 02/09/2026"
    assert "www.example.co.il" in rtl.reverse_visual("www.example.co.il :רתא")
    assert rtl.reverse_visual("150,000 :םוכס") == "סכום: 150,000"


def test_reverse_visual_keeps_unit_symbols_attached():
    # 4% לא יכול להפוך ל-%4
    assert "4%" in rtl.reverse_visual("4% :תיתנש תיביר")


def test_calibrate_detects_legacy_and_visual_order():
    original = [
        "זה טקסט בעברית שנשמר בקידוד מיושן של חלונות",
        "ויש בו גם מילים נפוצות כמו של ועל ואת כדי שניתן יהיה לזהות את הכיוון",
        "וגם שורה נוספת עם מה שצריך כדי לקבל החלטה על כל המסמך",
    ]
    samples = [_as_legacy(line[::-1]) for line in original]
    policy = rtl.calibrate(samples)
    assert policy.decode and policy.reverse
    assert policy.apply(samples[0]) == original[0]


def test_calibrate_changes_nothing_on_a_healthy_document():
    samples = [
        "זה מסמך תקין לחלוטין עם עברית בסדר לוגי",
        "ואין שום סיבה לגעת בו או לשנות את התווים שלו",
    ]
    policy = rtl.calibrate(samples)
    assert not policy.active
    assert policy.apply(samples[0]) == samples[0]
