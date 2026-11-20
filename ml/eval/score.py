"""Score saved hypotheses against a manifest split and print a results table.

    python -m ml.eval.score --root DATA --split test --systems turbo mine [--baseline turbo]

Reads DATA/hyps/<split>/<system>.jsonl (written by ml.asr.transcribe, or by any
other tool as one {"id", "text"} per line). Results are reported per subset with
95% bootstrap intervals that resample whole speaker groups, and saved to
DATA/results/<split>.json together with each system's recorded transcription
settings. With --baseline, every other system also gets its paired difference
from that system. Systems transcribed under different protocols are refused
unless --mixed-protocols is given. Every system is also run through the checks
of ml.eval.sanity, and a warning is printed when its outputs would distort the
score, with each system's share of looping, capped, over-long and empty outputs.
--max-seconds scores only clips up to that length, and --common-clean only the clips
on which no listed system loops or runs over, both as robustness checks.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from ml.asr.data import read_jsonl
from ml.eval.metrics import clip_counts, paired_difference, summarise
from ml.eval.sanity import checks, warnings
from ml.eval.text import NORMALISER_VERSION, normalise

SHOWN = ("wer", "ins_rate", "cer", "ur_wer", "en_wer", "en_script_kept", "en_exact",
         "latin_share", "devanagari_share")


def cell(estimate: dict[str, float]) -> str:
    if estimate["value"] != estimate["value"]:  # not a number: no English words in this subset
        return "-"
    return f"{100 * estimate['value']:.1f} ({100 * estimate['low']:.1f}-{100 * estimate['high']:.1f})"


def protocol(folder: Path, system: str) -> dict:
    meta = folder / f"{system}.json"
    return json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--split", choices=["test", "val"], default="test")
    p.add_argument("--systems", nargs="+", required=True)
    p.add_argument("--baseline", help="system that the others are compared with")
    p.add_argument("--samples", type=int, default=2000, help="bootstrap resamples")
    p.add_argument("--max-seconds", type=float, help="score only clips up to this length")
    p.add_argument("--mixed-protocols", action="store_true",
                   help="allow systems transcribed under different protocols in one table")
    p.add_argument("--common-clean", action="store_true",
                   help="score only clips on which no listed system loops or runs over")
    p.add_argument("--out", type=Path, help="results file (default DATA/results/<split>.json)")
    args = p.parse_args()

    folder = args.root / "hyps" / args.split
    settings = {s: protocol(folder, s) for s in args.systems}
    for system, recorded in settings.items():
        if recorded:
            print(f"{system}: protocol {recorded['protocol']}, {recorded['backend']} "
                  f"{recorded['model']}, language {recorded['language']}, {recorded['decoding']}, "
                  f"{recorded['dtype']}")
        else:
            print(f"{system}: transcription settings not recorded")
    protocols = {s.get("protocol", "unrecorded") for s in settings.values()}
    if len(protocols) > 1 and not args.mixed_protocols:
        raise SystemExit(f"the systems were transcribed under different protocols {protocols}; "
                         "re-run them, or pass --mixed-protocols for a side table")

    manifest = "benchmark.jsonl" if args.split == "test" else "corpus.jsonl"
    rows = [r for r in read_jsonl(args.root / "manifests" / manifest)
            if r["split"] == args.split and normalise(r["text"])
            and (args.max_seconds is None or r["duration"] <= args.max_seconds)]
    hyps = {s: {h["id"]: h for h in read_jsonl(folder / f"{s}.jsonl")} for s in args.systems}
    scored = [r for r in rows if all(r["id"] in h for h in hyps.values())]
    if not scored:
        raise SystemExit("no clip has a hypothesis from every system")
    if len(scored) < len(rows):
        print(f"WARNING: scoring {len(scored)} of {len(rows)} clips; "
              "the rest lack a hypothesis from at least one system")

    results = {"split": args.split, "normaliser": NORMALISER_VERSION,
               "max_seconds": args.max_seconds, "common_clean": args.common_clean,
               "settings": settings, "subsets": {}}
    for subset in sorted({r["subset"] for r in scored}):
        part = [r for r in scored if r["subset"] == subset]
        found = {s: checks(part, hyps[s]) for s in args.systems}
        if args.common_clean:  # the same clips for every system: those no system loops on
            bad = np.any([found[s]["per_clip"]["flagged"] for s in args.systems], axis=0)
            print(f"\n{subset}: keeping {int((~bad).sum())} of {len(part)} clips on which no "
                  "system loops, reaches the cap or writes over 3x the reference")
            part = [r for r, b in zip(part, bad) if not b]
            if not part:
                continue
            found = {s: checks(part, hyps[s]) for s in args.systems}
        groups = [r["group"] for r in part]
        references = [r["text"] for r in part]
        counts = {s: clip_counts(references, [hyps[s][r["id"]]["text"] for r in part])
                  for s in args.systems}
        entry = {"clips": len(part), "systems": {}, "warnings": {}}
        print(f"\n{subset}: {len(part)} clips, {sum(r['duration'] for r in part) / 3600:.2f} h")
        print("| system | " + " | ".join(SHOWN) + " |")
        print("|---|" + "---|" * len(SHOWN))
        for system in args.systems:
            summary = summarise(counts[system], groups, args.samples)
            entry["systems"][system] = summary
            print(f"| {system} | " + " | ".join(cell(summary[m]) for m in SHOWN) + " |")
        print("| system | loops | reached cap | over 3x reference | empty |")
        print("|---|---|---|---|---|")
        entry["output_checks"] = {}
        for system in args.systems:
            shares = found[system]["shares"]
            entry["output_checks"][system] = shares
            print(f"| {system} | " + " | ".join(f"{100 * shares[k]:.1f}" for k in (
                "loop", "hit_cap", "over_3x", "empty")) + " |")
        for system in args.systems:
            entry["warnings"][system] = warnings(found[system])
            for warning in entry["warnings"][system]:
                print(f"WARNING {system}: {warning}; see python -m ml.eval.sanity")
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
    out = args.out or args.root / "results" / f"{args.split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
