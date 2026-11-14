"""Score saved hypotheses against a manifest split and print a results table.

    python -m ml.eval.score --root DATA --split test --systems turbo mine [--baseline turbo]

Reads DATA/hyps/<split>/<system>.jsonl (written by ml.asr.transcribe, or by any
other tool as one {"id", "text"} per line). Results are reported per subset with
95% bootstrap intervals that resample whole speaker groups, and saved to
DATA/results/<split>.json. With --baseline, every other system also gets its
paired difference from that system.
"""

import argparse
import json
from pathlib import Path

from ml.asr.data import read_jsonl
from ml.eval.metrics import clip_counts, paired_difference, summarise
from ml.eval.text import NORMALISER_VERSION, normalise

SHOWN = ("wer", "cer", "en_wer", "en_script_kept", "en_exact", "latin_share", "devanagari_share")


def cell(estimate: dict[str, float]) -> str:
    if estimate["value"] != estimate["value"]:  # not a number: no English words in this subset
        return "-"
    return f"{100 * estimate['value']:.1f} ({100 * estimate['low']:.1f}-{100 * estimate['high']:.1f})"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--split", choices=["test", "val"], default="test")
    p.add_argument("--systems", nargs="+", required=True)
    p.add_argument("--baseline", help="system that the others are compared with")
    p.add_argument("--samples", type=int, default=2000, help="bootstrap resamples")
    args = p.parse_args()

    manifest = "benchmark.jsonl" if args.split == "test" else "corpus.jsonl"
    rows = [r for r in read_jsonl(args.root / "manifests" / manifest)
            if r["split"] == args.split and normalise(r["text"])]
    outputs = {}
    for system in args.systems:
        path = args.root / "hyps" / args.split / f"{system}.jsonl"
        outputs[system] = {r["id"]: r["text"] for r in read_jsonl(path)}
    scored = [r for r in rows if all(r["id"] in texts for texts in outputs.values())]
    if not scored:
        raise SystemExit("no clip has a hypothesis from every system")
    if len(scored) < len(rows):
        print(f"WARNING: scoring {len(scored)} of {len(rows)} clips; "
              "the rest lack a hypothesis from at least one system")

    results = {"split": args.split, "normaliser": NORMALISER_VERSION, "subsets": {}}
    for subset in sorted({r["subset"] for r in scored}):
        part = [r for r in scored if r["subset"] == subset]
        groups = [r["group"] for r in part]
        references = [r["text"] for r in part]
        counts = {s: clip_counts(references, [outputs[s][r["id"]] for r in part])
                  for s in args.systems}
        entry = {"clips": len(part), "systems": {}}
        print(f"\n{subset}: {len(part)} clips, {sum(r['duration'] for r in part) / 3600:.2f} h")
        print("| system | " + " | ".join(SHOWN) + " |")
        print("|---|" + "---|" * len(SHOWN))
        for system in args.systems:
            summary = summarise(counts[system], groups, args.samples)
            entry["systems"][system] = summary
            print(f"| {system} | " + " | ".join(cell(summary[m]) for m in SHOWN) + " |")
        if args.baseline:
            entry["difference_from_" + args.baseline] = {}
            for system in args.systems:
                if system != args.baseline:
                    diff = paired_difference(counts[system], counts[args.baseline], groups,
                                             args.samples)
                    entry["difference_from_" + args.baseline][system] = diff
                    w = diff["wer"]
                    print(f"{system} minus {args.baseline}, WER: {100 * w['value']:+.1f} points "
                          f"({100 * w['low']:+.1f} to {100 * w['high']:+.1f})")
        results["subsets"][subset] = entry
    out = args.root / "results" / f"{args.split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
