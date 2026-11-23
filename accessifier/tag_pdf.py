"""Stage 4: produce a tagged PDF/UA file.

The tagged PDF is generated from the accessible HTML rather than by patching
the original file. Writing a correct structure tree into an existing content
stream means injecting marked-content operators into streams we did not author,
which is fragile and fails quietly. Rendering from semantic HTML gives a real
structure tree, a reliable reading order and correct /Lang, which is what a
screen reader actually needs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pikepdf

log = logging.getLogger(__name__)


class TaggingError(Exception):
    pass


def html_to_tagged_pdf(html: str, out_path: str | Path, title: str, lang: str = "he") -> Path:
    """Render accessible HTML to a tagged PDF/UA-1 file."""
    try:
        from weasyprint import HTML  # imported lazily: heavy dependency
    except ImportError as exc:  # pragma: no cover - environment issue
        raise TaggingError(
            "WeasyPrint אינו מותקן. התקיני: pip install weasyprint"
        ) from exc

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    HTML(string=html).write_pdf(
        out_path,
        pdf_variant="pdf/ua-1",
        uncompressed_pdf=False,
    )
    _finalise_metadata(out_path, title=title, lang=lang)
    return out_path


def _finalise_metadata(path: Path, title: str, lang: str) -> None:
    """Set the document title, language and viewer preference.

    PDF/UA requires a real title and that viewers display it instead of the
    file name, which is what a screen reader announces when the file opens.
    """
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.Lang = pikepdf.String(lang)
        prefs = pdf.Root.get("/ViewerPreferences")
        if not isinstance(prefs, pikepdf.Dictionary):
            prefs = pdf.Root.ViewerPreferences = pikepdf.Dictionary()
        prefs["/DisplayDocTitle"] = True

        mark_info = pdf.Root.get("/MarkInfo")
        if not isinstance(mark_info, pikepdf.Dictionary):
            mark_info = pdf.Root.MarkInfo = pikepdf.Dictionary()
        mark_info["/Marked"] = True

        if title:
            with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
                meta["dc:title"] = title
            pdf.docinfo["/Title"] = title
        pdf.save()


def verify_tagged(path: str | Path) -> dict[str, bool]:
    """Cheap structural check of the produced PDF. Not a substitute for VeraPDF."""
    with pikepdf.open(path) as pdf:
        root = pdf.Root
        struct = root.get("/StructTreeRoot")
        mark_info = root.get("/MarkInfo")
        prefs = root.get("/ViewerPreferences")
        try:
            with pdf.open_metadata() as meta:
                has_uaid = "pdfuaid:part" in str(meta)
                has_title = bool(str(meta.get("dc:title", "") or ""))
        except Exception:  # noqa: BLE001
            has_uaid = has_title = False

        return {
            "struct_tree": struct is not None,
            "marked": bool(mark_info and mark_info.get("/Marked")),
            "lang": "/Lang" in root,
            "display_doc_title": bool(prefs and prefs.get("/DisplayDocTitle")),
            "xmp_pdfua": has_uaid,
            "title": has_title or bool(pdf.docinfo.get("/Title")),
        }


def structure_summary(path: str | Path, limit: int = 40) -> list[str]:
    """Flat list of structure element types, for the report and for tests."""
    out: list[str] = []

    def walk(element: pikepdf.Object, depth: int = 0) -> None:
        if len(out) >= limit or depth > 12:
            return
        if isinstance(element, pikepdf.Dictionary):
            tag = element.get("/S")
            if tag is not None:
                out.append(str(tag).lstrip("/"))
            kids = element.get("/K")
            if kids is not None:
                walk(kids, depth + 1)
        elif isinstance(element, pikepdf.Array):
            for kid in element:
                walk(kid, depth + 1)

    with pikepdf.open(path) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if root is not None:
            walk(root)
    return out
