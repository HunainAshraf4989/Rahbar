"""Checks on saved hypotheses that catch outputs which would distort the scores.

    python -m ml.eval.sanity --root DATA --split test --systems whisper-turbo whisper-large-v3

For every system and subset it prints: output words per reference word, clips whose
output is more than 3 times longer than the reference, clips that reached the output
cap, repetition loops, gzip compression ratio above 2.4 (Whisper's own loop test),
empty outputs, special tokens or broken characters left in the text, letters of
other scripts, the share of all word errors caused by the worst 1% of clips, and the
clips longer than 30 s. The worst clips, with their texts, are written to
DATA/results/sanity-<split>-<system>.tsv. Runs on CPU in a few minutes.
"""

import argparse
import math
import re
import unicodedata
import zlib
from pathlib import Path

import numpy as np

from ml.asr.data import read_jsonl
from ml.eval.metrics import COUNTS, clip_counts
from ml.eval.text import normalise

STRAY = re.compile(r"<\|[^|>]*\|>|</?s>|<unk>|<pad>|\[[A-Z_]+\]|�")
LOOP_TIMES = 5


def compression_ratio(text: str) -> float:
    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data)) if data else 0.0


def has_loop(words: list[str], times: int = LOOP_TIMES) -> bool:
    """True when a unit of 1 to 4 words is repeated at least `times` times in a row."""
    for n in range(1, 5):
        for start in range(len(words) - n * times + 1):
            unit = words[start:start + n]
            if all(words[start + k * n:start + (k + 1) * n] == unit for k in range(1, times)):
                return True
    return False


def foreign_letters(text: str) -> bool:
    """True when the text has a letter that is not Latin, Arabic-block or Devanagari."""
    return any(c.isalpha() and not unicodedata.name(c, "").startswith(
        ("LATIN", "ARABIC", "DEVANAGARI")) for c in text)


def checks(rows: list[dict], hyps: dict[str, dict]) -> dict:
    """Per-clip flags and totals for one system on one subset."""
    outputs = [hyps[r["id"]]["text"] for r in rows]
    counts = clip_counts([r["text"] for r in rows], outputs)
    ref = counts[:, COUNTS.index("ref_words")]
    out = counts[:, COUNTS.index("hyp_words")]
    errors = counts[:, COUNTS.index("word_errors")]
    capped = np.array([bool(hyps[r["id"]].get("hit_cap")) for r in rows])
    # An output stopped by the cap can end half-way through a letter; that is not stray.
    whole = [o.rstrip("�") if cap else o for o, cap in zip(outputs, capped)]
    flags = {
        "over_3x": out > 3 * ref,
        "hit_cap": capped,
        "loop": np.array([has_loop(normalise(o).split()) for o in outputs]),
        "compressible": np.array([compression_ratio(o) > 2.4 for o in outputs]),
        "empty": out == 0,
        "stray": np.array([bool(STRAY.search(o)) for o in whole]),
        "other_script": np.array([foreign_letters(o) for o in outputs]),
    }
    flagged = flags["over_3x"] | flags["hit_cap"] | flags["loop"] | flags["compressible"]
    worst = max(1, math.ceil(0.01 * len(rows)))
    return {
        "clips": len(rows),
        "over_30s": sum(r["duration"] > 30 for r in rows),
        "word_ratio": out.sum() / ref.sum(),
        "shares": {name: float(flag.mean()) for name, flag in flags.items()},
        "worst_1pct_error_share": float(np.sort(errors)[::-1][:worst].sum() / max(1, errors.sum())),
        "wer": errors.sum() / ref.sum(),
        "wer_without_flagged": errors[~flagged].sum() / max(1, ref[~flagged].sum()),
        "cap_recorded": all("hit_cap" in hyps[r["id"]] for r in rows),
        "per_clip": {"ref": ref, "out": out, "errors": errors, "flags": flags},
    }


