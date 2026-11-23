"""Stage 2: turn page layout into a semantic document model."""

from __future__ import annotations

import base64
import collections
import logging
import re
from pathlib import Path

import pymupdf

from . import rtl
from .models import Block, BlockType, Diagnosis, Document, Link

log = logging.getLogger(__name__)

HEADER_BAND = 0.18  # top/bottom 18% of the page
ARTIFACT_MAX_CHARS = 90
HEADING_MAX_CHARS = 120
# A heading names a section; past this many words it is a sentence.
HEADING_MAX_WORDS = 8
# Above this share of headings the style signal is noise, not structure.
HEADING_RATIO_LIMIT = 0.35
CAPTION_RE = re.compile(r"^\s*(תמונה|איור|תרשים|גרף|לוח|טבלה)\s*\d*[:.–-]?\s*(.*)$")


def _spans(block: dict) -> list[dict]:
    return [s for line in block.get("lines", []) for s in line.get("spans", [])]


def _size_histogram(pages: list[dict]) -> collections.Counter[float]:
    """Font sizes weighted by character count. Rounded once, used everywhere."""
    counter: collections.Counter[float] = collections.Counter()
    for page in pages:
        for block in page.get("blocks", []):
            for span in _spans(block):
                text = span.get("text", "").strip()
                if text:
                    counter[round(span["size"], 1)] += len(text)
    return counter


def _body_size(pages: list[dict]) -> float:
    """Most common font size weighted by character count — the body text size."""
    counter = _size_histogram(pages)
    return counter.most_common(1)[0][0] if counter else 11.0


BOLD_NAMES = ("bold", "black", "heavy", "semib", "demi", " bd", "-bd", "bd.")


def _is_bold(span: dict) -> bool:
    """Bold by flag or by font name.

    The flag is unset in plenty of real documents, and Hebrew foundries abbreviate
    the weight ("IDI Prop. Bd."), so the name has to be checked too.
    """
    if span.get("flags", 0) & 2 ** 4:
        return True
    name = span.get("font", "").lower()
    return any(token in name for token in BOLD_NAMES)


def _block_text(block: dict) -> str:
    lines = []
    for line in block.get("lines", []):
        lines.append("".join(s.get("text", "") for s in line.get("spans", [])))
    return "\n".join(lines)


def _artifact_key(text: str) -> str:
    """Page numbers and dates differ per page, so normalise digits away."""
    return re.sub(r"\d+", "#", rtl.clean(text))


