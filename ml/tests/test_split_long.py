import json

import numpy as np
import pytest
import soundfile as sf

from ml.asr.split_long import check, choose_cut, spell, viterbi


def test_viterbi_recovers_token_positions():
    tokens = [1, 2, 2, 1]
    frames = [0, 1, 1, 0, 2, 0, 2, 2, 1, 0]  # the repeated token needs a blank between
    emission = np.full((len(frames), 3), -10.0)
    emission[np.arange(len(frames)), frames] = -0.1
    path, score = viterbi(emission, tokens)
    assert path.tolist() == [0, 1, 1, 2, 3, 4, 5, 5, 7, 8]
    assert score > -0.2


def test_cut_is_the_longest_pause_between_timed_neighbours_within_30s():
    times = [(0.0, 4.0), (4.1, 9.0), None, (11.0, 20.0), (20.2, 28.5), (32.5, 34.0)]
    # The 2 s gap around the untimed word and the 4 s pause at 30.5 s are both refused.
    assert choose_cut(times, duration=35.0) == (3, 20.1, pytest.approx(0.2))
    assert choose_cut([(0.0, 2.0), (33.0, 35.0)], duration=35.0) is not None
    assert choose_cut([(0.0, 1.0), (1.2, 3.0), (3.1, 35.0)], duration=35.0) is None


def test_numbers_get_no_alignment_times():
    import uroman

    roman = uroman.Uroman()
    assert [spell(roman, w) for w in ["2021", "۲۰۲۱", "150", "777"]] == ["", "", "", ""]
    assert spell(roman, "years") == "years" and spell(roman, "سال")


def timed(words: dict) -> list:
    """Alignment times as the aligner gives them: None for a word without letters."""
    return [None if word.isdigit() else span for word, span in words.items()]


def test_cut_never_lands_inside_a_number_pair_13():
    # "... post 2020 and 2021 ..." where 2021 is spoken from 12.5 s to 13.6 s: the
    # aligner shows silence there, longer than the real 0.6 s pause after "years".
    words = {"years": (10.0, 10.4), "especially": (11.0, 11.6), "post": (11.7, 12.0),
             "2020": None, "and": (12.1, 12.3), "2021": None, "ka": (14.0, 14.2),
             "asar": (14.3, 30.5)}
    last, when, pause = choose_cut(timed(words), duration=31.0)
    names = list(words)
    assert (names[last], names[last + 1]) == ("years", "especially")
    assert when == pytest.approx(10.7) and pause == pytest.approx(0.6)


def test_two_numbers_stay_together_pair_22():
    # "... between 150 160 rupees ..." with both numbers untimed.
    words = {"price": (3.0, 3.5), "between": (4.0, 4.4), "150": None, "160": None,
             "rupees": (6.5, 7.0), "tha": (7.3, 31.0)}
    last, _, _ = choose_cut(timed(words), duration=32.0)
    names = list(words)
    assert names[last] in ("price", "rupees")  # never "between" or a number
    assert all(times is not None for times in timed(words)[last:last + 2])


def write_pair(root, parent_text, a_text, b_text, a_seconds=10.0, b_seconds=12.0):
    audio = root / "audio16k"
    audio.mkdir(parents=True, exist_ok=True)
    whole = np.zeros(int((a_seconds + b_seconds) * 16000), dtype="int16")
    sf.write(audio / "c1.flac", whole, 16000, subtype="PCM_16")
    cut = int(a_seconds * 16000)
    sf.write(audio / "c1_a.flac", whole[:cut], 16000, subtype="PCM_16")
    sf.write(audio / "c1_b.flac", whole[cut:], 16000, subtype="PCM_16")
    base = {"split": "train", "parent": "c1", "parent_audio": "audio16k/c1.flac",
            "parent_text": parent_text, "cut_pause": 0.3}
    rows = [{**base, "id": "c1_a", "audio": "audio16k/c1_a.flac", "text": a_text,
             "duration": a_seconds},
            {**base, "id": "c1_b", "audio": "audio16k/c1_b.flac", "text": b_text,
             "duration": b_seconds}]
    (root / "manifests").mkdir(exist_ok=True)
    (root / "manifests" / "corpus.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def test_check_passes_a_clean_pair_and_exports_it(tmp_path, capsys):
    write_pair(tmp_path, "یہ پہلا حصہ ہے اور یہ دوسرا", "یہ پہلا حصہ ہے", "اور یہ دوسرا")
    check(tmp_path, tmp_path / "listen", pairs=2)
    assert "all pairs pass" in capsys.readouterr().out
    assert (tmp_path / "listen" / "01_a.flac").exists()
    assert (tmp_path / "listen" / "pairs.tsv").exists()


def test_check_fails_the_old_number_cut(tmp_path, capsys):
    # The bug: part a's text ends with a number whose sound is in part b.
    write_pair(tmp_path, "post 2020 and 2021 ka asar", "post 2020 and 2021", "ka asar")
    with pytest.raises(SystemExit):
        check(tmp_path, None, pairs=0)
    assert "without alignment times is next to the cut" in capsys.readouterr().out


def test_check_fails_when_text_is_lost(tmp_path, capsys):
    write_pair(tmp_path, "یہ پہلا حصہ ہے اور یہ دوسرا", "یہ پہلا حصہ", "اور یہ دوسرا")
    with pytest.raises(SystemExit):
        check(tmp_path, None, pairs=0)
    assert "do not join to the original transcript" in capsys.readouterr().out
