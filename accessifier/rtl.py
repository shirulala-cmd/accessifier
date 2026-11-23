"""Hebrew / RTL helpers.

Character order is the single most common failure in Hebrew PDF work, so every
piece of text passes through here before it reaches the output.
"""

from __future__ import annotations

import re
import unicodedata

HEBREW = r"֐-׿יִ-ﭏ"
HEBREW_RE = re.compile(f"[{HEBREW}]")
LATIN_RE = re.compile(r"[A-Za-z]")

# Bidi control characters. They are legitimate in source documents but they
# confuse screen readers when copied blindly into the output, so we drop them
# and let the markup's dir attribute do the work.
BIDI_CONTROLS = dict.fromkeys(
    [0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]
)

# Frequent Hebrew function words. Used to tell correctly-ordered text from text
# that was extracted backwards: a reversed string scores near zero here.
COMMON_WORDS = {
    "של", "את", "על", "לא", "זה", "הוא", "היא", "אני", "יש", "אם", "כי", "גם",
    "או", "עם", "כל", "אבל", "רק", "מה", "זו", "הם", "היה", "לפי", "אחרי",
    "בין", "אל", "כמו", "עוד", "לכן", "ואת", "הזה", "ניתן", "אין", "כדי",
}

# Characters whose mirror image is a different character. If a line was
# extracted backwards these end up facing the wrong way.
MIRRORED = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<"}


def hebrew_ratio(text: str) -> float:
    """Share of letters that are Hebrew. 0 for pure Latin, 1 for pure Hebrew."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if HEBREW_RE.match(c)) / len(letters)


def detect_lang(text: str) -> str:
    """Return a BCP-47 tag for the dominant script of a string."""
    ratio = hebrew_ratio(text)
    if ratio >= 0.5:
        return "he"
    if ratio == 0.0 and LATIN_RE.search(text):
        return "en"
    return "he" if ratio > 0 else "en"


def is_rtl(text: str) -> bool:
    return hebrew_ratio(text) >= 0.5


def _word_score(text: str) -> int:
    """How many common Hebrew words appear. Higher means more plausibly correct."""
    words = re.findall(f"[{HEBREW}]+", text)
    return sum(1 for w in words if w in COMMON_WORDS)


def looks_reversed(text: str) -> bool:
    """Heuristic: does this Hebrew string look like it was extracted backwards?

    Compares how many recognisable Hebrew words appear in the string against the
    reversed string. Only meaningful on a reasonable amount of text, so short
    strings always return False rather than guess.
    """
    if hebrew_ratio(text) < 0.5 or len(text) < 40:
        return False
    forward = _word_score(text)
    backward = _word_score(text[::-1])
    return backward > forward and backward >= 2


def unmirror(text: str) -> str:
    """Flip mirrored punctuation. Only call on text known to be reversed."""
    return "".join(MIRRORED.get(c, c) for c in text)


def repair(text: str) -> tuple[str, bool]:
    """Return (text, was_repaired). Reverses a line that was extracted backwards."""
    if looks_reversed(text):
        return unmirror(text[::-1]), True
    return text, False


def clean(text: str) -> str:
    """Normalise a string for output: drop bidi controls, tidy whitespace."""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(BIDI_CONTROLS)
    text = text.replace("­", "")  # soft hyphen
    text = re.sub(r"[ \t ]+", " ", text)
    return text.strip()


def join_wrapped_lines(lines: list[str]) -> str:
    """Join lines of one paragraph that a PDF broke mid-sentence."""
    out: list[str] = []
    for raw in lines:
        line = clean(raw)
        if not line:
            continue
        if out and not re.search(r"[.!?:;׃]$", out[-1]):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return " ".join(out).strip()


# ---------------------------------------------------------------------------
# Legacy encoding and visual character order
#
# Many Hebrew PDFs produced by older Israeli systems embed fonts whose ToUnicode
# map points at the Windows-1255 code points rather than at Unicode Hebrew. The
# extracted text then looks like Latin-1 accented letters: "äñéìåôä" is really
# "הפוליסה" stored backwards. Such documents also store each line in visual
# order, so the line has to be reversed after decoding.
#
# This is decided once per document rather than per line: the encoding is a
# property of the file, and guessing line by line produces a mix of repaired and
# unrepaired text, which is worse than either.
# ---------------------------------------------------------------------------

# cp1255 maps Hebrew letters to 0xE0-0xFA; 0xB0-0xBF carry niqqud and punctuation.
_LEGACY_BYTES = set(range(0xE0, 0xFB))


def legacy_ratio(text: str) -> float:
    """Share of letters that sit in the cp1255 Hebrew byte range."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if ord(c) in _LEGACY_BYTES) / len(letters)


