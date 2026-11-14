"""Split corpus clips longer than Whisper's 30 s window into two aligned parts.

    python -m ml.asr.split_long --root DATA [--min-score -2.0] [--device cuda]

Each long clip is force-aligned to its transcript with the MMS aligner on
romanised text, then cut at the longest pause that leaves both parts within
30 s. The two parts replace the clip in manifests/corpus.jsonl and keep every
word and every sample. A clip whose alignment is poor, or that has no usable
pause, stays whole and is left out of training. Benchmark clips are never split.
Each part records the alignment score, the pause length and the loudness at the
cut relative to the clip (cut_energy), so that doubtful cuts can be reviewed.
"""

import argparse
import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from ml.asr.data import MAX_SECONDS, read_jsonl, write_jsonl

ALIGNER = "MahmoudAshraf/mms-300m-1130-forced-aligner"
FRAME = 0.02  # seconds per aligner output frame


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
        spelt = [re.sub("[^a-z']", "", self.roman.romanize_string(w).lower()) for w in words]
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
    """Index of the last word of part one, the cut time and the pause length."""
    best = None
    timed = [(i, t) for i, t in enumerate(times) if t]
    for (left, (_, end)), (right, (start, _)) in zip(timed, timed[1:]):
        cut = (end + start) / 2
        if duration - MAX_SECONDS < cut < MAX_SECONDS and (best is None or start - end > best[2]):
            best = (right - 1, cut, start - end)
    return best


def split_rows(root: Path, min_score: float, min_pause: float, device: str, model: str) -> None:
    manifest = root / "manifests" / "corpus.jsonl"
    rows = read_jsonl(manifest)
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
                            "parent": row["id"], "align_score": round(aligned[1], 3),
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--min-score", type=float, default=-2.0,
                        help="lowest mean log-probability accepted for an alignment")
    parser.add_argument("--min-pause", type=float, default=0.1, help="shortest pause to cut in")
    parser.add_argument("--aligner", default=ALIGNER, help="model name or local folder")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()
    split_rows(args.root, args.min_score, args.min_pause, args.device, args.aligner)


if __name__ == "__main__":
    main()
