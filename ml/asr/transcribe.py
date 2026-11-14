"""Transcribe a manifest split with one model and save the hypotheses.

    python -m ml.asr.transcribe --root DATA --split test --name turbo --model openai/whisper-large-v3-turbo
    python -m ml.asr.transcribe --root DATA --split test --name mine  --model RUNS/turbo-full/best

Output: DATA/hyps/<split>/<name>.jsonl, one {"id", "text"} per clip. An interrupted
run continues where it stopped. Backends: whisper (any Whisper checkpoint, optionally
with a LoRA adapter), mms, seamless and omnilingual.
"""

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

import soundfile as sf
import torch

from ml.asr.data import read_jsonl

Transcriber = Callable[[list[Path]], list[str]]


def load(path: Path):
    samples, rate = sf.read(path, dtype="float32")
    assert rate == 16000, f"{path} is {rate} Hz; run ml.asr.data audio first"
    return samples


def whisper(args: argparse.Namespace) -> Transcriber:
    from transformers import WhisperForConditionalGeneration, WhisperProcessor, pipeline

    dtype = torch.float16 if args.device == "cuda" else torch.float32
    model = WhisperForConditionalGeneration.from_pretrained(args.model, torch_dtype=dtype)
    if args.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    processor = WhisperProcessor.from_pretrained(args.model)
    pipe = pipeline("automatic-speech-recognition", model=model, tokenizer=processor.tokenizer,
                    feature_extractor=processor.feature_extractor, device=args.device,
                    torch_dtype=dtype, chunk_length_s=30)
    options = {"task": "transcribe"}
    if args.language != "auto":
        options["language"] = args.language

    def run(paths: list[Path]) -> list[str]:
        clips = [{"raw": load(p), "sampling_rate": 16000} for p in paths]
        return [o["text"] for o in pipe(clips, batch_size=len(clips), generate_kwargs=options)]

    return run


def mms(args: argparse.Namespace) -> Transcriber:
    from transformers import AutoProcessor, Wav2Vec2ForCTC

    processor = AutoProcessor.from_pretrained(args.model)
    model = Wav2Vec2ForCTC.from_pretrained(args.model).to(args.device).eval()
    processor.tokenizer.set_target_lang("urd-script_arabic")
    model.load_adapter("urd-script_arabic")

    def run(paths: list[Path]) -> list[str]:
        inputs = processor([load(p) for p in paths], sampling_rate=16000, return_tensors="pt",
                           padding=True).to(args.device)
        with torch.inference_mode():
            return processor.batch_decode(model(**inputs).logits.argmax(dim=-1))

    return run


def seamless(args: argparse.Namespace) -> Transcriber:
    from transformers import AutoProcessor, SeamlessM4Tv2ForSpeechToText

    processor = AutoProcessor.from_pretrained(args.model)
    model = SeamlessM4Tv2ForSpeechToText.from_pretrained(args.model).to(args.device).eval()

    def run(paths: list[Path]) -> list[str]:
        inputs = processor(audio=[load(p) for p in paths], sampling_rate=16000,
                           return_tensors="pt", padding=True).to(args.device)
        with torch.inference_mode():
            tokens = model.generate(**inputs, tgt_lang="urd")
        return processor.batch_decode(tokens, skip_special_tokens=True)

    return run


def omnilingual(args: argparse.Namespace) -> Transcriber:
    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

    pipe = ASRInferencePipeline(model_card=args.model)

    def run(paths: list[Path]) -> list[str]:
        return pipe.transcribe([str(p) for p in paths], lang=["urd_Arab"] * len(paths),
                               batch_size=len(paths))

    return run


BACKENDS = {"whisper": whisper, "mms": mms, "seamless": seamless, "omnilingual": omnilingual}


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
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = p.parse_args()

    manifest = "benchmark.jsonl" if args.split == "test" else "corpus.jsonl"
    rows = [r for r in read_jsonl(args.root / "manifests" / manifest)
            if r["split"] == args.split and r["subset"] in args.subsets and "audio" in r]
    out = args.root / "hyps" / args.split / f"{args.name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    todo = sorted((r for r in rows if r["id"] not in done), key=lambda r: -r["duration"])
    print(f"{len(done)} clips already transcribed, {len(todo)} to do")
    if not todo:
        return
    transcribe = BACKENDS[args.backend](args)
    audio_seconds, started = 0.0, time.time()
    with out.open("a", encoding="utf-8", newline="\n") as sink:
        for start in range(0, len(todo), args.batch_size):
            batch = todo[start:start + args.batch_size]
            texts = transcribe([args.root / r["audio"] for r in batch])
            for row, text in zip(batch, texts):
                sink.write(json.dumps({"id": row["id"], "text": text.strip()},
                                      ensure_ascii=False) + "\n")
            sink.flush()
            audio_seconds += sum(r["duration"] for r in batch)
            if (start // args.batch_size) % 20 == 0:
                print(f"{start + len(batch)}/{len(todo)} clips")
    elapsed = time.time() - started
    print(f"{audio_seconds / 3600:.2f} h of audio in {elapsed / 60:.1f} min "
          f"(real-time factor {elapsed / audio_seconds:.3f}) on {args.device}")


if __name__ == "__main__":
    main()
