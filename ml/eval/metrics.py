"""Word, character and script-aware error metrics with bootstrap intervals.

English words are the reference words written in Latin script. Three measures
are computed on them:

- en_wer: substitutions and deletions of reference English words plus inserted
  Latin words, divided by reference English words.
- en_script_kept: share of reference English words whose aligned output word is
  in Latin script, whatever its spelling.
- en_exact: share of reference English words reproduced exactly.
"""

from collections.abc import Sequence

import jiwer
import numpy as np

from ml.eval.text import NORMALISER_VERSION, normalise, script

COUNTS = (
    "ref_words", "word_errors", "ref_chars", "char_errors",
    "en_ref", "en_errors", "en_script_kept", "en_exact",
    "ur_ref", "ur_errors",
    "hyp_words", "hyp_latin", "hyp_devanagari",
    "substitutions", "deletions", "insertions",
)


def clip_counts(references: Sequence[str], hypotheses: Sequence[str],
                version: str = NORMALISER_VERSION) -> np.ndarray:
    """Return one row of COUNTS per clip. References must be non-empty after normalising."""
    refs = [normalise(r, version) for r in references]
    hyps = [normalise(h, version) for h in hypotheses]
    if not all(refs):
        raise ValueError("empty reference after normalisation")
    words = jiwer.process_words(refs, hyps)
    chars = jiwer.process_characters(refs, hyps)
    rows = np.zeros((len(refs), len(COUNTS)), dtype=np.int64)
    for i, (ref, hyp, chunks) in enumerate(
        zip(words.references, words.hypotheses, words.alignments)
    ):
        c = dict.fromkeys(COUNTS, 0)
        ref_script = [script(w) for w in ref]
        hyp_script = [script(w) for w in hyp]
        c["ref_words"] = len(ref)
        c["en_ref"] = ref_script.count("latin")
        c["ur_ref"] = ref_script.count("urdu")
        c["hyp_words"] = len(hyp)
        c["hyp_latin"] = hyp_script.count("latin")
        c["hyp_devanagari"] = hyp_script.count("devanagari")
        for chunk in chunks:
            ref_span = range(chunk.ref_start_idx, chunk.ref_end_idx)
            hyp_span = range(chunk.hyp_start_idx, chunk.hyp_end_idx)
            if chunk.type == "equal":
                for r in ref_span:
                    if ref_script[r] == "latin":
                        c["en_script_kept"] += 1
                        c["en_exact"] += 1
            elif chunk.type == "substitute":
                c["word_errors"] += len(ref_span)
                c["substitutions"] += len(ref_span)
                for r, h in zip(ref_span, hyp_span):
                    if ref_script[r] == "latin":
                        c["en_errors"] += 1
                        c["en_script_kept"] += hyp_script[h] == "latin"
                    elif ref_script[r] == "urdu":
                        c["ur_errors"] += 1
            elif chunk.type == "delete":
                c["word_errors"] += len(ref_span)
                c["deletions"] += len(ref_span)
                for r in ref_span:
                    c["en_errors"] += ref_script[r] == "latin"
                    c["ur_errors"] += ref_script[r] == "urdu"
            elif chunk.type == "insert":
                c["word_errors"] += len(hyp_span)
                c["insertions"] += len(hyp_span)
                for h in hyp_span:
                    c["en_errors"] += hyp_script[h] == "latin"
                    c["ur_errors"] += hyp_script[h] == "urdu"
        c["ref_chars"] = len(chars.references[i])
        c["char_errors"] = sum(
            max(a.ref_end_idx - a.ref_start_idx, a.hyp_end_idx - a.hyp_start_idx)
            for a in chars.alignments[i]
            if a.type != "equal"
        )
        rows[i] = [c[k] for k in COUNTS]
    return rows


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    return np.divide(num, den, out=np.full(num.shape, np.nan), where=den > 0)


def rates(total: np.ndarray) -> dict[str, np.ndarray]:
    """Turn summed COUNTS (last axis) into the reported rates."""
    t = {k: total[..., i].astype(float) for i, k in enumerate(COUNTS)}
    return {
        "wer": _ratio(t["word_errors"], t["ref_words"]),
        "cer": _ratio(t["char_errors"], t["ref_chars"]),
        "en_wer": _ratio(t["en_errors"], t["en_ref"]),
        "en_script_kept": _ratio(t["en_script_kept"], t["en_ref"]),
        "en_exact": _ratio(t["en_exact"], t["en_ref"]),
        "ur_wer": _ratio(t["ur_errors"], t["ur_ref"]),
        "latin_share": _ratio(t["hyp_latin"], t["hyp_words"]),
        "devanagari_share": _ratio(t["hyp_devanagari"], t["hyp_words"]),
        "sub_rate": _ratio(t["substitutions"], t["ref_words"]),
        "del_rate": _ratio(t["deletions"], t["ref_words"]),
        "ins_rate": _ratio(t["insertions"], t["ref_words"]),
    }


def _interval(values: np.ndarray) -> tuple[float, float]:
    """95% percentile interval, or NaN when no resample defines the rate."""
    if np.all(np.isnan(values)):
        return np.nan, np.nan
    low, high = np.nanpercentile(values, [2.5, 97.5])
    return float(low), float(high)


def _resamples(
    n_clips: int, groups: Sequence[str] | None, samples: int, seed: int
) -> np.ndarray:
    """Matrix (samples x clips) of how often each clip is drawn; whole groups are drawn together."""
    rng = np.random.default_rng(seed)
    if groups is None:
        return rng.multinomial(n_clips, np.full(n_clips, 1 / n_clips), size=samples)
    names, index = np.unique(np.asarray(groups), return_inverse=True)
    draws = rng.multinomial(len(names), np.full(len(names), 1 / len(names)), size=samples)
    return draws[:, index]


def summarise(
    counts: np.ndarray,
    groups: Sequence[str] | None = None,
    samples: int = 2000,
    seed: int = 0,
) -> dict[str, dict[str, float]]:
    """Point estimate and 95% bootstrap interval for every rate."""
    point = rates(counts.sum(axis=0))
    boot = rates(_resamples(len(counts), groups, samples, seed) @ counts)
    out = {}
    for name, value in point.items():
        low, high = _interval(boot[name]) if not np.isnan(value) else (np.nan, np.nan)
        out[name] = {"value": float(value), "low": low, "high": high}
    return out


def paired_difference(
    counts_a: np.ndarray,
    counts_b: np.ndarray,
    groups: Sequence[str] | None = None,
    samples: int = 2000,
    seed: int = 0,
) -> dict[str, dict[str, float]]:
    """Rate of system A minus system B on the same clips, with a paired bootstrap interval."""
    draws = _resamples(len(counts_a), groups, samples, seed)
    a, b = rates(draws @ counts_a), rates(draws @ counts_b)
    point_a, point_b = rates(counts_a.sum(axis=0)), rates(counts_b.sum(axis=0))
    out = {}
    for name in point_a:
        low, high = _interval(a[name] - b[name])
        out[name] = {"value": float(point_a[name] - point_b[name]), "low": low, "high": high}
    return out