def warnings(result: dict) -> list[str]:
    """Problems large enough to make a score untrustworthy."""
    s = result["shares"]
    found = []
    if result["word_ratio"] > 1.3:
        found.append(f"output has {result['word_ratio']:.2f} words per reference word")
    if s["over_3x"] > 0.01 or s["loop"] > 0.02:
        found.append(f"{100 * s['over_3x']:.1f}% of outputs are over 3x the reference, "
                     f"{100 * s['loop']:.1f}% repeat themselves")
    if s["stray"] > 0:
        found.append(f"{100 * s['stray']:.1f}% of outputs contain special tokens or broken characters")
    if s["empty"] > 0.05:
        found.append(f"{100 * s['empty']:.1f}% of outputs are empty")
    return found


def show(system: str, subset: str, result: dict) -> None:
    s = {k: f"{100 * v:.1f}%" for k, v in result["shares"].items()}
    cap = s["hit_cap"] if result["cap_recorded"] else "not recorded"
    print(f"\n{subset} / {system}: {result['clips']} clips, {result['over_30s']} over 30 s")
    print(f"  output words per reference word {result['word_ratio']:.2f}; over 3x reference "
          f"{s['over_3x']}; hit cap {cap}; loops {s['loop']}; compression >2.4 {s['compressible']}")
    print(f"  empty {s['empty']}; special tokens or broken characters {s['stray']}; other "
          f"scripts {s['other_script']}; worst 1% of clips cause "
          f"{100 * result['worst_1pct_error_share']:.0f}% of word errors")
    print(f"  WER {100 * result['wer']:.1f}%; without loop or over-long clips "
          f"{100 * result['wer_without_flagged']:.1f}% (diagnostic only, not a result)")
    for warning in warnings(result):
        print(f"  WARNING {warning}")


def worst_lines(subset: str, rows: list[dict], hyps: dict, result: dict, n: int) -> list[str]:
    clip = result["per_clip"]
    lines = []
    for i in np.argsort(-clip["errors"], kind="stable")[:n]:
        row = rows[i]
        flags = ",".join(name for name, flag in clip["flags"].items() if flag[i]) or "-"
        text = " ".join(hyps[row["id"]]["text"].split())
        lines.append(f"{subset}\t{row['id']}\t{row['duration']}\t{clip['ref'][i]}\t"
                     f"{clip['out'][i]}\t{clip['errors'][i]}\t{flags}\t{row['text']}\t{text}")
    return lines


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--split", choices=["test", "val"], default="test")
    p.add_argument("--systems", nargs="+", required=True)
    p.add_argument("--worst", type=int, default=10, help="worst clips listed per subset")
    p.add_argument("--hyps", type=Path, help="folder of hypothesis files (default DATA/hyps/SPLIT)")
    args = p.parse_args()

    manifest = "benchmark.jsonl" if args.split == "test" else "corpus.jsonl"
    rows = [r for r in read_jsonl(args.root / "manifests" / manifest)
            if r["split"] == args.split and normalise(r["text"])]
    folder = args.hyps or args.root / "hyps" / args.split
    for system in args.systems:
        hyps = {h["id"]: h for h in read_jsonl(folder / f"{system}.jsonl")}
        scored = [r for r in rows if r["id"] in hyps]
        print(f"\n=== {system}: {len(scored)} of {len(rows)} clips have an output")
        lines = ["subset\tid\tseconds\tref_words\tout_words\terrors\tflags\treference\toutput"]
        for subset in sorted({r["subset"] for r in scored}):
            part = [r for r in scored if r["subset"] == subset]
            result = checks(part, hyps)
            show(system, subset, result)
            new = worst_lines(subset, part, hyps, result, args.worst)
            lines += new
            worst = [line.split("\t") for line in new]
            print("  worst: " + "; ".join(f"{w[1].rsplit('/', 1)[-1]} ({float(w[2]):.0f} s, "
                                          f"{w[3]}->{w[4]} words)" for w in worst[:5]))
        out = args.root / "results" / f"sanity-{args.split}-{system}.tsv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        print(f"  worst clips with texts: {out}")


if __name__ == "__main__":
    main()
