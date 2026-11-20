"""Split corpus clips longer than Whisper's 30 s window into two aligned parts.

    python -m ml.asr.split_long --root DATA [--min-score -2.0] [--device cuda]
    python -m ml.asr.split_long --root DATA --check [--listen FOLDER]

Each long clip is force-aligned to its transcript with the MMS aligner on
romanised text, then cut at the longest pause between two neighbouring words that
both have alignment times and that leaves both parts within 30 s. A word without
times (a number, or a token with no letters) is never at the edge of a part, so
its sound and its text always stay in the same part. The two parts replace the
clip in manifests/corpus.jsonl and keep every word and every sample. A clip whose
alignment is poor, or that has no usable pause, stays whole and is left out of
training. Benchmark and held-out clips are never split. Each part records the
alignment score, the pause length and the loudness at the cut relative to the
clip (cut_energy), so that doubtful cuts can be reviewed.

--check verifies a finished split: for every pair the two texts join to the
original transcript, the two audio parts join to the original audio, both parts
fit in 30 s, and the words on either side of the cut have alignment times. With
--listen it also copies pairs for listening into FOLDER.
"""

import argparse
import re
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from ml.asr.data import MAX_SECONDS, read_jsonl, write_jsonl

ALIGNER = "MahmoudAshraf/mms-300m-1130-forced-aligner"
FRAME = 0.02  # seconds per aligner output frame
DIGIT = re.compile("[0-9٠-٩۰-۹]")


def spell(roman, word: str) -> str:
    """The letters the aligner is given for one word; empty means the word gets no times."""
    return re.sub("[^a-z']", "", roman.romanize_string(word).lower())


def viterbi(emission: np.ndarray, tokens: list[int], blank: int = 0) -> tuple[np.ndarray, float]:
    """Best CTC path for `tokens`: frame index of each state, and mean log-probability."""
    states = np.full(2 * len(tokens) + 1, blank)
    states[1::2] = tokens
    frames, width = len(emission), len(states)
    skip = np.zeros(width, dtype=bool)  # may jump over the blank between two different tokens
    skip[3::2] = states[3::2] != states[1:-2:2]
    score = np.full(width, -np.inf)
    score[:2] = emission[0, states[:2]]
    back = np.zeros((frames, width), dtype=np.int8)
    for t in range(1, frames):
        stay = score
        step = np.concatenate(([-np.inf], score[:-1]))
        jump = np.where(skip, np.concatenate(([-np.inf, -np.inf], score[:-2])), -np.inf)
        options = np.stack([stay, step, jump])
        back[t] = options.argmax(axis=0)
        score = options.max(axis=0) + emission[t, states]
    state = width - 1 if score[-1] >= score[-2] else width - 2
    total = score[state]
    path = np.empty(frames, dtype=np.int64)
    for t in range(frames - 1, -1, -1):
        path[t] = state
        state -= int(back[t, state])
    return path, float(total / frames)


class Aligner:
    def __init__(self, device: str, model: str = ALIGNER):
        import uroman
        from transformers import AutoModelForCTC, AutoTokenizer

        self.device = device
        self.model = AutoModelForCTC.from_pretrained(model).to(device).eval()
        self.vocab = AutoTokenizer.from_pretrained(model).get_vocab()
        self.roman = uroman.Uroman()

    def word_times(self, samples: np.ndarray, words: list[str]) -> tuple[list, float] | None:
        """(start, end) seconds of every word that has letters, None for the others."""
        spelt = [spell(self.roman, w) for w in words]
        tokens = [self.vocab[c] for word in spelt for c in word]
        audio = torch.from_numpy((samples - samples.mean()) / (samples.std() + 1e-7))
        with torch.inference_mode():
            logits = self.model(audio[None].float().to(self.device)).logits[0]
        emission = torch.log_softmax(logits, dim=-1).cpu().numpy()
        if not tokens or len(emission) < 2 * len(tokens):
            return None
        path, score = viterbi(emission, tokens)
        times, position = [], 0
        for word in spelt:
            if not word:
                times.append(None)
                continue
            own = np.flatnonzero((path >= 2 * position + 1) & (path <= 2 * (position + len(word)) - 1))
            times.append((own[0] * FRAME, (own[-1] + 1) * FRAME))
            position += len(word)
        return times, score


