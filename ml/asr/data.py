"""Prepare ASLP-lab/UrduSpeech for training and evaluation.

    python -m ml.asr.data download --root DATA [--parts corpus/US-CS benchmark] [--limit N]
    python -m ml.asr.data manifest --root DATA
    python -m ml.asr.data audio    --root DATA [--workers 8] [--delete-raw]

Layout under DATA: raw/ (dataset files as published), audio16k/ (16 kHz mono
FLAC), manifests/corpus.jsonl, manifests/benchmark.jsonl, manifests/stats.json.

The corpus has no source-recording identifier. Speaker labels are diarisation
clusters numbered in processing order within a content category, so a
(category, speaker) pair is the smallest unit that keeps one voice in one
recording together. The validation set is made of contiguous runs of those
units so that neighbouring speakers from the same recording stay on one side.
"""

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ml.eval.text import normalise

REPO = "ASLP-lab/UrduSpeech"
REVISION = "16dd380cfd9049a3db7f06a98e878086916bf833"  # dataset version the splits are defined on
HELDOUT_MAX_SECONDS = 28.0  # held-out clips must fit Whisper's window without splitting
MAX_SECONDS = 30.0  # Whisper's input window
SHINGLE = 8  # words; a shared run this long marks a corpus clip as benchmark audio


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def repo_files(raw: Path) -> list[str]:
    """Published file list, cached so that later steps work offline."""
    cache = raw / "_files.txt"
    if not cache.exists():
        from huggingface_hub import HfApi

        raw.mkdir(parents=True, exist_ok=True)
        files = HfApi().list_repo_files(REPO, repo_type="dataset", revision=REVISION)
        cache.write_text("\n".join(files), encoding="utf-8")
    return cache.read_text(encoding="utf-8").splitlines()


def is_transcript(name: str) -> bool:
    return name.endswith(".jsonl") and "paralinguistic" not in name


def local_names(files: list[str]) -> dict[str, str]:
    """Where each published file is stored under raw/.

    The dataset has folders whose names differ only in letter case (PODCAST and
    Podcast), each with its own transcript file. On a case-insensitive disk one
    would overwrite the other, so every file in such a clash gets a numbered name.
    """
    by_folded: dict[str, list[str]] = defaultdict(list)
    for name in files:
        by_folded[name.lower()].append(name)
    names = {name: name for name in files}
    for clash in by_folded.values():
        if len(clash) > 1:
            for number, name in enumerate(sorted(clash), 1):
                path = Path(name)
                names[name] = path.with_name(f"{path.stem}.case{number}{path.suffix}").as_posix()
    return names


def fetch(raw: Path, name: str, local: str) -> str | None:
    """Download one file unless it is already there; return an error message on failure."""
    import shutil
    import tempfile
    import time

    from huggingface_hub import hf_hub_download

    target = raw / local
    if target.exists() and target.stat().st_size > 0:
        return None
    error: Exception | None = None
    for attempt in range(5):
        try:
            if local == name:
                hf_hub_download(REPO, name, repo_type="dataset", revision=REVISION, local_dir=raw)
            else:
                with tempfile.TemporaryDirectory(dir=raw) as scratch:
                    got = hf_hub_download(REPO, name, repo_type="dataset", revision=REVISION,
                                          local_dir=scratch)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(got, target)
            return None
        except Exception as problem:  # network errors vary; retry them all
            error = problem
            time.sleep(2 * (attempt + 1))
    return f"{name}: {error}"


def download(root: Path, parts: list[str], limit: int | None, workers: int = 8) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw = root / "raw"
    files = repo_files(raw)
    names = local_names(files)
    wanted = [f for f in files if is_transcript(f)]  # every subset: needed for the overlap check
    audio = [f for f in files
             if f.lower().endswith(".wav") and any(f.startswith(p + "/") for p in parts)]
    if limit is not None:
        per_folder: dict[str, list[str]] = defaultdict(list)
        for f in sorted(audio):
            per_folder[f.rsplit("/", 1)[0]].append(f)
        audio = [f for group in per_folder.values() for f in group[:limit]]
    todo = wanted + audio
    failed = []
    with ThreadPoolExecutor(workers) as pool:
        jobs = [pool.submit(fetch, raw, name, names[name]) for name in todo]
        for done, job in enumerate(as_completed(jobs), 1):
            if job.result():
                failed.append(job.result())
            if done % 500 == 0 or done == len(todo):
                print(f"{done}/{len(todo)} files, {len(failed)} failed", flush=True)
    if failed:
        for line in failed[:20]:
            print(line)
        raise SystemExit(f"{len(failed)} files failed; run the download step again")
    print(f"{len(wanted)} transcript files and {len(audio)} clips are in {raw}")


