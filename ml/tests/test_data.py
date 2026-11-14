from ml.asr.data import assign_heldout, assign_validation, fingerprint, mark_benchmark_overlap

SENTENCE = "یہ ایک لمبا جملہ ہے جس میں کافی سارے الفاظ موجود ہیں اور relay بھی ہے"


def corpus_row(index: int, speaker: int, text: str = "کچھ بھی", category: str = "news") -> dict:
    return {"id": f"c{index}", "category": category, "speaker": f"S{speaker}",
            "group": f"{category}/S{speaker}", "clip_index": index, "duration": 10.0,
            "text": text}


def test_overlap_removes_only_clips_that_share_a_run_of_words():
    benchmark = [{"text": SENTENCE, "gemini_text": SENTENCE}]
    corpus = [corpus_row(0, 1, "آغاز " + SENTENCE), corpus_row(1, 1), corpus_row(2, 2, "relay ہے")]
    assert mark_benchmark_overlap(corpus, benchmark) == 1
    assert [r.get("split") for r in corpus] == ["excluded", None, None]


def test_short_clip_must_match_whole_text():
    benchmark = [{"text": "یہ بالکل ٹھیک بات ہے", "gemini_text": ""}]
    corpus = [corpus_row(0, 1, "یہ بالکل ٹھیک بات ہے۔"), corpus_row(1, 1, "یہ بالکل ٹھیک")]
    assert mark_benchmark_overlap(corpus, benchmark) == 1
    assert corpus[1].get("split") is None


def test_validation_keeps_groups_whole_and_is_reproducible():
    rows = [corpus_row(i, i // 5) for i in range(400)]
    rows[7].update(split="excluded")
    assign_validation(rows, fraction=0.1, seed=0)
    first = [r["split"] for r in rows]
    by_group: dict[str, set] = {}
    for row in rows:
        if row["split"] != "excluded":
            by_group.setdefault(row["group"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in by_group.values())
    assert 0.05 < first.count("val") / len(rows) < 0.15
    assert rows[7]["split"] == "excluded"
    assign_validation(rows, fraction=0.1, seed=0)
    assert [r["split"] for r in rows] == first


def heldout_rows() -> list[dict]:
    rows = []
    for i in range(600):
        category = "news" if i % 3 else "drama"
        row = corpus_row(i, i // 5, category=category)
        row.update(subset="US-CS" if i % 10 else "US-Std", split="val" if i % 7 == 0 else "train",
                   duration=35.0 if i % 11 == 0 else 6.0)
        rows.append(row)
    return rows


def test_heldout_is_stratified_random_and_reproducible():
    rows = heldout_rows()
    assign_heldout(rows, hours=0.2, seed=0)
    held = [r for r in rows if r["split"] == "heldout"]
    assert 0.2 * 3600 <= sum(r["duration"] for r in held) <= 0.2 * 3600 + 2 * 6.0
    assert all(r["subset"] == "US-CS" and r["duration"] <= 28 for r in held)
    assert not any(r["clip_index"] % 7 == 0 for r in held)  # validation clips are left alone
    pool = [r for r in rows if r["subset"] == "US-CS" and r["duration"] <= 28
            and r["clip_index"] % 7]
    for category in ("news", "drama"):
        share = (sum(r["category"] == category for r in held)
                 / sum(r["category"] == category for r in pool))
        assert abs(share - len(held) / len(pool)) < 0.02
    again = heldout_rows()
    assign_heldout(again, hours=0.2, seed=0)
    assert fingerprint(again) == fingerprint(rows)
    other = heldout_rows()
    assign_heldout(other, hours=0.2, seed=1)
    assert fingerprint(other) != fingerprint(rows)
