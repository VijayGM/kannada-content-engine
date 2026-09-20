"""
Unicode-safe Kannada text handling.

Important:
- Kannada is a complex Indic script; do not replace Unicode characters based on
  Unicode category alone.
- ZWJ (U+200D) and ZWNJ (U+200C) are format characters but can be meaningful
  for Indic shaping, so they must be preserved.
- U+FFFD (�) is never a valid output from our pipeline. It is the Unicode
  replacement character and indicates that corruption happened upstream.
"""

import unicodedata


REPLACEMENT_CHAR = "\uFFFD"
# Format characters that may be meaningful in Indic-script shaping.
ALLOWED_FORMAT_CHARS = {"\u200c", "\u200d"}


def sanitize_kannada(text: str) -> str:
    """Normalize text without damaging Kannada/Indic shaping sequences.

    The function deliberately preserves Kannada, English code-mixing,
    punctuation, emoji, combining marks, ZWJ and ZWNJ.

    Control characters are removed, except newline/tab. A pre-existing
    replacement character raises an error instead of being silently published.
    """
    if text is None:
        return ""

    text = unicodedata.normalize("NFC", str(text))

    if REPLACEMENT_CHAR in text:
        raise ValueError(
            "Unicode replacement character U+FFFD detected in source text. "
            "The text is already corrupted; refusing to publish it."
        )

    cleaned = []
    for ch in text:
        if ch in ("\n", "\t", "\r", " ", "\u200c", "\u200d"):
            cleaned.append(ch)
            continue

        category = unicodedata.category(ch)

        # Remove actual control characters. Do NOT replace them with U+FFFD.
        if category == "Cc":
            continue

        # Preserve all printable Unicode, including Kannada combining marks,
        # punctuation, currency symbols, emoji and English code-mixing.
        if not category.startswith("C"):
            cleaned.append(ch)
            continue

        # Other format characters (Cf) are generally invisible noise. Remove
        # them rather than converting them to the replacement glyph.
        continue

    return "".join(cleaned).strip()


def validate_kannada_text(text: str, min_ratio: float = 0.30) -> None:
    """Fail fast on encoding corruption or an unexpected loss of Kannada."""
    if text is None:
        return

    if REPLACEMENT_CHAR in text:
        raise ValueError("U+FFFD (�) detected in Kannada text")

    normalized = unicodedata.normalize("NFC", str(text))
    letters = [c for c in normalized if c.isalpha()]
    if not letters:
        return

    kannada = sum(1 for c in letters if "\u0C80" <= c <= "\u0CFF")
    ratio = kannada / len(letters)

    if ratio < min_ratio:
        raise ValueError(
            f"Kannada validation failed: only {ratio:.1%} of alphabetic "
            f"characters are in the Kannada block"
        )


def sanitize_and_validate(text: str, min_ratio: float = 0.10) -> str:
    """Single entry point for all Kannada text entering TTS/subtitles/DB."""
    cleaned = sanitize_kannada(text)
    validate_kannada_text(cleaned, min_ratio=min_ratio)
    return cleaned