def decode_legacy(text: str) -> str:
    """Re-decode cp1255 bytes that were read as Latin-1. Lossless on failure."""
    try:
        return text.encode("latin-1", errors="strict").decode("cp1255", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Mixed content: convert character by character and keep the rest as is.
        out = []
        for char in text:
            if ord(char) < 256:
                try:
                    out.append(bytes([ord(char)]).decode("cp1255"))
                    continue
                except UnicodeDecodeError:
                    pass
            out.append(char)
        return "".join(out)


def looks_legacy_encoded(text: str) -> bool:
    """True when the text is cp1255 bytes misread as Latin-1."""
    if hebrew_ratio(text) > 0.2 or legacy_ratio(text) < 0.4:
        return False
    return hebrew_ratio(decode_legacy(text)) >= 0.5


# Punctuation that binds a Latin/digit run together: dates, decimals, URLs.
_LTR_GLUE = set("./:,-_%&?=#@+'~")
# Symbols that belong to the number they touch: 4% must not become %4.
_NUM_AFFIX = set("%‰°₪$€£¢")


def _runs(text: str) -> list[tuple[str, str]]:
    """Split into (class, chunk) runs. Class is R (Hebrew), L (Latin/digit) or N.

    A unit symbol gets a class of its own ("A") while the runs are being built.
    Were it merged into the surrounding neutral run, "4% :" would give the run
    "% :", which is no longer all-unit, and the symbol would drift away from its
    number: 4% would come out as %4.
    """

    def classify(char: str) -> str:
        if HEBREW_RE.match(char):
            return "R"
        if char.isascii() and char.isalnum():
            return "L"
        if char in _NUM_AFFIX:
            return "A"
        return "N"

    out: list[tuple[str, str]] = []
    for char in text:
        cls = classify(char)
        if out and out[-1][0] == cls:
            out[-1] = (cls, out[-1][1] + char)
        else:
            out.append((cls, char))

    # "A" has served its purpose as a separator; from here it is a neutral run
    # that the loop below may attach to the number beside it.
    out = [("N" if cls == "A" else cls, chunk) for cls, chunk in out]

    # Glue "N" runs that sit between two L runs: 02/09/2026 and a URL must stay whole.
    merged: list[tuple[str, str]] = []
    for i, (cls, chunk) in enumerate(out):
        if (
            cls == "N"
            and merged
            and merged[-1][0] == "L"
            and i + 1 < len(out)
            and out[i + 1][0] == "L"
            and all(c in _LTR_GLUE for c in chunk)
        ):
            merged[-1] = ("L", merged[-1][1] + chunk)
            continue
        if cls == "L" and merged and merged[-1][0] == "L":
            merged[-1] = ("L", merged[-1][1] + chunk)
            continue
        # A unit symbol belongs to the digits beside it, on either side.
        if cls == "N" and all(c in _NUM_AFFIX for c in chunk):
            if merged and merged[-1][0] == "L" and merged[-1][1][-1:].isdigit():
                merged[-1] = ("L", merged[-1][1] + chunk)
                continue
            if i + 1 < len(out) and out[i + 1][0] == "L" and out[i + 1][1][:1].isdigit():
                merged.append(("L", chunk))
                continue
        merged.append((cls, chunk))
    return merged


def reverse_visual(text: str) -> str:
    """Restore logical order in a line that was stored in visual order.

    Only the Hebrew runs are reversed. Numbers, dates and URLs are laid out
    left to right even inside an RTL line, so they are already stored in
    reading order and reversing them would corrupt them — a postal code or a
    due date silently turned backwards is worse than no repair at all.
    """
    parts = []
    for cls, chunk in reversed(_runs(text)):
        if cls == "L":
            parts.append(chunk)
        else:
            parts.append(unmirror(chunk[::-1]))
    return "".join(parts)


class TextPolicy:
    """How to repair every string in one document. Calibrated once, applied everywhere."""

    __slots__ = ("decode", "reverse")

    def __init__(self, decode: bool = False, reverse: bool = False) -> None:
        self.decode = decode
        self.reverse = reverse

    @property
    def active(self) -> bool:
        return self.decode or self.reverse

    def __repr__(self) -> str:
        return f"TextPolicy(decode={self.decode}, reverse={self.reverse})"

    def apply(self, text: str) -> str:
        """Repair one line. Reversal is per line: a line is the visual unit."""
        if self.decode:
            text = decode_legacy(text)
        if self.reverse:
            text = reverse_visual(text)
        return text


def calibrate(samples: list[str], sample_chars: int = 4000) -> TextPolicy:
    """Decide the document-wide repair from a sample of its lines."""
    blob = "\n".join(s for s in samples if s.strip())[:sample_chars]
    if not blob.strip():
        return TextPolicy()

    policy = TextPolicy()
    if looks_legacy_encoded(blob):
        policy.decode = True
        blob = decode_legacy(blob)

    if hebrew_ratio(blob) >= 0.4:
        lines = blob.splitlines()
        forward = sum(_word_score(ln) for ln in lines)
        backward = sum(_word_score(ln[::-1]) for ln in lines)
        # Require a clear win: equal scores mean no evidence, so change nothing.
        if backward > forward * 1.5 and backward >= 3:
            policy.reverse = True
    return policy


def strip_bullet(line: str) -> tuple[str, bool, bool]:
    """Split a list marker off a line.

    Returns (text, is_list_item, is_ordered).
    """
    line = clean(line)
    m = re.match(r"^[•‣●○▪·*\-–—]\s+(.*)$", line)
    if m:
        return m.group(1).strip(), True, False
    m = re.match(r"^(\d+|[א-ת])[.)]\s+(.*)$", line)
    if m:
        return m.group(2).strip(), True, True
    return line, False, False