def choose_cut(times: list, duration: float) -> tuple[int, float, float] | None:
    """Index of the last word of part one, the cut time and the pause length.

    Only the gap between two neighbouring words that both have times can hold the
    cut. Between a timed word and an untimed one (a number, say) the aligner shows
    silence where the untimed word is actually spoken, so such a gap is never used.
    """
    best = None
    for left in range(len(times) - 1):
        before, after = times[left], times[left + 1]
        if before is None or after is None:
            continue
        cut, pause = (before[1] + after[0]) / 2, after[0] - before[1]
        if duration - MAX_SECONDS < cut < MAX_SECONDS and (best is None or pause > best[2]):
            best = (left, cut, pause)
    return best


def split_rows(root: Path, min_score: float, min_pause: float, device: str, model: str) -> None:
    manifest = root / "manifests" / "corpus.jsonl"
    rows = read_jsonl(manifest)
    if any("parent" in row for row in rows):
        raise SystemExit("this manifest is already split. To split again, first run "
                         "`python -m ml.asr.data manifest` and `python -m ml.asr.data audio`")
    aligner = Aligner(device, model)
    out, tally = [], {"split": 0, "poor_alignment": 0, "no_pause": 0}
    hours = {"split": 0.0, "kept_whole": 0.0}
    for row in rows:
        long = "audio" in row and row["duration"] > MAX_SECONDS and row["split"] in ("train", "val")
        if not long:
            out.append(row)
            continue
        samples, _ = sf.read(root / row["audio"], dtype="float32")
        words = row["text"].split()
        aligned = aligner.word_times(samples, words)
        cut = choose_cut(aligned[0], row["duration"]) if aligned else None
        if aligned is None or aligned[1] < min_score:
            reason = "poor_alignment"
        elif cut is None or cut[2] < min_pause:
            reason = "no_pause"
        else:
            last_word, when, pause = cut
            edge = int(when * 16000)
            around = samples[max(0, edge - 800):edge + 800]
            energy = float(np.sqrt((around ** 2).mean() / ((samples ** 2).mean() + 1e-12)))
            pieces = [(samples[:edge], words[:last_word + 1]), (samples[edge:], words[last_word + 1:])]
            for letter, (piece, text) in zip("ab", pieces):
                path = Path(row["audio"]).with_name(f"{Path(row['audio']).stem}_{letter}.flac")
                sf.write(root / path, piece, 16000, format="FLAC", subtype="PCM_16")
                out.append({**row, "id": f"{row['id']}_{letter}", "audio": path.as_posix(),
                            "text": " ".join(text), "duration": round(len(piece) / 16000, 3),
                            "parent": row["id"], "parent_audio": row["audio"],
                            "parent_text": row["text"], "align_score": round(aligned[1], 3),
                            "cut_pause": round(pause, 3),
                            "cut_energy": round(energy, 3)})
            tally["split"] += 1
            hours["split"] += row["duration"] / 3600
            continue
        tally[reason] += 1
        hours["kept_whole"] += row["duration"] / 3600
        out.append({**row, "too_long": reason})
    write_jsonl(manifest, out)
    print(f"{tally}; hours split {hours['split']:.2f}, hours left whole {hours['kept_whole']:.2f}")


def check_pair(root: Path, a: dict, b: dict, roman) -> list[str]:
    """Every way in which one pair of parts fails to match its original clip."""
    problems = []
    if f"{a['text']} {b['text']}" != a.get("parent_text"):
        problems.append("texts do not join to the original transcript")
    if not spell(roman, a["text"].split()[-1]) or not spell(roman, b["text"].split()[0]):
        problems.append("a word without alignment times is next to the cut")
    frames = [sf.info(root / part["audio"]).frames for part in (a, b)]
    whole = root / a["parent_audio"]
    if whole.exists() and sum(frames) != sf.info(whole).frames:
        problems.append("audio parts do not join to the original audio")
    if not all(0 < f <= MAX_SECONDS * 16000 for f in frames):
        problems.append("a part is empty or longer than 30 s")
    return problems