def load_rows(raw: Path) -> tuple[list[dict], Counter]:
    """One record per published clip that has both a transcript row and an audio file."""
    files = repo_files(raw)
    names = local_names(files)
    audio = {f.lower(): f for f in files if f.lower().endswith(".wav")}
    dropped: Counter = Counter()
    rows, seen = [], set()
    for name in sorted(f for f in files if is_transcript(f)):
        path = raw / names[name]
        if not path.exists():
            if names[name] != name:
                raise SystemExit(f"{names[name]} is missing; run the download step again")
            continue
        part, subset, length, category = name.split("/")[:4]
        subset = subset.replace("benchmark-", "")
        for record in read_jsonl(path):
            clip = str(record.get("Audio_Clip") or "")
            raw_audio = audio.get(f"{name.rsplit('/', 1)[0]}/audio/{clip}".lower())
            text = record.get("ground_truth") if part == "benchmark" else record.get(
                "Transcription")
            text = " ".join(str(text or "").split())
            if raw_audio is None:
                dropped["no_audio_file"] += 1
            elif raw_audio in seen:
                dropped["duplicate_row"] += 1
            elif not normalise(text):
                dropped["empty_text"] += 1
            else:
                seen.add(raw_audio)
                index = re.search(r"(\d+)\.wav$", clip, re.IGNORECASE)
                row = {
                    "id": f"{part}/{subset}/{length}/{category.lower()}/{Path(clip).stem}",
                    "part": part,
                    "subset": subset,
                    "category": category.lower(),
                    "speaker": str(record.get("Speaker_id") or ""),
                    "clip_index": int(index.group(1)) if index else 0,
                    "raw_audio": raw_audio,
                    "text": text,
                    "duration": float(record.get("Duration_seconds") or 0),
                }
                if part == "benchmark":
                    row["gemini_text"] = " ".join(str(record.get("Transcription") or "").split())
                else:
                    row["confidence"] = float(record.get("Confidence_score") or 0)
                row["group"] = f"{row['category']}/{row['speaker']}"
                rows.append(row)
    return rows, dropped