# Pagination, in the forms that actually turn up in Hebrew documents.
PAGINATION_RE = re.compile(
    r"""^\s*(?:
        [-–—]?\s*\d+\s*[-–—]?                      # 3   -3-
      | (?:עמ(?:וד)?[.'׳]?\s*)?\d+\s*(?:מ|מתוך)\s*\d+ # עמוד 1 מתוך 2   1 מתוך 2
      | עמ(?:וד)?[.'׳]?\s*\d+                         # עמוד 4
      | page\s*\d+(?:\s*(?:of|/)\s*\d+)?           # Page 1 of 2
      | \d+\s*/\s*\d+                              # 1/2
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_pagination(text: str) -> bool:
    """True for a page number in any of its usual shapes.

    Pagination is an artifact of the printed page: it carries no meaning in a
    continuous HTML document and PDF/UA expects it marked as an artifact rather
    than read aloud. It is recognised by shape rather than by repetition,
    because a two-page document never repeats anything often enough to be
    caught by the repetition test.
    """
    return bool(PAGINATION_RE.match(rtl.clean(text)))


def _repeating_artifacts(pages: list[dict], page_heights: list[float]) -> set[str]:
    """Running headers and footers: short text repeated near the page edges.

    The band has to be generous. Measured on real documents the running header
    sits well below the very top of the page — 100pt into an A4 page is common
    once a logo and a rule are above it — and a 7% band misses all of them.
    The band is still narrow enough to exclude the body text and the tables,
    and the repetition and length conditions carry most of the weight anyway.
    """
    seen: collections.Counter[str] = collections.Counter()
    banded: set[str] = set()
    for page, height in zip(pages, page_heights):
        for block in page.get("blocks", []):
            if block.get("type") != 0:
                continue
            key = _artifact_key(_block_text(block))
            if not key or len(key) > ARTIFACT_MAX_CHARS:
                continue
            seen[key] += 1
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y1 < height * HEADER_BAND or y0 > height * (1 - HEADER_BAND):
                banded.add(key)
    # A two-page document can never reach three repeats, so asking for more
    # occurrences than it has pages would disable the test entirely there.
    threshold = 3 if len(pages) >= 3 else 2
    return {k for k, v in seen.items() if v >= threshold and k in banded}


def _heading_level_map(sizes: list[float], body: float) -> dict[float, int]:
    """Map distinct heading sizes to H1..H6 with no gaps in the hierarchy.

    Sizes are rounded before comparing, because body is a rounded value too.
    Comparing a raw size against a rounded body makes every span on a
    single-size document look larger than the body text, which turns the whole
    document into headings. That happens in practice whenever the font size is
    1 and the scale lives in the text matrix.
    """
    rounded = {round(s, 1) for s in sizes}
    if len(rounded) < 2:
        return {}  # one size only: font size carries no heading information
    distinct = sorted({s for s in rounded if s > body * 1.12}, reverse=True)
    return {size: min(i + 1, 6) for i, size in enumerate(distinct[:6])}


FIELD_RE = re.compile(r":\s*\S*\d")
URL_RE = re.compile(r"^(https?://|www\.)\S+$")


def _is_field_label(text: str) -> bool:
    """A "label: value" line is data, not a section heading.

    Forms, policies and certificates set their field labels in bold, so bold
    alone would turn every field on the page into a heading and make screen
    reader heading navigation useless. A colon followed by a number is the
    reliable giveaway: "תאריך תחילה: 02/09/2026" is a value, not a section.
    """
    stripped = text.strip()
    return bool(FIELD_RE.search(stripped) or URL_RE.match(stripped))


def _is_sentence(text: str) -> bool:
    """True when a line reads as prose rather than as a section name.

    Bold is also used for emphasis inside body text, and a PDF often breaks one
    emphasised sentence into several positioned chunks. Each chunk is short and
    bold, so style alone would promote all of them. Word count and an internal
    comma catch the difference: a heading names a section in a few words and
    does not need punctuation to hold itself together.

    A heading wrongly left as a paragraph only loses one navigation stop; a
    paragraph wrongly promoted pollutes the heading list for every user of it,
    so the check deliberately errs towards paragraph.
    """
    stripped = text.strip()
    if len(stripped.split()) > HEADING_MAX_WORDS:
        return True
    return any(mark in stripped for mark in (",", ";", "؛"))


def _extract_links(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    out = []
    for link in page.get_links():
        uri = link.get("uri")
        if uri:
            out.append((pymupdf.Rect(link["from"]), uri))
    return out


def _looks_like_caption(text: str) -> bool:
    """Is this line safe to treat as the caption of the image above it?

    Proximity alone is not evidence. A logo at the foot of a page sits directly
    above the page number, and "עמוד 1 מתוך 2" adopted as alt text makes a
    screen reader announce the logo as "page 1 of 2" — worse than silence,
    because it is confidently wrong and it suppresses the report entry that
    would have asked a human to write a real description.

    An explicit "תמונה/איור/תרשים" prefix is accepted outright. Anything else
    has to read like a description: at least two words, no pagination, no
    "label: value" field, no bare URL.
    """
    text = text.strip()
    if not text:
        return False
    if CAPTION_RE.match(text) and CAPTION_RE.match(text).group(2).strip():
        return True
    if _is_pagination(text) or _is_field_label(text):
        return False
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    return len(words) >= 2 and len(text) <= 120


def _figure_alt(caption: str | None) -> str | None:
    if not caption:
        return None
    m = CAPTION_RE.match(caption)
    if m and m.group(2).strip():
        return m.group(2).strip()
    return caption.strip() or None


def _sort_reading_order(blocks: list[Block], page_width: float, columns: int) -> list[Block]:
    """Right-to-left reading order.

    Single column: top to bottom. Two columns: the right column first, then the
    left, because Hebrew reads right to left.
    """
    if columns < 2:
        return sorted(blocks, key=lambda b: (b.page, round(b.bbox[1], 1), -b.bbox[2]))

    def column_of(b: Block) -> int:
        centre = (b.bbox[0] + b.bbox[2]) / 2
        return 0 if centre > page_width / 2 else 1  # right column sorts first

    return sorted(blocks, key=lambda b: (b.page, column_of(b), round(b.bbox[1], 1)))


def _fix(text: str, policy: rtl.TextPolicy) -> str:
    """Normalise, then apply the document-wide encoding and order repair."""
    return rtl.clean(policy.apply(rtl.clean(text)))


def _block_lines(raw: dict, policy: rtl.TextPolicy) -> list[str]:
    """The block's text lines, repaired and in logical reading order.

    A visual-order PDF does not only store characters backwards. It also emits
    each word as its own positioned chunk, left to right, so repairing the
    characters alone leaves the words in the wrong order. The chunks therefore
    have to be grouped back into visual rows and re-read right to left.
    """
    raw_lines = raw.get("lines", [])
    chunks = [
        (line.get("bbox", (0, 0, 0, 0)), "".join(s.get("text", "") for s in line.get("spans", [])))
        for line in raw_lines
    ]
    if not policy.reverse:
        return [_fix(text, policy) for _, text in chunks]

    heights = [b[3] - b[1] for b, t in chunks if t.strip()] or [10.0]
    tolerance = max(1.0, sorted(heights)[len(heights) // 2] * 0.6)

    items = sorted(
        (((b[1] + b[3]) / 2, b[0], _fix(t, policy)) for b, t in chunks if t.strip()),
        key=lambda it: it[0],
    )
    rows: list[list[tuple[float, float, str]]] = []
    for item in items:
        if rows and abs(item[0] - rows[-1][0][0]) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])

    # Each chunk is already repaired; only the order between chunks is left.
    return [" ".join(t for _, _, t in sorted(row, key=lambda it: -it[1])) for row in rows]


def _cell_text(page: pymupdf.Page, cell: tuple | None, policy: rtl.TextPolicy) -> str:
    """Read one table cell through the bidi-aware text extractor.

    PyMuPDF's table extractor returns cell text in raw glyph order, with no
    bidi reordering, so Hebrew comes out backwards ("טור ןולא" for "אלון רוט")
    even in a document whose paragraphs extract perfectly. Re-reading the cell
    from its own rectangle with get_text goes through the same path the rest of
    the document uses, which keeps the two consistent.
    """
    if cell is None:
        return ""
    rect = pymupdf.Rect(cell)
    if rect.is_empty or rect.is_infinite:
        return ""
    # The rectangle is used exactly as given. Padding it to catch a glyph that
    # a border might clip also reaches into the neighbouring column, and the
    # columns of a numeric table touch: one point of slack appended a stray
    # digit to every amount and every date ("137,549 0", "09/2027 9"). A
    # silently wrong number is far worse than a missing hairline of a letter.
    raw = page.get_text("text", clip=rect)
    lines = [ln for ln in (rtl.clean(ln) for ln in raw.splitlines()) if ln]
    return _fix(" ".join(lines), policy)


def _tables_on_page(
    page: pymupdf.Page, policy: rtl.TextPolicy
) -> list[tuple[pymupdf.Rect, list[list[str]]]]:
    try:
        found = page.find_tables()
    except Exception as exc:  # noqa: BLE001 - table finding is best effort
        log.debug("זיהוי טבלאות נכשל בעמוד %s: %s", page.number, exc)
        return []
    out = []
    for table in found:
        rows = []
        for row in table.rows:
            cells = [_cell_text(page, c, policy) for c in row.cells]
            if any(cells):
                rows.append(cells)
        if len(rows) >= 2:
            out.append((pymupdf.Rect(table.bbox), rows))
    return out


def _calibrate(pages: list[dict]) -> rtl.TextPolicy:
    """Decide once, for the whole file, how its text must be repaired."""
    samples = [
        "".join(s.get("text", "") for s in line.get("spans", []))
        for page in pages
        for block in page.get("blocks", [])
        for line in block.get("lines", [])
    ]
    policy = rtl.calibrate(samples)
    if policy.active:
        log.info("זוהה קידוד/סדר בעייתי במסמך: %s", policy)
    return policy


def extract(path: str | Path, diagnosis: Diagnosis) -> Document:
    """Build a Document from a PDF that already has a usable text layer."""
    path = Path(path)
    doc = pymupdf.open(path)
    repaired_lines = 0
    try:
        pages = [p.get_text("dict") for p in doc]
        heights = [p.rect.height for p in doc]
        widths = [p.rect.width for p in doc]
        policy = _calibrate(pages)
        body = _body_size(pages)
        artifacts = _repeating_artifacts(pages, heights)

        all_sizes = [
            s["size"]
            for page in pages
            for block in page.get("blocks", [])
            for s in _spans(block)
            if s.get("text", "").strip()
        ]
        level_map = _heading_level_map(all_sizes, body)

        blocks: list[Block] = []
        seen_artifacts: collections.Counter[str] = collections.Counter()
        for index, page in enumerate(doc):
            page_dict = pages[index]
            page_no = index + 1
            links = _extract_links(page)
            tables = _tables_on_page(page, policy)
            table_rects = [r for r, _ in tables]

            for rect, rows in tables:
                blocks.append(
                    Block(
                        type=BlockType.TABLE,
                        page=page_no,
                        bbox=tuple(rect),
                        rows=rows,
                        has_header=True,
                        lang=rtl.detect_lang(" ".join(rows[0])),
                    )
                )

            pending_figure: Block | None = None

            for raw in page_dict.get("blocks", []):
                bbox = tuple(raw["bbox"])
                rect = pymupdf.Rect(bbox)

                if raw.get("type") == 1:
                    data = raw.get("image")
                    if not data:
                        continue
                    fig = Block(
                        type=BlockType.FIGURE,
                        page=page_no,
                        bbox=bbox,
                        image_b64=base64.b64encode(data).decode("ascii"),
                        image_mime=f"image/{raw.get('ext', 'png')}",
                    )
                    blocks.append(fig)
                    pending_figure = fig
                    continue

                # Skip text that belongs to a table we already captured.
                if any(rect.intersects(tr) and rect.get_area() < tr.get_area() for tr in table_rects):
                    continue

                text = _block_text(raw)

                # Pagination is an artifact by its shape, not by repetition.
                if _is_pagination(text):
                    blocks.append(
                        Block(
                            type=BlockType.ARTIFACT,
                            page=page_no,
                            bbox=bbox,
                            text=_fix(text, policy),
                        )
                    )
                    continue

                key = _artifact_key(text)
                if key in artifacts:
                    # The output HTML is one continuous document, so a running
                    # header read on every page is pure noise. Keep the first
                    # occurrence, because it may carry real information such as
                    # a name or a policy number, and silence the repeats.
                    seen_artifacts[key] += 1
                    if seen_artifacts[key] > 1:
                        blocks.append(
                            Block(
                                type=BlockType.ARTIFACT,
                                page=page_no,
                                bbox=bbox,
                                text=_fix(text, policy),
                            )
                        )
                        continue

                lines = [ln for ln in _block_lines(raw, policy) if ln]
                if not lines:
                    continue

                if not policy.active:
                    # No document-wide problem, so look for individual reversed lines.
                    fixed_lines = []
                    for ln in lines:
                        repaired, changed = rtl.repair(ln)
                        repaired_lines += int(changed)
                        fixed_lines.append(repaired)
                    lines = fixed_lines

                # A line right under an image may be its caption — but only if
                # it reads like a description. See _looks_like_caption.
                if pending_figure and len(lines) <= 2 and rect.y0 >= pending_figure.bbox[3] - 2:
                    caption = " ".join(lines)
                    if _looks_like_caption(caption):
                        pending_figure.caption = caption
                        pending_figure.alt = _figure_alt(caption)
                        pending_figure = None
                        continue
                pending_figure = None

                spans = _spans(raw)
                max_size = max((s["size"] for s in spans), default=body)
                bold = any(_is_bold(s) for s in spans)
                joined = " ".join(lines)
                lang = rtl.detect_lang(joined)

                # List?
                stripped = [rtl.strip_bullet(ln) for ln in lines]
                if sum(1 for _, is_item, _ in stripped if is_item) >= max(2, len(lines) // 2):
                    items = [t for t, is_item, _ in stripped if is_item and t]
                    ordered = any(o for _, is_item, o in stripped if is_item)
                    blocks.append(
                        Block(
                            type=BlockType.LIST,
                            page=page_no,
                            bbox=bbox,
                            items=items,
                            ordered=ordered,
                            lang=lang,
                        )
                    )
                    continue

                # Heading? A heading is short by definition: a long run of text
                # is a paragraph however it is styled. A "label: value" line is
                # excluded however short or bold it is, because it is data.
                short = (
                    len(joined) <= HEADING_MAX_CHARS
                    and len(lines) <= 2
                    and not _is_field_label(joined)
                    and not _is_sentence(joined)
                )
                rounded = round(max_size, 1)
                level = level_map.get(rounded) if short else None
                if level is None and bold and short:
                    level = min(len(level_map) + 1, 6) if level_map else 2
                if level and not joined.endswith("."):
                    blocks.append(
                        Block(
                            type=BlockType.HEADING,
                            page=page_no,
                            bbox=bbox,
                            text=joined,
                            level=level,
                            lang=lang,
                        )
                    )
                    continue

                block = Block(
                    type=BlockType.PARAGRAPH,
                    page=page_no,
                    bbox=bbox,
                    text=rtl.join_wrapped_lines(lines),
                    lang=lang,
                )
                for lrect, uri in links:
                    if rect.intersects(lrect):
                        block.links.append(Link(text=block.text[:80], uri=uri))
                blocks.append(block)

        width = widths[0] if widths else 595.0
        ordered_blocks = _sort_reading_order(blocks, width, diagnosis.max_columns)
    finally:
        doc.close()

    document = Document(
        title=diagnosis.title or path.stem,
        lang="he",
        blocks=ordered_blocks,
        diagnosis=diagnosis,
    )
    _demote_excess_headings(document)
    _normalise_heading_hierarchy(document)

    if policy.decode:
        document.add_finding(
            "LEGACY_ENCODING",
            "המסמך קודד בעברית מיושנת (Windows-1255) ללא מיפוי Unicode תקין. "
            "הטקסט פוענח מחדש — מומלץ לעבור על המסמך ולוודא שהתוכן נכון.",
            wcag="1.3.1",
            fixed=True,
        )
    if policy.reverse:
        document.add_finding(
            "VISUAL_ORDER",
            "התווים במסמך נשמרו בסדר חזותי (הפוך) ולא בסדר לוגי. "
            "סדר הקריאה שוחזר; מספרים, תאריכים וכתובות אינטרנט נשמרו כפי שהיו.",
            wcag="1.3.2",
            fixed=True,
        )
    if repaired_lines:
        document.add_finding(
            "RTL_ORDER_REPAIRED",
            f"{repaired_lines} שורות בעברית חולצו בסדר הפוך ותוקנו אוטומטית. מומלץ לוודא אותן.",
            wcag="1.3.2",
            fixed=True,
        )
    log.info(
        "חולצו %s בלוקים (%s כותרות, %s טבלאות, %s תמונות)",
        len(document.content_blocks),
        len(document.headings),
        sum(1 for b in document.blocks if b.type is BlockType.TABLE),
        sum(1 for b in document.blocks if b.type is BlockType.FIGURE),
    )
    return document


def _demote_excess_headings(document: Document) -> None:
    """Turn headings back into paragraphs when almost everything looks like one.

    Styling is only evidence of structure while it is used sparingly. Once a
    third of the document is marked up as headings, the navigation it produces
    is worse than none: pressing H in a screen reader would step through every
    line. In that case keep only the largest, most prominent lines.
    """
    textual = [
        b
        for b in document.content_blocks
        if b.type in (BlockType.HEADING, BlockType.PARAGRAPH)
    ]
    headings = [b for b in textual if b.type is BlockType.HEADING]
    if not textual or len(headings) / len(textual) <= HEADING_RATIO_LIMIT:
        return

    best = min(b.level for b in headings)
    demoted = 0
    for block in headings:
        if block.level > best:
            block.type = BlockType.PARAGRAPH
            block.level = 0
            demoted += 1
    log.info("הורדו %s כותרות שגויות לפסקאות", demoted)
    if demoted:
        document.add_finding(
            "HEADINGS_UNRELIABLE",
            f"{demoted} שורות סומנו בעיצוב ככותרות אך הן כנראה טקסט רגיל, "
            "ולכן הוחזרו לפסקאות. מבנה הכותרות במסמך המקורי אינו אמין — "
            "כדאי לעבור על המסמך ולסמן את הכותרות האמיתיות ידנית.",
            wcag="1.3.1",
            fixed=False,
        )


def _normalise_heading_hierarchy(document: Document) -> None:
    """Close gaps such as H1 followed by H3, which fails WCAG 1.3.1."""
    previous = 0
    changed = 0
    for block in document.content_blocks:
        if block.type is not BlockType.HEADING:
            continue
        if previous and block.level > previous + 1:
            block.level = previous + 1
            changed += 1
        previous = block.level
    if changed:
        document.add_finding(
            "HEADING_GAP_FIXED",
            f"תוקנו {changed} דילוגים בהיררכיית הכותרות (למשל H1 ואחריו H3).",
            wcag="1.3.1",
            fixed=True,
        )
