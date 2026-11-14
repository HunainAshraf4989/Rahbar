import numpy as np
import pytest

from ml.eval.metrics import COUNTS, clip_counts, paired_difference, rates, summarise
from ml.eval.text import normalise, script


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
