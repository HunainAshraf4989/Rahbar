"""Transcribe a manifest split with one model under one fixed, recorded protocol.

    python -m ml.asr.transcribe --root DATA --name turbo --model openai/whisper-large-v3-turbo --preflight 30
    python -m ml.asr.transcribe --root DATA --name turbo --model openai/whisper-large-v3-turbo
    python -m ml.asr.transcribe --root DATA --split val --subsets US-CS --name mine --model RUNS/x/best

The protocol is the same for every backend:
- audio longer than 30 s is cut at its quietest 0.2 s into pieces of at most 30 s;
  each piece is transcribed on its own and the texts are joined with a space;
- decoding is greedy: no beam search, no sampling, no temperature fallback;
  --decoding fallback adds Whisper's own temperature fallback (Whisper models only)
  as a second condition;
- output is capped at 30 tokens per second of audio plus 20, and at most 440 tokens,
  per piece (references need at most 30 per second); a clip whose output reached
  the cap is marked hit_cap, so runaway repetition is counted, not hidden;
- Whisper and Seamless run in 16-bit on a GPU, MMS in 32-bit.

Output: DATA/hyps/<split>/<name>.jsonl with one {"id", "text", "pieces", "hit_cap"}
per clip ("language" too when Whisper detects it), and <name>.json with the
settings, versions, timings and peak GPU memory. A run that stops continues where it
stopped, but only with the same settings. If the GPU runs out of memory the batch
size is halved and the run goes on. --preflight N transcribes N clips per subset
(the longest half and a random half) into hyps/<split>/preflight/, then prints the
output checks and the estimated time for the whole split.
"""

import argparse
import json
import math
import platform
import random
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from ml.asr.data import read_jsonl
from ml.eval.sanity import checks, show

PROTOCOL = "2"
RATE = 16000
WINDOW = 30.0
MAX_TOKENS = 440  # Whisper's decoder holds 448 positions, 4 of them taken by the prompt
# Whisper's own decoding recipe: redo an output at a higher temperature when it repeats
# itself (zlib ratio of its tokens above 1.35, the token-level equivalent of 2.4 on text)
# or when the model is unsure of it (mean log-probability below -1). The no-speech skip
# is left out: every benchmark clip contains speech, so skipping one only deletes words.
FALLBACK = {"temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0), "compression_ratio_threshold": 1.35,
            "logprob_threshold": -1.0}
DECODING = {"greedy": "greedy",
            "fallback": "greedy, then temperature fallback 0.2-1.0 when the token compression "
                        "ratio exceeds 1.35 or the mean log-probability is below -1; seed 0"}


@dataclass
class Output:
    text: str
    hit_cap: bool = False
    language: str | None = None


Transcriber = Callable[[list[np.ndarray]], list[Output]]


def token_cap(seconds: float) -> int:
    return min(MAX_TOKENS, math.ceil(30 * seconds) + 20)


def pieces(samples: np.ndarray, window: float = WINDOW) -> list[np.ndarray]:
    """Cut audio longer than `window` at its quietest 0.2 s so that every piece fits."""
    limit = int(window * RATE)
    if len(samples) <= limit:
        return [samples]
    low = len(samples) - limit if len(samples) <= 2 * limit else limit // 2
    half = RATE // 10
    energy = np.concatenate(([0.0], np.cumsum(samples.astype(np.float64) ** 2)))
    at = np.arange(low, limit + 1)
    level = energy[np.minimum(at + half, len(samples))] - energy[np.maximum(at - half, 0)]
    cut = int(at[np.argmin(level)])
    return [samples[:cut]] + pieces(samples[cut:], window)


def load(path: Path) -> np.ndarray:
    samples, rate = sf.read(path, dtype="float32")
    if rate != RATE or samples.ndim != 1:
        raise SystemExit(f"{path} is not 16 kHz mono; run ml.asr.data audio first")
    return samples


def half(args: argparse.Namespace) -> torch.dtype:
    return torch.float16 if args.device == "cuda" else torch.float32


