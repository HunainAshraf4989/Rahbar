import json
import warnings as python_warnings
from contextlib import contextmanager

import numpy as np
import pytest

from ml.asr.train import check_data, check_resume
from ml.asr.transcribe import RATE, pieces, token_cap
from ml.asr.transcribe import check_resume as check_hyps
from ml.eval.metrics import COUNTS, clip_counts, paired_difference, summarise
from ml.eval.sanity import checks, compression_ratio, foreign_letters, has_loop, warnings


def test_long_audio_is_cut_at_the_quietest_point_and_keeps_every_sample():
    rng = np.random.default_rng(0)
    audio = rng.uniform(-0.5, 0.5, 34 * RATE).astype("float32")
    audio[int(12.0 * RATE):int(12.4 * RATE)] = 0.0  # the only silence
    parts = pieces(audio)
    assert len(parts) == 2 and sum(len(p) for p in parts) == len(audio)
    assert abs(len(parts[0]) / RATE - 12.2) < 0.15
    assert all(len(p) <= 30 * RATE for p in parts)
    assert len(pieces(audio[:30 * RATE])) == 1
    very_long = rng.uniform(-0.5, 0.5, 95 * RATE).astype("float32")
    assert all(len(p) <= 30 * RATE for p in pieces(very_long))
    assert sum(len(p) for p in pieces(very_long)) == len(very_long)


def test_output_cap_is_far_above_real_speech_and_within_whisper():
    assert token_cap(1.0) == 50 and token_cap(10.0) == 320 and token_cap(30.0) == 440


def test_transcription_resume_needs_the_same_settings(tmp_path):
    out, meta = tmp_path / "x.jsonl", tmp_path / "x.json"
    wanted = {"protocol": "2", "model": "m", "language": "ur"}
    assert check_hyps(out, meta, wanted) == {}
    out.write_text('{"id": "a", "text": "b"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="older transcribe"):
        check_hyps(out, meta, wanted)
    meta.write_text(json.dumps({**wanted, "language": "auto"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="language"):
        check_hyps(out, meta, wanted)
    meta.write_text(json.dumps(wanted), encoding="utf-8")
    assert check_hyps(out, meta, wanted)["model"] == "m"


def test_training_resume_needs_the_same_settings_and_data(tmp_path):
    (tmp_path / "checkpoint-500").mkdir()
    record = {"args": {"lr": "0.0001", "seed": "0"}, "manifest_sha1": "abc"}
    (tmp_path / "run.json").write_text(json.dumps(record), encoding="utf-8")
    check_resume(tmp_path, record)
    with pytest.raises(SystemExit, match="lr"):
        check_resume(tmp_path, {**record, "args": {"lr": "0.0003", "seed": "0"}})
    with pytest.raises(SystemExit, match="manifest"):
        check_resume(tmp_path, {**record, "manifest_sha1": "def"})


def test_training_refuses_a_manifest_that_lost_clips():
    fine = {"left_out": {"no_audio": 0, "over_30s": 3, "label_too_long": 1, "no_label": 0}}
    check_data(fine, rows=26000)
    with pytest.raises(SystemExit, match="unsplit"):
        check_data({"left_out": {**fine["left_out"], "over_30s": 2900}}, rows=23000)
    with pytest.raises(SystemExit, match="no audio"):
        check_data({"left_out": {**fine["left_out"], "no_audio": 5}}, rows=26000)


def test_loops_and_stray_tokens_are_found():
    assert has_loop("یہ بات ہے کہ کہ کہ کہ کہ".split())
    assert has_loop(("اور پھر " * 6).split())
    assert not has_loop("یہ ایک عام جملہ ہے جس میں relay اور fuse ہیں".split())
    assert compression_ratio("بہت " * 60) > 2.4 > compression_ratio("یہ ایک عام جملہ ہے")
    assert foreign_letters("یہ 你好") and not foreign_letters("یہ relay ہے")
    rows = [{"id": str(i), "text": "یہ relay ٹھیک ہے", "duration": 3.0} for i in range(10)]
    good = {str(i): {"text": "یہ relay ٹھیک ہے"} for i in range(10)}
    assert warnings(checks(rows, good)) == []
    bad = {**good, "0": {"text": "یہ " * 40, "hit_cap": True}, "1": {"text": "<|ur|> یہ"}}
    result = checks(rows, bad)
    assert result["shares"]["over_3x"] == 0.1 and result["shares"]["stray"] == 0.1
    assert result["wer_without_flagged"] < result["wer"]
    assert len(warnings(result)) >= 2


def test_insertions_deletions_and_substitutions_are_counted():
    refs = ["یہ relay ہے"] * 3
    hyps = ["یہ relay relay ہے", "یہ ہے", "یہ fuse ہے"]
    rows = [dict(zip(COUNTS, row.tolist())) for row in clip_counts(refs, hyps)]
    assert [(r["insertions"], r["deletions"], r["substitutions"]) for r in rows] == [
        (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    assert all(r["word_errors"] == 1 for r in rows)


@contextmanager
def warnings_as_errors():
    with python_warnings.catch_warnings():
        python_warnings.simplefilter("error")
        yield


def test_a_rate_no_resample_defines_gives_nan_without_warning():
    counts = clip_counts(["relay fuse"] * 4, ["relay fuse"] * 4)  # no Urdu words at all
    with warnings_as_errors():
        result = summarise(counts, samples=50)
        diff = paired_difference(counts, counts, samples=50)
    assert np.isnan(result["ur_wer"]["low"]) and np.isnan(diff["ur_wer"]["low"])
