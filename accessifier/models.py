"""Data model for the document pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Level(str, Enum):
    """Remediation level decided during triage."""

    A = "A"  # healthy document: tag in place, keep the design
    B = "B"  # structure must be reconstructed, design mostly preserved
    C = "C"  # too damaged: rebuild with an accessible design

    @property
    def hebrew(self) -> str:
        return {
            "A": "מסמך תקין — תיוג בלבד",
            "B": "שחזור מבנה",
            "C": "בנייה מחדש עם עיצוב נגיש",
        }[self.value]


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    ARTIFACT = "artifact"  # running header/footer/page number: not read aloud


@dataclass
class Link:
    text: str
    uri: str


@dataclass
class Block:
    """One semantic unit of the document."""

    type: BlockType
    page: int
    bbox: tuple[float, float, float, float]
    text: str = ""
    lang: str = "he"

    # heading
    level: int = 0
    # list
    items: list[str] = field(default_factory=list)
    ordered: bool = False
    # table: rows[0] is treated as the header row when has_header is True
    rows: list[list[str]] = field(default_factory=list)
    has_header: bool = False
    # figure
    image_b64: str | None = None
    image_mime: str = "image/png"
    alt: str | None = None
    caption: str | None = None

    links: list[Link] = field(default_factory=list)

    @property
    def needs_alt(self) -> bool:
        return self.type is BlockType.FIGURE and not self.alt


@dataclass
class Finding:
    """One accessibility issue, for the report."""

    code: str
    message: str
    wcag: str = ""
    page: int | None = None
    fixed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "wcag": self.wcag,
            "page": self.page,
            "fixed": self.fixed,
        }


@dataclass
class Diagnosis:
    """Result of triage."""

    level: Level
    reasons: list[str] = field(default_factory=list)
    pages: int = 0
    chars_per_page: float = 0.0
    scanned_pages: list[int] = field(default_factory=list)
    has_struct_tree: bool = False
    fonts_without_unicode: list[str] = field(default_factory=list)
    max_columns: int = 1
    encrypted: bool = False
    low_contrast_pages: list[int] = field(default_factory=list)
    title: str = ""
    legacy_encoding: bool = False  # Hebrew stored as cp1255 with no Unicode map

    @property
    def needs_ocr(self) -> bool:
        return bool(self.scanned_pages)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "לא נמצאו בעיות מבניות"

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "level_hebrew": self.level.hebrew,
            "reasons": self.reasons,
            "pages": self.pages,
            "chars_per_page": round(self.chars_per_page, 1),
            "scanned_pages": self.scanned_pages,
            "has_struct_tree": self.has_struct_tree,
            "fonts_without_unicode": self.fonts_without_unicode,
            "max_columns": self.max_columns,
            "low_contrast_pages": self.low_contrast_pages,
            "legacy_encoding": self.legacy_encoding,
            "needs_ocr": self.needs_ocr,
            "title": self.title,
        }


@dataclass
class Document:
    """The extracted, structured document."""

    title: str = ""
    lang: str = "he"
    blocks: list[Block] = field(default_factory=list)
    diagnosis: Diagnosis | None = None
    findings: list[Finding] = field(default_factory=list)
    source_path: Any = None

    def add_finding(self, *a: Any, **kw: Any) -> None:
        """Accepts either a ready Finding or the arguments to build one."""
        if len(a) == 1 and not kw and isinstance(a[0], Finding):
            self.findings.append(a[0])
            return
        self.findings.append(Finding(*a, **kw))

    @property
    def content_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.type is not BlockType.ARTIFACT]

    @property
    def headings(self) -> list[Block]:
        return [b for b in self.content_blocks if b.type is BlockType.HEADING]

    @property
    def manual_review_required(self) -> bool:
        return any(not f.fixed for f in self.findings)