def whisper(args: argparse.Namespace) -> tuple[Transcriber, dict]:
    from transformers import GenerationConfig, WhisperForConditionalGeneration, WhisperProcessor

    dtype = half(args)
    model = WhisperForConditionalGeneration.from_pretrained(args.model, dtype=dtype)
    revision = getattr(model.config, "_commit_hash", None)
    if args.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    model = model.to(args.device).eval()
    processor = WhisperProcessor.from_pretrained(args.model)
    config = (GenerationConfig.from_pretrained(args.generation_config) if args.generation_config
              else model.generation_config)
    if not getattr(config, "lang_to_id", None):
        raise SystemExit(f"{args.model} has an old generation config without language tokens; "
                         "add --generation-config with the base model it was trained from")
    config.forced_decoder_ids = None
    options = {"task": "transcribe", "num_beams": 1, "return_timestamps": False}
    if args.decoding == "fallback":
        options.update(FALLBACK)
    else:
        options["do_sample"] = False
    if args.language != "auto":
        options["language"] = args.language
    languages = {token: name.strip("<|>") for name, token in config.lang_to_id.items()}
    text_end = config.eos_token_id  # Whisper's text tokens all come before end-of-text

    def run(audio: list[np.ndarray]) -> list[Output]:
        cap = token_cap(max(len(a) for a in audio) / RATE)
        features = processor.feature_extractor(audio, sampling_rate=RATE,
                                               return_tensors="pt").input_features
        with torch.inference_mode():
            ids = model.generate(features.to(args.device, dtype), generation_config=config,
                                 max_new_tokens=cap, **options)
        ids = getattr(ids, "sequences", ids)  # the log-probability test returns a dict
        out = []
        for row in ids.tolist():
            found = next((languages[t] for t in row if t in languages), None)
            out.append(Output(processor.tokenizer.decode(row, skip_special_tokens=True),
                              sum(t < text_end for t in row) >= cap, found))
        return out

    return run, {"dtype": str(dtype), "model_revision": revision,
                 "decoding": DECODING[args.decoding],
                 "max_new_tokens": "min(440, ceil(30 * seconds) + 20)"}


def mms(args: argparse.Namespace) -> tuple[Transcriber, dict]:
    from transformers import AutoProcessor, Wav2Vec2ForCTC

    processor = AutoProcessor.from_pretrained(args.model)
    model = Wav2Vec2ForCTC.from_pretrained(args.model, dtype=torch.float32)
    revision = getattr(model.config, "_commit_hash", None)
    processor.tokenizer.set_target_lang("urd-script_arabic")
    model.load_adapter("urd-script_arabic")
    model = model.to(args.device).eval()

    def run(audio: list[np.ndarray]) -> list[Output]:
        inputs = processor(audio, sampling_rate=RATE, return_tensors="pt", padding=True)
        if "attention_mask" not in inputs and len(audio) > 1:  # padding would be decoded too
            return [o for a in audio for o in run([a])]
        with torch.inference_mode():
            logits = model(**inputs.to(args.device)).logits
        if "attention_mask" in inputs:  # decode only the frames of real audio
            lengths = model._get_feat_extract_output_lengths(inputs["attention_mask"].sum(-1))
        else:
            lengths = [logits.shape[1]]
        best = logits.argmax(dim=-1).cpu()
        return [Output(processor.decode(best[i, :int(n)])) for i, n in enumerate(lengths)]

    return run, {"dtype": "torch.float32", "model_revision": revision,
                 "decoding": "greedy CTC", "max_new_tokens": "not applicable"}


def seamless(args: argparse.Namespace) -> tuple[Transcriber, dict]:
    from transformers import AutoProcessor, SeamlessM4Tv2ForSpeechToText

    processor = AutoProcessor.from_pretrained(args.model)
    dtype = half(args)
    model = SeamlessM4Tv2ForSpeechToText.from_pretrained(args.model, dtype=dtype)
    revision = getattr(model.config, "_commit_hash", None)
    model = model.to(args.device).eval()
    end = model.generation_config.eos_token_id

    def run(audio: list[np.ndarray]) -> list[Output]:
        cap = token_cap(max(len(a) for a in audio) / RATE)
        inputs = processor(audio=audio, sampling_rate=RATE, return_tensors="pt",
                           padding=True).to(args.device, dtype)
        with torch.inference_mode():
            ids = model.generate(**inputs, tgt_lang="urd", num_beams=1, do_sample=False,
                                 max_new_tokens=cap)
        texts = processor.batch_decode(ids, skip_special_tokens=True)
        return [Output(t, end not in row[1:]) for t, row in zip(texts, ids.tolist())]

    return run, {"dtype": str(dtype), "model_revision": revision, "decoding": "greedy",
                 "max_new_tokens": "min(440, ceil(30 * seconds) + 20)"}


def omnilingual(args: argparse.Namespace) -> tuple[Transcriber, dict]:
    import tempfile

    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

    pipe = ASRInferencePipeline(model_card=args.model)

    def run(audio: list[np.ndarray]) -> list[Output]:
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for i, a in enumerate(audio):
                paths.append(str(Path(folder) / f"{i}.wav"))
                sf.write(paths[-1], a, RATE)
            texts = pipe.transcribe(paths, lang=["urd_Arab"] * len(paths), batch_size=len(paths))
        return [Output(t) for t in texts]

    return run, {"dtype": "package default", "model_revision": None,
                 "decoding": "package default", "max_new_tokens": "package default"}


