"""Stage 3: render the document model as accessible, RTL HTML.

This file is both a deliverable in its own right and the source the tagged PDF
is produced from, so its semantics decide the quality of both outputs.
"""

from __future__ import annotations

import html
import re

from .models import Block, BlockType, Document

MISSING_ALT = "דרוש תיאור חלופי לתמונה זו"

CSS = """
:root { --ink:#141414; --muted:#4a4a4a; --line:#c9c4bc; --bg:#ffffff; --accent:#7a3b1d; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font-family:"Rubik","Heebo","Assistant","DejaVu Sans",Arial,sans-serif;
  font-size:1rem; line-height:1.5; }
.page { max-width:46rem; margin:0 auto; padding:2rem 1.25rem 4rem; }
a { color:var(--accent); }
a:focus-visible, button:focus-visible, [tabindex]:focus-visible {
  outline:3px solid var(--accent); outline-offset:3px; }
.skip-link { position:absolute; right:-9999px; top:0; background:var(--accent); color:#fff;
  padding:.6rem 1rem; z-index:10; }
.skip-link:focus { right:1rem; position:fixed; }
h1 { font-size:1.75rem; line-height:1.3; margin:0 0 1rem; }
h2 { font-size:1.4rem; margin:2rem 0 .6rem; }
h3 { font-size:1.2rem; margin:1.6rem 0 .5rem; }
h4,h5,h6 { font-size:1.05rem; margin:1.3rem 0 .4rem; }
p { margin:0 0 1.125rem; }
ul, ol { margin:0 0 1.125rem; padding-inline-start:1.5rem; }
li { margin-bottom:.4rem; }
table { border-collapse:collapse; width:100%; margin:1.25rem 0; }
caption { text-align:right; font-weight:600; padding-bottom:.5rem; }
th, td { border:1px solid var(--line); padding:.55rem .7rem; text-align:right; vertical-align:top; }
th { background:#f0ede8; font-weight:600; }
figure { margin:1.5rem 0; }
figure img { max-width:100%; height:auto; }
figcaption { color:var(--muted); font-size:.95rem; margin-top:.4rem; }
nav.toc { border:1px solid var(--line); padding:1rem 1.25rem; margin-bottom:2rem; }
nav.toc ul { padding-inline-start:1.25rem; margin:.5rem 0 0; }
.needs-alt { border:2px dashed var(--accent); padding:.5rem; color:var(--accent);
  font-weight:600; }
@media print { .skip-link { display:none; } .page { max-width:none; } }
"""


def _slug(text: str, index: int) -> str:
    base = re.sub(r"[^\w֐-׿]+", "-", text.strip())[:40].strip("-")
    return f"s{index}-{base}" if base else f"s{index}"


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _lang_attr(block: Block, document_lang: str) -> str:
    return f' lang="{block.lang}" dir="{"rtl" if block.lang == "he" else "ltr"}"' if block.lang != document_lang else ""


def _linkify(text: str, block: Block) -> str:
    out = _esc(text)
    for link in block.links:
        # Keep the link text descriptive: never expose a bare URL as the label.
        label = _esc(link.text.strip() or "קישור")
        out = out.replace(label, f'<a href="{_esc(link.uri)}">{label}</a>', 1)
    return out


def _render_block(block: Block, document: Document, anchors: dict[int, str]) -> str:
    extra = _lang_attr(block, document.lang)

    if block.type is BlockType.HEADING:
        anchor = anchors.get(id(block), "")
        attr = f' id="{anchor}"' if anchor else ""
        return f"<h{block.level}{attr}{extra}>{_esc(block.text)}</h{block.level}>"

    if block.type is BlockType.PARAGRAPH:
        return f"<p{extra}>{_linkify(block.text, block)}</p>"

    if block.type is BlockType.LIST:
        tag = "ol" if block.ordered else "ul"
        items = "".join(f"<li>{_esc(i)}</li>" for i in block.items)
        return f"<{tag}{extra}>{items}</{tag}>"

    if block.type is BlockType.TABLE:
        head, *body = block.rows
        ths = "".join(f'<th scope="col">{_esc(c)}</th>' for c in head)
        trs = []
        for row in body:
            if not row:
                continue
            first, *rest = row
            cells = f'<th scope="row">{_esc(first)}</th>' + "".join(
                f"<td>{_esc(c)}</td>" for c in rest
            )
            trs.append(f"<tr>{cells}</tr>")
        caption = f"<caption>{_esc(block.caption)}</caption>" if block.caption else ""
        return (
            f"<table{extra}>{caption}<thead><tr>{ths}</tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
        )

    if block.type is BlockType.FIGURE:
        src = f"data:{block.image_mime};base64,{block.image_b64}"
        if block.alt:
            img = f'<img src="{src}" alt="{_esc(block.alt)}">'
            cap = f"<figcaption>{_esc(block.caption)}</figcaption>" if block.caption else ""
            return f"<figure>{img}{cap}</figure>"
        # No alt text available. Never invent one. An empty alt would make the
        # image decorative and hide its existence, so say plainly that a
        # description is missing: a screen reader user then knows something is
        # there and undescribed, which is the truthful state of the document.
        # PDF/UA also rejects an image with no alt, so this keeps the PDF valid.
        return (
            f'<figure><img src="{src}" alt="{MISSING_ALT}">'
            f'<figcaption class="needs-alt">{MISSING_ALT}</figcaption>'
            f"</figure>"
        )

    return ""


def render(document: Document) -> str:
    """Return a complete, self-contained accessible HTML document."""
    headings = document.headings
    anchors = {id(h): _slug(h.text, i) for i, h in enumerate(headings, start=1)}

    body_parts = []
    for block in document.content_blocks:
        rendered = _render_block(block, document, anchors)
        if rendered:
            body_parts.append(rendered)

    toc = ""
    if len(headings) >= 3:
        items = "".join(
            f'<li><a href="#{anchors[id(h)]}">{_esc(h.text)}</a></li>' for h in headings
        )
        toc = (
            '<nav class="toc" aria-labelledby="toc-title">'
            '<h2 id="toc-title">תוכן עניינים</h2>'
            f"<ul>{items}</ul></nav>"
        )

    title = _esc(document.title or "מסמך")
    # Exactly one H1 per page. If the document already supplies one, don't add
    # a second one for the title.
    header = "" if any(h.level == 1 for h in headings) else f"<header><h1>{title}</h1></header>"

    return f"""<!DOCTYPE html>
<html lang="{document.lang}" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip-link" href="#main">דלג לתוכן המרכזי</a>
<div class="page">
{header}
{toc}
<main id="main" tabindex="-1">
{chr(10).join(body_parts)}
</main>
</div>
</body>
</html>
"""
