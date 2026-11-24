import numpy as np
import pytest

from ml.eval.metrics import COUNTS, clip_counts, paired_difference, rates, summarise
from ml.eval.text import VERSIONS, WORD_VARIANTS, normalise, script


def one(reference: str, hypothesis: str) -> dict[str, int]:
    return dict(zip(COUNTS, clip_counts([reference], [hypothesis])[0].tolist()))


def test_normalise_removes_only_non_recognition_differences():
    assert normalise("یہ Relay ہے۔") == "یہ relay ہے"
    assert normalise("كيا ھے؟") == normalise("کیا ھے")
    assert normalise("اُردُو") == "اردو"
    assert normalise("۱۲ volt, ١٢") == "12 volt 12"
    assert normalise("don't") == "dont"
    assert normalise("") == ""


def test_normalise_never_changes_script():
    assert normalise("ریلے") != normalise("relay")
    assert normalise("रिले") == "रिले"


def test_version_two_scores_listed_spellings_as_one_word():
    for old, new in WORD_VARIANTS.items():
        assert normalise(old, "1") == old and normalise(new, "1") == new  # already version-1 form
        assert normalise(old, "2") == new
        assert normalise(f"یہ {old} ہے", "1") != normalise(f"یہ {new} ہے", "1")
        assert normalise(f"یہ {old} ہے", "2") == normalise(f"یہ {new} ہے", "2")
    assert normalise("اس کے لئے", "2") == normalise("اس کے لیے", "2")
    assert normalise("پتا", "2") == normalise("پتہ", "2")
    assert normalise("گھر پے", "2") == normalise("گھر پہ", "2")
    assert one("اس کے لیے", "اس کے لئے")["word_errors"] == 0
    assert dict(zip(COUNTS, clip_counts(["اس کے لیے"], ["اس کے لئے"], "1")[0].tolist()))[
        "word_errors"] == 1


@pytest.mark.parametrize(
    ("a", "b"),
    [("کہا", "کہہ"), ("جگہ", "جگا"), ("بچہ", "بچا"), ("سارا", "سارہ"), ("تیرا", "تیرہ"),
     ("منہ", "منا"), ("ذرا", "ذرہ"), ("جائے", "جائیے"), ("گئے", "گیے"), ("مزہ", "مزا"),
     ("ڈرامہ", "ڈراما"), ("اپ", "آپ"), ("پہ", "پر"), ("ہے", "ہیں"), ("کی", "کے"),
     ("ہ", "ھ"), ("کیے", "کئے"), ("دیے", "دیئے")],
)
def test_version_two_keeps_different_words_apart(a, b):
    assert normalise(a, "2") != normalise(b, "2")


def test_version_two_folds_latin_accents_only():
    assert normalise("Café", "1") == "café" and normalise("Café", "2") == "cafe"
    assert script(normalise("Café")) == "latin"
    assert normalise("اُردُو", "2") == "اردو"
    assert normalise("रिले", "2") == "रिले"
    assert normalise("کےلئے", "2") == normalise("کےلئے", "1")  # whole words only


def test_normalising_twice_changes_nothing():
    for text in ["یہ relay ہے، اس کے لئے café پتا پے", "Tom's café, ۱۲ volt ١٢", "रिले यह है", ""]:
        for version in VERSIONS:
            once = normalise(text, version)
            assert normalise(once, version) == once


def test_unknown_normaliser_version_is_refused():
    with pytest.raises(ValueError):
        normalise("یہ ہے", "3")


@pytest.mark.parametrize(
    ("word", "expected"),
    [("relay", "latin"), ("terminal85", "latin"), ("ریلے", "urdu"), ("रिले", "devanagari"),
     ("85", "number"), ("relayکا", "other")],
)
def test_script(word, expected):
    assert script(word) == expected


def test_perfect_match():
    c = one("اس relay کا terminal دیکھو", "اس relay کا terminal دیکھو")
    assert c["word_errors"] == 0 and c["char_errors"] == 0
    assert c["en_ref"] == 2 and c["en_exact"] == 2 and c["en_script_kept"] == 2


def test_english_word_written_in_urdu_script_is_an_english_error():
    c = one("اس relay کا terminal دیکھو", "اس ریلے کا terminal دیکھو")
    assert c["word_errors"] == 1
    assert c["en_errors"] == 1 and c["en_script_kept"] == 1 and c["en_exact"] == 1
    assert c["ur_errors"] == 0


def test_misspelt_english_keeps_script_but_is_not_exact():
    c = one("اس relay کو دیکھو", "اس rely کو دیکھو")
    assert c["en_errors"] == 1 and c["en_script_kept"] == 1 and c["en_exact"] == 0


def test_deletion_and_insertion_of_english_words():
    assert one("یہ fuse ہے", "یہ ہے")["en_errors"] == 1
    inserted = one("یہ ہے", "یہ fuse ہے")
    assert inserted["en_ref"] == 0 and inserted["en_errors"] == 1 and inserted["hyp_latin"] == 1


def test_devanagari_output_is_counted():
    c = one("یہ relay ہے", "यह relay है")
    assert c["hyp_devanagari"] == 2 and c["ur_errors"] == 2 and c["en_exact"] == 1


def test_empty_hypothesis_and_empty_reference():
    c = one("یہ relay ہے", "")
    assert c["word_errors"] == 3 and c["char_errors"] == c["ref_chars"]
    with pytest.raises(ValueError):
        clip_counts(["۔"], ["کچھ"])


def test_rates_are_micro_averaged():
    counts = clip_counts(["ایک دو", "ایک دو تین چار"], ["ایک", "ایک دو تین چار"])
    assert rates(counts.sum(axis=0))["wer"] == pytest.approx(1 / 6)


def test_bootstrap_interval_contains_point_and_is_reproducible():
    refs = ["یہ relay ہے", "وہ fuse تھا", "battery کہاں ہے", "یہ ٹھیک ہے"] * 10
    hyps = ["یہ relay ہے", "وہ فیوز تھا", "battery ہے", "یہ ٹھیک ہے"] * 10
    counts = clip_counts(refs, hyps)
    first = summarise(counts, samples=300, seed=1)
    assert first == summarise(counts, samples=300, seed=1)
    assert first["wer"]["low"] <= first["wer"]["value"] <= first["wer"]["high"]
    grouped = summarise(counts, groups=[str(i % 4) for i in range(40)], samples=300)
    assert grouped["wer"]["high"] - grouped["wer"]["low"] > first["wer"]["high"] - first["wer"]["low"]


def test_paired_difference_sign():
    refs = ["یہ relay ہے", "وہ fuse تھا"] * 10
    good = clip_counts(refs, refs)
    bad = clip_counts(refs, ["یہ ہے", "وہ تھا"] * 10)
    diff = paired_difference(good, bad, samples=200)
    assert diff["wer"]["value"] < 0 and diff["wer"]["high"] < 0
    assert np.isclose(diff["en_exact"]["value"], 1.0)