def shingles(text: str) -> set[str]:
    words = normalise(text).split()
    if len(words) < SHINGLE:
        return {" ".join(words)} if len(words) >= 4 else set()
    return {" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def mark_benchmark_overlap(corpus: list[dict], benchmark: list[dict]) -> int:
    """Exclude every corpus clip that shares a run of words with a benchmark clip.

    This removes benchmark audio that was segmented again for the corpus. It also
    removes a few clips that only repeat a presenter's stock phrase; that costs
    little and needs no similarity threshold. The benchmark and the corpus come
    from the same programmes, so other clips of the same speakers remain.
    """
    test_text: set[str] = set()
    for row in benchmark:
        test_text |= shingles(row["text"]) | shingles(row["gemini_text"])
    leaked = 0
    for row in corpus:
        if shingles(row["text"]) & test_text:
            row["split"] = "excluded"
            row["excluded"] = "benchmark_text_overlap"
            leaked += 1
    return leaked


def assign_validation(corpus: list[dict], fraction: float, seed: int, runs: int = 20) -> None:
    """Give `fraction` of each category to validation as contiguous runs of speaker groups."""
    by_category: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in corpus:
        if row.get("split") != "excluded":
            by_category[row["category"]][row["group"]].append(row)
    rng = random.Random(seed)
    for category in sorted(by_category):
        groups = by_category[category]
        order = sorted(groups, key=lambda g: (min(r["clip_index"] for r in groups[g]), g))
        total = sum(r["duration"] for g in order for r in groups[g])
        chosen = set(rng.sample(range(runs), max(1, round(runs * fraction))))
        elapsed = 0.0
        for group in order:
            run = min(runs - 1, int(runs * elapsed / total)) if total else 0
            for row in groups[group]:
                row["split"] = "val" if run in chosen else "train"
            elapsed += sum(r["duration"] for r in groups[group])


def assign_heldout(corpus: list[dict], hours: float, seed: int, subset: str = "US-CS") -> None:
    """Move a stratified random sample of training clips to the held-out split.

    The sample is drawn from one subset, takes the same share of every content
    category's audio, and holds at least `hours` in total. It is kept out of
    training so that its transcripts can later be corrected by hand and the
    model fine-tuned on either version.
    """
    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in corpus:
        if (row.get("split") == "train" and row["subset"] == subset
                and row["duration"] <= HELDOUT_MAX_SECONDS):
            by_category[row["category"]].append(row)
    eligible = sum(r["duration"] for rows in by_category.values() for r in rows)
    share = min(1.0, hours * 3600 / eligible) if eligible else 0.0
    for category in sorted(by_category):
        rows = sorted(by_category[category], key=lambda r: r["id"])
        random.Random(f"{seed}/{category}").shuffle(rows)
        quota = share * sum(r["duration"] for r in rows)
        taken = 0.0
        for row in rows:
            if taken >= quota:
                break
            row["split"] = "heldout"
            taken += row["duration"]


def fingerprint(rows: list[dict]) -> str:
    """Short hash of every clip's split; equal fingerprints mean identical splits."""
    lines = sorted(f"{r['id']} {r['split']}" for r in rows)
    return hashlib.sha256(";".join(lines).encode()).hexdigest()[:16]


def build_manifest(root: Path, fraction: float, heldout_hours: float, seed: int) -> None:
    rows, dropped = load_rows(root / "raw")
    if len({r["id"] for r in rows}) != len(rows):
        raise SystemExit("two clips share an id; the manifest would be ambiguous")
    corpus = [r for r in rows if r["part"] == "corpus"]
    benchmark = [r for r in rows if r["part"] == "benchmark"]
    for row in benchmark:
        row["split"] = "test"
    leaked = mark_benchmark_overlap(corpus, benchmark)
    assign_validation(corpus, fraction, seed)
    assign_heldout(corpus, heldout_hours, seed)
    carry_audio_fields(root, corpus + benchmark)
    write_jsonl(root / "manifests" / "corpus.jsonl", corpus)
    write_jsonl(root / "manifests" / "benchmark.jsonl", benchmark)
    write_stats(root, corpus + benchmark, {"dropped_rows": dict(dropped),
                                           "benchmark_text_overlap_clips": leaked,
                                           "val_fraction": fraction,
                                           "heldout_hours": heldout_hours, "seed": seed,
                                           "dataset_revision": REVISION,
                                           "split_fingerprint": fingerprint(corpus + benchmark)})


def carry_audio_fields(root: Path, rows: list[dict]) -> None:
    """Keep measured durations and hashes when the manifest is rebuilt after the audio step."""
    done = {}
    for name in ("corpus.jsonl", "benchmark.jsonl"):
        path = root / "manifests" / name
        if path.exists():
            done.update({r["id"]: r for r in read_jsonl(path) if "audio" in r})
    for row in rows:
        if row["id"] in done:
            old = done[row["id"]]
            row.update(audio=old["audio"], duration=old["duration"], sha1=old["sha1"])


def write_stats(root: Path, rows: list[dict], extra: dict) -> None:
    table: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        cell = table[f"{row['subset']}/{row['split']}"]
        cell["clips"] += 1
        cell["hours"] += row["duration"] / 3600
        cell["clips_with_audio"] += "audio" in row
        if row["duration"] > MAX_SECONDS:
            cell["clips_over_30s"] += 1
            cell["hours_over_30s"] += row["duration"] / 3600
    stats = {"splits": {k: {m: round(v, 2) for m, v in cell.items()}
                        for k, cell in sorted(table.items())}, **extra}
    path = root / "manifests" / "stats.json"
    path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))