BACKENDS = {"whisper": whisper, "mms": mms, "seamless": seamless, "omnilingual": omnilingual}
SAME_RUN = ("protocol", "backend", "model", "adapter", "language", "generation_config",
            "long_clips", "decoding_option", "decoding", "max_new_tokens", "dtype",
            "model_revision")
BEFORE_OPTIONS = {"decoding_option": "greedy"}  # files written before --decoding existed


def settings(args: argparse.Namespace) -> dict:
    return {"protocol": PROTOCOL, "backend": args.backend, "model": args.model,
            "adapter": args.adapter, "language": args.language,
            "generation_config": args.generation_config, "decoding_option": args.decoding,
            "long_clips": f"cut at the quietest 0.2 s into pieces of at most {WINDOW:g} s"}


def check_resume(out: Path, meta: Path, wanted: dict) -> dict:
    """Settings already recorded for this output; stop if they differ from `wanted`."""
    if not out.exists():
        return {}
    if not meta.exists():
        raise SystemExit(f"{out} was written by an older transcribe that did not record its "
                         f"settings. Keep it under another name (for example "
                         f"{out.stem}-initial.jsonl) or delete it, then run again.")
    recorded = json.loads(meta.read_text(encoding="utf-8"))
    differ = [k for k in SAME_RUN
              if k in wanted and recorded.get(k, BEFORE_OPTIONS.get(k)) != wanted[k]]
    if differ:
        raise SystemExit(f"{out} was made with different settings ({', '.join(differ)}); "
                         "use another --name")
    return recorded


