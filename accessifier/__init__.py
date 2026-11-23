"""accessifier — turn a PDF into an accessible PDF/UA file plus accessible HTML.

Built against ת"י 5568 (which follows WCAG 2.1 AA) and ISO 14289-1 (PDF/UA).

    from accessifier import process
    result = process("doc.pdf", "out/")
    print(result.level, result.manual_review_required)

The tool fixes what can be fixed mechanically and reports everything else.
It never invents alternative text for an image, and it never claims a document
is compliant: compliance is a human judgement that needs a screen-reader pass.
"""

from __future__ import annotations

import os

# PyMuPDF prints an advisory notice on stdout the first time a page is laid
# out, which corrupts --json output and anything that pipes it. Both switches
# have to be set before PyMuPDF is imported.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")
os.environ.setdefault("PYMUPDF_MESSAGE", "fd:2")

from .models import Block, BlockType, Diagnosis, Document, Finding, Level  # noqa: E402
from .pipeline import Result, process, summarise  # noqa: E402
from .triage import EncryptedPDF, diagnose  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "Block",
    "BlockType",
    "Diagnosis",
    "Document",
    "EncryptedPDF",
    "Finding",
    "Level",
    "Result",
    "diagnose",
    "process",
    "summarise",
    "__version__",
]