def convert(job: tuple[str, str]) -> tuple[float, str] | None:
    """Write one clip as 16 kHz mono FLAC; return its duration and a hash of the samples.

    A readable FLAC already at the target is kept and only measured, so the step can
    be repeated after the original WAV files have been deleted.
    """
    import librosa
    import numpy as np
    import soundfile as sf

    source, target = job
    if Path(target).exists():
        try:
            pcm, rate = sf.read(target, dtype="int16")
            if rate == 16000 and pcm.ndim == 1 and len(pcm):
                return len(pcm) / 16000, hashlib.sha1(pcm.astype("<i2").tobytes()).hexdigest()
        except Exception:  # unreadable leftover of an interrupted run: convert again
            pass
    try:
        samples, rate = sf.read(source, dtype="float32", always_2d=True)
        mono = samples.mean(axis=1)
        if rate != 16000:
            mono = librosa.resample(mono, orig_sr=rate, target_sr=16000)
        pcm = np.clip(np.round(mono * 32767), -32768, 32767).astype("<i2")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        sf.write(target, pcm, 16000, format="FLAC", subtype="PCM_16")
        return len(pcm) / 16000, hashlib.sha1(pcm.tobytes()).hexdigest()
    except Exception as error:  # a corrupt clip must not stop the other clips
        print(f"failed {source}: {error}")
        return None


def prepare_audio(root: Path, workers: int, delete_raw: bool) -> None:
    corpus = read_jsonl(root / "manifests" / "corpus.jsonl")
    benchmark = read_jsonl(root / "manifests" / "benchmark.jsonl")
    todo = [r for r in corpus + benchmark if "audio" not in r and (
        (root / "raw" / r["raw_audio"]).exists()
        or (root / "audio16k" / f"{r['id']}.flac").exists())]
    jobs = [(str(root / "raw" / r["raw_audio"]), str(root / "audio16k" / f"{r['id']}.flac"))
            for r in todo]
    with ProcessPoolExecutor(workers) as pool:
        for done, (row, result) in enumerate(zip(todo, pool.map(convert, jobs, chunksize=16))):
            if result:
                row["duration"], row["sha1"] = round(result[0], 3), result[1]
                row["audio"] = f"audio16k/{row['id']}.flac"
                if delete_raw:
                    (root / "raw" / row["raw_audio"]).unlink(missing_ok=True)
            if done % 2000 == 0:
                print(f"{done}/{len(todo)} clips converted")
    test_audio = {r["sha1"] for r in benchmark if "sha1" in r}
    same_audio = 0
    for row in corpus:
        if row.get("sha1") in test_audio and row["split"] != "excluded":
            row["split"], row["excluded"] = "excluded", "benchmark_same_audio"
            same_audio += 1
    write_jsonl(root / "manifests" / "corpus.jsonl", corpus)
    write_jsonl(root / "manifests" / "benchmark.jsonl", benchmark)
    old = json.loads((root / "manifests" / "stats.json").read_text(encoding="utf-8"))
    old.pop("splits", None)
    write_stats(root, corpus + benchmark, {**old, "benchmark_same_audio_clips": same_audio})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("download", "manifest", "audio"):
        commands.add_parser(name).add_argument("--root", type=Path, required=True)
    commands.choices["download"].add_argument(
        "--parts", nargs="+", default=["corpus/US-CS", "benchmark"])
    commands.choices["download"].add_argument(
        "--limit", type=int, help="clips per folder, for a small trial")
    commands.choices["manifest"].add_argument("--val-fraction", type=float, default=0.1)
    commands.choices["manifest"].add_argument(
        "--heldout-hours", type=float, default=5.0,
        help="code-switched training audio set aside for the label-correction experiment")
    commands.choices["manifest"].add_argument("--seed", type=int, default=0)
    commands.choices["audio"].add_argument("--workers", type=int, default=8)
    commands.choices["audio"].add_argument(
        "--delete-raw", action="store_true", help="remove each original WAV once converted")
    args = parser.parse_args()
    if args.command == "download":
        download(args.root, args.parts, args.limit)
    elif args.command == "manifest":
        build_manifest(args.root, args.val_fraction, args.heldout_hours, args.seed)
    else:
        prepare_audio(args.root, args.workers, args.delete_raw)


if __name__ == "__main__":
    main()