def preflight_rows(rows: list[dict], n: int) -> list[dict]:
    chosen = []
    for subset in sorted({r["subset"] for r in rows}):
        part = sorted((r for r in rows if r["subset"] == subset), key=lambda r: -r["duration"])
        longest = part[:n // 2]
        rest = part[n // 2:]
        chosen += longest + random.Random(0).sample(rest, min(len(rest), n - len(longest)))
    return chosen


def environment(args: argparse.Namespace) -> dict:
    import transformers

    try:
        code = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        code = "unknown"
    gpu = torch.cuda.get_device_name(0) if args.device == "cuda" else "cpu"
    return {"device": gpu, "python": platform.python_version(), "torch": torch.__version__,
            "transformers": transformers.__version__, "code_revision": code,
            "batch_size": args.batch_size, "started": time.strftime("%Y-%m-%d %H:%M:%S")}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--split", choices=["test", "val"], default="test")
    p.add_argument("--subsets", nargs="+", default=["US-CS", "US-Std", "US-EngPk"])
    p.add_argument("--name", required=True, help="label for this system in the results")
    p.add_argument("--model", required=True, help="model name, folder or omnilingual model card")
    p.add_argument("--backend", choices=sorted(BACKENDS), default="whisper")
    p.add_argument("--adapter", help="LoRA adapter to apply to a Whisper model")
    p.add_argument("--language", default="ur", help="Whisper language token, or 'auto'")
    p.add_argument("--generation-config", help="take Whisper's generation config from this model")
    p.add_argument("--decoding", choices=sorted(DECODING), default="greedy",
                   help="'fallback' adds Whisper's temperature fallback (Whisper models only)")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--preflight", type=int, metavar="N", help="trial on N clips per subset")
    args = p.parse_args()
    if args.decoding == "fallback" and args.backend != "whisper":
        raise SystemExit("--decoding fallback exists only for Whisper models")
    torch.manual_seed(0)  # the fallback samples; a fixed seed makes a run repeatable

    manifest = "benchmark.jsonl" if args.split == "test" else "corpus.jsonl"
    rows = [r for r in read_jsonl(args.root / "manifests" / manifest)
            if r["split"] == args.split and r["subset"] in args.subsets]
    missing = [r["id"] for r in rows if "audio" not in r]
    if missing:
        raise SystemExit(f"{len(missing)} clips have no audio (for example {missing[0]}); "
                         "a result on part of the split is not comparable")
    folder = args.root / "hyps" / args.split
    if args.preflight:
        everything = rows
        rows = preflight_rows(rows, args.preflight)
        folder = folder / "preflight"
        for old in folder.glob(f"{args.name}.json*"):
            old.unlink()
    out = folder / f"{args.name}.jsonl"
    meta = out.with_suffix(".json")
    folder.mkdir(parents=True, exist_ok=True)
    wanted = settings(args)
    recorded = check_resume(out, meta, wanted)
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    todo = sorted((r for r in rows if r["id"] not in done), key=lambda r: -r["duration"])
    print(f"{len(done)} clips already transcribed, {len(todo)} to do")
    if todo:
        transcribe, details = BACKENDS[args.backend](args)
        wanted.update(details)
        check_resume(out, meta, wanted)
        run_log = environment(args)
        record = {**wanted, "runs": recorded.get("runs", []) + [run_log]}
        meta.write_text(json.dumps(record, indent=2), encoding="utf-8")  # before any output
        stats = run_all(args, todo, transcribe, out)
        run_log.update(stats, finished=time.strftime("%Y-%m-%d %H:%M:%S"))
        meta.write_text(json.dumps(record, indent=2), encoding="utf-8")
    hyps = {h["id"]: h for h in read_jsonl(out)}
    for subset in sorted({r["subset"] for r in rows}):
        part = [r for r in rows if r["subset"] == subset and r["id"] in hyps]
        show(args.name, subset, checks(part, hyps))
    if args.preflight and todo:
        estimate(everything, rows, stats)


def run_all(args, todo: list[dict], transcribe: Transcriber, out: Path) -> dict:
    size, start, first_batch_end = args.batch_size, 0, None
    timed_clips, timed_audio, cut_clips = 0, 0.0, 0
    started = time.time()
    with out.open("a", encoding="utf-8", newline="\n") as sink:
        while start < len(todo):
            batch = todo[start:start + size]
            audio = [pieces(load(args.root / r["audio"])) for r in batch]
            flat = [a for clip in audio for a in clip]
            try:
                results = iter(transcribe(flat))
            except torch.cuda.OutOfMemoryError:
                if size == 1:
                    raise
                torch.cuda.empty_cache()
                size //= 2
                print(f"out of GPU memory; batch size is now {size}")
                continue
            for row, clip in zip(batch, audio):
                parts = [next(results) for _ in clip]
                line = {"id": row["id"], "text": " ".join(o.text.strip() for o in parts).strip(),
                        "pieces": len(clip), "hit_cap": any(o.hit_cap for o in parts)}
                languages = {o.language for o in parts if o.language}
                if args.language == "auto" and languages:
                    line["language"] = ",".join(sorted(languages))
                sink.write(json.dumps(line, ensure_ascii=False) + "\n")
                cut_clips += len(clip) > 1
            sink.flush()
            start += len(batch)
            if first_batch_end is None:
                first_batch_end = time.time()
                warn_if_spilling(args)
            else:
                timed_clips += len(batch)
                timed_audio += sum(r["duration"] for r in batch)
            if (start // size) % 20 == 0 or start == len(todo):
                rate = (time.time() - started) / start
                print(f"{start}/{len(todo)} clips, about {rate * (len(todo) - start) / 60:.0f} "
                      "min left", flush=True)
    elapsed = time.time() - started
    audio_hours = sum(r["duration"] for r in todo) / 3600
    after_first = time.time() - first_batch_end
    peak = torch.cuda.max_memory_allocated() / 2**30 if args.device == "cuda" else None
    print(f"{audio_hours:.2f} h of audio in {elapsed / 60:.1f} min (real-time factor "
          f"{elapsed / (audio_hours * 3600):.3f}); final batch size {size}"
          + (f"; peak GPU memory {peak:.1f} GiB" if peak else ""))
    return {"clips": len(todo), "audio_hours": round(audio_hours, 3),
            "minutes": round(elapsed / 60, 1), "real_time_factor": round(elapsed / (audio_hours * 3600), 4),
            "final_batch_size": size, "clips_cut_over_30s": cut_clips,
            "peak_gpu_gib": round(peak, 2) if peak else None,
            "seconds_per_clip_after_first_batch": after_first / timed_clips if timed_clips else None,
            "seconds_per_audio_second_after_first_batch": after_first / timed_audio if timed_audio else None}


def warn_if_spilling(args: argparse.Namespace) -> None:
    """Windows can lend system memory to the GPU: no error, but everything runs far slower."""
    if args.device != "cuda":
        return
    total = torch.cuda.get_device_properties(0).total_memory
    if torch.cuda.max_memory_allocated() > 0.95 * total:
        print("WARNING: this batch used more memory than the GPU has; Windows is lending system "
              "memory and the run will be very slow. Stop and use a smaller --batch-size.")


def estimate(everything: list[dict], sample: list[dict], stats: dict) -> None:
    per_clip, per_second = (stats["seconds_per_clip_after_first_batch"],
                            stats["seconds_per_audio_second_after_first_batch"])
    if not per_clip:
        print("too few clips to estimate the full run; use a larger --preflight")
        return
    clips = len(everything)
    seconds = sum(r["duration"] for r in everything)
    guess = max(per_clip * clips, per_second * seconds)
    print(f"\nestimate for all {clips} clips ({seconds / 3600:.2f} h): about {guess / 60:.0f} min "
          "(the trial includes the longest clips, so this is on the high side)")


if __name__ == "__main__":
    main()
