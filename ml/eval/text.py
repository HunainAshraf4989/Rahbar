"""Scoring normalisation and script classification for Urdu-English transcripts.

The normaliser removes differences that are not recognition errors (punctuation,
diacritics, letter-form variants, digit forms, case). It never transliterates,
so a word written in the wrong script still counts as an error.
"""

import re
import unicodedata

NORMALISER_VERSION = "2"
VERSIONS = ("1", "2")  # "1" is kept so that results can be compared across the change

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


# Version 2, class B (decision log in ml/README.md): spellings of one word that the human
# references themselves write both ways. Whole words only, after the text is split, and
# every key is already in the form that version 1 produces.
WORD_VARIANTS = {
    "لئے": "لیے", "لیئے": "لیے",
    "چاہئے": "چاہیے", "چاہیئے": "چاہیے", "چاہئیے": "چاہیے",
    "دیجئے": "دیجیے", "لیجئے": "لیجیے", "کیجئے": "کیجیے",
    "پتا": "پتہ",
    "پے": "پہ",
}


def fold_accents(text: str) -> str:
    """Version 2, class A: drop accents on Latin letters (café -> cafe); other scripts stay."""
    kept: list[str] = []
    for c in unicodedata.normalize("NFD", text):
        if unicodedata.category(c) == "Mn" and kept and "LATIN" in unicodedata.name(kept[-1], ""):
            continue
        kept.append(c)
    return unicodedata.normalize("NFC", "".join(kept))


def normalise(text: str, version: str = NORMALISER_VERSION) -> str:
    """Return the text as lower-case, space-separated words ready for scoring."""
    if version not in VERSIONS:
        raise ValueError(f"unknown normaliser version {version!r}")
    text = unicodedata.normalize("NFKC", text or "")
    if version == "2":
        text = fold_accents(text)
    text = _DROP.sub("", text).translate(_LETTERS).translate(_DIGITS).lower()
    text = text.replace("'", "").replace("’", "")
    chars = [c if unicodedata.category(c)[0] in "LNM" else " " for c in text]
    words = "".join(chars).split()
    if version == "2":
        words = [WORD_VARIANTS.get(w, w) for w in words]
    return " ".join(words)


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
