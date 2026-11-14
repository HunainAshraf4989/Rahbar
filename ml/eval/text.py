"""Scoring normalisation and script classification for Urdu-English transcripts.

The normaliser removes differences that are not recognition errors (punctuation,
diacritics, letter-form variants, digit forms, case). It never transliterates,
so a word written in the wrong script still counts as an error.
"""

import re
import unicodedata

NORMALISER_VERSION = "1"

# Arabic-block letter forms that Urdu text writes with a different code point.
_LETTERS = str.maketrans(
    {
        "ي": "ی", "ى": "ی", "ك": "ک", "ه": "ہ", "ة": "ہ", "ۃ": "ہ",
        "أ": "ا", "إ": "ا", "ٱ": "ا", "ؤ": "و", "ۓ": "ے", "ۂ": "ہ",
    }
)
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DROP = re.compile(
    "[ؐ-ًؚ-ٰٟۖ-ۭ"  # diacritics and Quranic marks
    "ـ​-‏‪-‮⁦-⁩﻿]"  # tatweel, joiners, bidi marks
)
_LATIN = re.compile(r"[a-z0-9]*[a-z][a-z0-9]*")
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def normalise(text: str) -> str:
    """Return the text as lower-case, space-separated words ready for scoring."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _DROP.sub("", text).translate(_LETTERS).translate(_DIGITS).lower()
    text = text.replace("'", "").replace("’", "")
    chars = [c if unicodedata.category(c)[0] in "LNM" else " " for c in text]
    return " ".join("".join(chars).split())


def script(word: str) -> str:
    """Classify one normalised word as latin, urdu, devanagari, number or other."""
    if _LATIN.fullmatch(word):
        return "latin"
    if word.isdigit():
        return "number"
    has_arabic = bool(_ARABIC.search(word))
    has_devanagari = bool(_DEVANAGARI.search(word))
    has_latin = bool(re.search("[a-z]", word))
    if has_arabic and not (has_devanagari or has_latin):
        return "urdu"
    if has_devanagari and not (has_arabic or has_latin):
        return "devanagari"
    return "other"