def check(root: Path, listen: Path | None, pairs: int) -> None:
    import uroman

    rows = read_jsonl(root / "manifests" / "corpus.jsonl")
    parts = {r["id"]: r for r in rows if "parent" in r}
    if not parts:
        raise SystemExit("no split clips in the manifest; run split_long first")
    if any("parent_text" not in r for r in parts.values()):
        raise SystemExit("this split was made by an older split_long; split again")
    roman = uroman.Uroman()
    found: dict[str, list[str]] = {}
    firsts = sorted(pid for pid in parts if pid.endswith("_a"))
    for pid in firsts:
        b = parts.get(pid[:-2] + "_b")
        problems = ["part b is missing"] if b is None else check_pair(root, parts[pid], b, roman)
        for problem in problems:
            found.setdefault(problem, []).append(parts[pid]["parent"])
    stray = [r["id"] for r in rows if r["split"] in ("train", "val")
             and r["duration"] > MAX_SECONDS and "too_long" not in r]
    if stray:
        found["clip over 30 s that was neither split nor marked too_long"] = stray
    kept = sum(1 for r in rows if "too_long" in r)
    print(f"{len(firsts)} split clips checked; {kept} clips left whole")
    for problem, ids in found.items():
        print(f"FAIL {problem}: {len(ids)} clips, for example {ids[:5]}")
    if listen:
        export(root, [parts[pid] for pid in firsts], parts, listen, pairs)
    if found:
        raise SystemExit(1)
    print("all pairs pass")


def export(root: Path, firsts: list[dict], parts: dict, folder: Path, pairs: int) -> None:
    """Copy pairs for listening: half with a number near the cut, half with the shortest pauses."""

    def number_distance(a: dict) -> int:
        words = a["parent_text"].split()
        edge = len(a["text"].split())  # index of the first word of part b
        near = [abs(i - edge) for i, w in enumerate(words) if DIGIT.search(w)]
        return min(near) if near else 10**6

    near_numbers = sorted((a for a in firsts if number_distance(a) < 10**6), key=number_distance)
    chosen = near_numbers[:pairs // 2]
    rest = sorted((a for a in firsts if a not in chosen), key=lambda a: a["cut_pause"])
    chosen += rest[:pairs - len(chosen)]
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["pair\tparent\tcut_pause_s\tend_of_a\tstart_of_b\tverdict"]
    for number, a in enumerate(chosen, 1):
        b = parts[a["id"][:-2] + "_b"]
        for letter, part in (("a", a), ("b", b)):
            shutil.copyfile(root / part["audio"], folder / f"{number:02d}_{letter}.flac")
        lines.append(f"{number:02d}\t{a['parent']}\t{a['cut_pause']}\t"
                     f"{' '.join(a['text'].split()[-6:])}\t{' '.join(b['text'].split()[:6])}\t")
    (folder / "pairs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print(f"copied {len(chosen)} pairs to {folder} ({min(len(near_numbers), pairs // 2)} "
          "with a number near the cut); fill in the verdict column of pairs.tsv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--min-score", type=float, default=-2.0,
                        help="lowest mean log-probability accepted for an alignment")
    parser.add_argument("--min-pause", type=float, default=0.1, help="shortest pause to cut in")
    parser.add_argument("--aligner", default=ALIGNER, help="model name or local folder")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--check", action="store_true", help="verify a finished split")
    parser.add_argument("--listen", type=Path, help="with --check: copy pairs here for listening")
    parser.add_argument("--pairs", type=int, default=20, help="with --listen: how many pairs")
    args = parser.parse_args()
    if args.check:
        check(args.root, args.listen, args.pairs)
    else:
        split_rows(args.root, args.min_score, args.min_pause, args.device, args.aligner)


if __name__ == "__main__":
    main()
