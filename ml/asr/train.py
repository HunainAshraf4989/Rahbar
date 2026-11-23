"""Fine-tune a Whisper-family model on the prepared UrduSpeech manifests.

    python -m ml.asr.train --root DATA --base openai/whisper-large-v3-turbo --out RUNS/turbo-full

Run the same command again to resume an interrupted run from its last checkpoint;
a resume with different settings or a changed manifest is refused. The validation
set is scored during training with greedy decoding and the checkpoint with the
lowest validation WER is saved to OUT/best. The benchmark is never read here.

--preflight trains 10 steps on the clips with the longest transcripts (the most
memory a batch can need) and validates on the 32 validation clips with the longest
transcripts, in OUT/preflight, then prints peak GPU memory, seconds per step and
the estimated time of the full run. Training stops by itself if the loss becomes
not-a-number.
"""

import argparse
import hashlib
import json
import math
import platform
import random
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from transformers import (
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    TrainerCallback,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)

from ml.asr.data import MAX_SECONDS, read_jsonl
from ml.eval.metrics import clip_counts, rates
from ml.eval.text import normalise

MAX_LABEL_TOKENS = 448  # Whisper's decoder length
# Arguments that must not change when a run is resumed.
FIXED = ("base", "method", "lora_r", "lora_scope", "language", "subsets", "train_split",
         "labels", "train_hours", "whole_clips_only", "max_steps", "batch_size", "grad_accum",
         "lr", "warmup_steps", "eval_steps", "eval_clips", "seed", "gradient_checkpointing")


def select_rows(args: argparse.Namespace, processor: WhisperProcessor) -> tuple[list, list, dict]:
    """Training and validation rows that Whisper can take, and what was left out."""
    usable, left_out = [], {"no_audio": 0, "over_30s": 0, "label_too_long": 0, "no_label": 0}
    labels = {r["id"]: r["text"] for r in read_jsonl(args.labels)} if args.labels else None
    for row in read_jsonl(args.root / "manifests" / "corpus.jsonl"):
        if row["split"] not in (args.train_split, "val") or row["subset"] not in args.subsets:
            continue
        if labels is not None and row["split"] == args.train_split:
            if not normalise(labels.get(row["id"], "")):
                left_out["no_label"] += 1
                continue
            row["text"] = labels[row["id"]]
        if args.whole_clips_only and "parent" in row:
            continue
        if "audio" not in row:
            left_out["no_audio"] += 1
        elif row["duration"] > MAX_SECONDS:
            left_out["over_30s"] += 1
        elif len(processor.tokenizer(row["text"]).input_ids) > MAX_LABEL_TOKENS:
            left_out["label_too_long"] += 1
        else:
            usable.append(row)
    rng = random.Random(args.seed)
    train = [r for r in usable if r["split"] == args.train_split]
    rng.shuffle(train)
    if args.train_hours:
        kept, seconds = [], 0.0
        for row in train:
            if seconds >= args.train_hours * 3600:
                break
            kept.append(row)
            seconds += row["duration"]
        train = kept
    val = sorted((r for r in usable if r["split"] == "val"), key=lambda r: r["id"])
    random.Random(0).shuffle(val)  # the same validation clips for every run
    val = val[:args.eval_clips]
    if args.preflight:  # the batches that need the most memory and the longest outputs
        steps = 10 * args.batch_size * args.grad_accum
        train = sorted(train, key=lambda r: -len(r["text"]))[:steps]
        val = sorted((r for r in usable if r["split"] == "val"), key=lambda r: -len(r["text"]))[:32]
    summary = {
        "train_clips": len(train), "train_hours": round(sum(r["duration"] for r in train) / 3600, 2),
        "val_clips": len(val), "val_hours": round(sum(r["duration"] for r in val) / 3600, 2),
        "left_out": left_out,
    }
    return train, val, summary


class Clips(torch.utils.data.Dataset):
    def __init__(self, rows: list[dict], root: Path, processor: WhisperProcessor):
        self.rows, self.root, self.processor = rows, root, processor

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict:
        row = self.rows[index]
        samples, rate = sf.read(self.root / row["audio"], dtype="float32")
        assert rate == 16000, f"{row['audio']} is {rate} Hz; run ml.asr.data audio first"
        features = self.processor.feature_extractor(samples, sampling_rate=16000).input_features[0]
        return {"input_features": features, "labels": self.processor.tokenizer(row["text"]).input_ids}


@dataclass
class Collator:
    processor: WhisperProcessor
    start_token: int

    def __call__(self, items: list[dict]) -> dict:
        batch = {"input_features": torch.tensor(np.stack([i["input_features"] for i in items]))}
        labels = self.processor.tokenizer.pad(
            {"input_ids": [i["labels"] for i in items]}, return_tensors="pt")
        ids = labels["input_ids"].masked_fill(labels["attention_mask"].ne(1), -100)
        if (ids[:, 0] == self.start_token).all():  # the model adds the start token itself
            ids = ids[:, 1:]
        batch["labels"] = ids
        return batch


def build_model(args: argparse.Namespace) -> WhisperForConditionalGeneration:
    # float32 weights: transformers 5 would otherwise load the fp16 checkpoint as fp16, and
    # generation during validation then fails (float32 features vs fp16 weights). The
    # fp16=True training argument still runs the maths in mixed precision.
    model = WhisperForConditionalGeneration.from_pretrained(args.base, dtype=torch.float32)
    model.generation_config.language = args.language
    model.generation_config.task = "transcribe"
    model.generation_config.forced_decoder_ids = None
    model.generation_config.num_beams = 1  # greedy validation, as in ml.asr.transcribe
    model.generation_config.do_sample = False
    model.config.use_cache = False  # training with gradient checkpointing cannot use the cache
    model.generation_config.use_cache = True  # but validation generation is far too slow without it
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
    if args.method == "lora":
        from peft import LoraConfig, get_peft_model

        layers = "q_proj|k_proj|v_proj|out_proj|fc1|fc2"
        where = ".*decoder.*" if args.lora_scope == "decoder" else ".*"
        config = LoraConfig(r=args.lora_r, lora_alpha=2 * args.lora_r, lora_dropout=0.05,
                            target_modules=f"{where}({layers})")
        model = get_peft_model(model, config)
        model.print_trainable_parameters()
    return model


def save_validation_outputs(folder: Path, step: int, rows: list[dict], references: list[str],
                            hypotheses: list[str]) -> None:
    """Keep what the model wrote at every validation pass, so curves can be re-scored later.

    The clips come back in the order of `rows` (one process, no shuffling). Never stops a run.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ids = ([r["id"] for r in rows] if len(rows) == len(hypotheses)
               else [None] * len(hypotheses))
        with (folder / f"step-{step}.jsonl").open("w", encoding="utf-8") as file:
            for clip, reference, hypothesis in zip(ids, references, hypotheses):
                file.write(json.dumps({"id": clip, "reference": reference, "text": hypothesis},
                                      ensure_ascii=False) + "\n")
    except Exception as error:  # a full disk must not end a 20-hour run
        print(f"WARNING: could not save the validation outputs: {error}")


def git_revision() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check_data(summary: dict, rows: int) -> None:
    """Stop before training on a manifest that silently lost clips."""
    left = summary["left_out"]
    if left["no_audio"]:
        raise SystemExit(f"{left['no_audio']} clips have no audio; run `ml.asr.data audio` "
                         "(and split_long after it) before training")
    if left["over_30s"] > 0.02 * (rows + left["over_30s"]):
        raise SystemExit(f"{left['over_30s']} clips are over 30 s and would be left out; "
                         "the manifest looks unsplit, so run ml.asr.split_long first")


def check_resume(out: Path, record: dict) -> None:
    """Refuse to continue a run whose settings or data have changed since it started."""
    previous = out / "run.json"
    if not any(out.glob("checkpoint-*")) or not previous.exists():
        return
    old = json.loads(previous.read_text(encoding="utf-8"))
    differ = [k for k in FIXED if old["args"].get(k) != record["args"].get(k)]
    if old.get("manifest_sha1") not in (None, record["manifest_sha1"]):
        differ.append("the manifest")
    if differ:
        raise SystemExit(f"{out} was started with different {', '.join(differ)}; "
                         "use another --out, or delete the folder to start again")


def environment(root: Path) -> dict:
    import accelerate
    import peft
    import transformers

    manifest = root / "manifests" / "corpus.jsonl"
    stats = json.loads((root / "manifests" / "stats.json").read_text(encoding="utf-8"))
    return {"code_revision": git_revision(), "python": platform.python_version(),
            "torch": torch.__version__, "transformers": transformers.__version__,
            "peft": peft.__version__, "accelerate": accelerate.__version__,
            "split_fingerprint": stats.get("split_fingerprint"),
            "manifest_sha1": hashlib.sha1(manifest.read_bytes()).hexdigest(),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}


class StepTimer(TrainerCallback):
    """Wall time of each training step, for the preflight estimate."""

    def __init__(self):
        self.times: list[float] = []
        self.last: float | None = None

    def on_step_end(self, args, state, control, **kwargs):
        now = time.time()
        if self.last is not None:
            self.times.append(now - self.last)
        self.last = now


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, required=True, help="folder prepared by ml.asr.data")
    p.add_argument("--base", required=True, help="model name or folder to start from")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--method", choices=["full", "lora"], default="full")
    p.add_argument("--lora-r", type=int, default=32)
    p.add_argument("--lora-scope", choices=["all", "decoder"], default="all",
                   help="'decoder' leaves the encoder untouched: faster, less memory")
    p.add_argument("--language", default="ur", help="Whisper language token used for every clip")
    p.add_argument("--subsets", nargs="+", default=["US-CS"])
    p.add_argument("--train-split", choices=["train", "heldout"], default="train")
    p.add_argument("--labels", type=Path,
                   help="file of {id, text} lines that replaces the training transcripts")
    p.add_argument("--train-hours", type=float, help="train on a random subset of this size")
    p.add_argument("--whole-clips-only", action="store_true",
                   help="leave out the parts made by ml.asr.split_long")
    p.add_argument("--max-steps", type=int, required=True)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--grad-accum", type=int, default=1)
    p.add_argument("--lr", type=float, help="default 1e-5 for full, 1e-4 for lora")
    p.add_argument("--warmup-steps", type=int, default=500)
    p.add_argument("--eval-steps", type=int, default=500)
    p.add_argument("--eval-clips", type=int, default=600)
    p.add_argument("--eval-batch-size", type=int, default=16)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--gradient-checkpointing", action="store_true")
    p.add_argument("--keep-checkpoints", action="store_true",
                   help="keep every checkpoint, not only the best and the latest")
    p.add_argument("--preflight", action="store_true",
                   help="10 worst-case steps in OUT/preflight and a time estimate for the run")
    args = p.parse_args()
    lr = args.lr or (1e-5 if args.method == "full" else 1e-4)
    planned_steps, planned_eval_steps, out = args.max_steps, args.eval_steps, args.out
    if args.preflight:
        out = args.out / "preflight"
        shutil.rmtree(out, ignore_errors=True)
        args.max_steps, args.eval_steps, args.warmup_steps = 10, 10, 0

    processor = WhisperProcessor.from_pretrained(args.base, language=args.language,
                                                 task="transcribe")
    train_rows, val_rows, data_summary = select_rows(args, processor)
    print(json.dumps(data_summary, indent=2))
    if not train_rows or not val_rows:
        raise SystemExit("no usable training or validation clips; check the manifests")
    check_data(data_summary, len(train_rows) + len(val_rows))
    record = {"args": {k: str(v) for k, v in vars(args).items()}, "learning_rate": lr,
              "data": data_summary, **environment(args.root),
              "precision": "float32 weights, float16 autocast" if torch.cuda.is_available()
              else "float32",
              "validation_decoding": f"greedy, at most {MAX_LABEL_TOKENS} tokens"}
    check_resume(out, record)
    model = build_model(args)
    record["trainable_parameters"] = sum(p.numel() for p in model.parameters() if p.requires_grad)

    def compute_metrics(prediction) -> dict:
        labels = np.where(prediction.label_ids == -100, processor.tokenizer.pad_token_id,
                          prediction.label_ids)
        references = processor.tokenizer.batch_decode(labels, skip_special_tokens=True)
        hypotheses = processor.tokenizer.batch_decode(prediction.predictions,
                                                      skip_special_tokens=True)
        save_validation_outputs(out / "validation", trainer.state.global_step, val_rows,
                                references, hypotheses)
        keep =[i for i, r in enumerate(references) if normalise(r)]
        counts = clip_counts([references[i] for i in keep], [hypotheses[i] for i in keep])
        return {k: float(v) for k, v in rates(counts.sum(axis=0)).items()}

    use_gpu = torch.cuda.is_available()
    training = Seq2SeqTrainingArguments(
        output_dir=str(out),
        max_steps=args.max_steps,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=lr,
        warmup_steps=args.warmup_steps,
        lr_scheduler_type="linear",
        weight_decay=0.0,
        fp16=use_gpu,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.eval_steps,
        save_total_limit=None if args.keep_checkpoints else 2,
        load_best_model_at_end=True,
        metric_for_best_model="wer",
        greater_is_better=False,
        predict_with_generate=True,
        generation_max_length=MAX_LABEL_TOKENS,
        generation_num_beams=1,
        logging_steps=5 if args.preflight else 25,
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
        dataloader_num_workers=args.workers,
        remove_unused_columns=False,
        label_names=["labels"],
    )
    gpu_memory = torch.cuda.get_device_properties(0).total_memory if use_gpu else 0

    class MixedPrecisionTrainer(Seq2SeqTrainer):
        """Generate under fp16 autocast too; plain float32 generation is very slow on a 3060."""

        stopped = None
        warned = False

        def prediction_step(self, *a, **k):
            with torch.autocast("cuda", dtype=torch.float16, enabled=use_gpu):
                return super().prediction_step(*a, **k)

        def log(self, logs, *a, **k):
            if use_gpu:
                peak = torch.cuda.max_memory_allocated()
                logs["peak_gpu_gib"] = round(peak / 2**30, 2)
                if peak > 0.95 * gpu_memory and not self.warned:
                    self.warned = True
                    print("WARNING: training used more memory than the GPU has; Windows is "
                          "lending system memory and steps will be very slow. Stop, halve "
                          "--batch-size and double --grad-accum.")
            super().log(logs, *a, **k)
            loss = logs.get("loss")
            if loss is not None and not math.isfinite(loss):
                self.stopped = f"loss was {loss} at step {self.state.global_step}"
                self.control.should_training_stop = True
                print(f"STOPPING: {self.stopped}")

    timer = StepTimer()
    trainer = MixedPrecisionTrainer(
        model=model,
        args=training,
        train_dataset=Clips(train_rows, args.root, processor),
        eval_dataset=Clips(val_rows, args.root, processor),
        data_collator=Collator(processor, model.config.decoder_start_token_id),
        compute_metrics=compute_metrics,
        callbacks=[timer],
    )
    out.mkdir(parents=True, exist_ok=True)
    record["sessions"] = []  # one entry per start or resume, with its real training time
    if (out / "run.json").exists():
        earlier = json.loads((out / "run.json").read_text(encoding="utf-8"))
        record["started"] = earlier["started"]
        record["sessions"] = earlier.get("sessions", [])
    (out / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    resume = any(out.glob("checkpoint-*"))
    session = {"began": time.strftime("%Y-%m-%d %H:%M:%S"), "resumed": resume}
    result = trainer.train(resume_from_checkpoint=resume)
    session.update(train_seconds=round(result.metrics.get("train_runtime", float("nan")), 1),
                   last_step=trainer.state.global_step)
    record["sessions"].append(session)
    record["train_seconds_total"] = round(sum(s["train_seconds"] for s in record["sessions"]), 1)
    history = [e for e in trainer.state.log_history if "eval_wer" in e or "loss" in e]
    record.update(best_checkpoint=trainer.state.best_model_checkpoint,
                  best_val_wer=trainer.state.best_metric, history=history,
                  stopped=trainer.stopped, finished=time.strftime("%Y-%m-%d %H:%M:%S"),
                  peak_gpu_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2) if use_gpu
                  else None)
    (out / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if trainer.stopped:
        raise SystemExit(f"training stopped: {trainer.stopped}; nothing was saved to best/")
    if args.preflight:
        estimate(timer.times, history, planned_steps, planned_eval_steps, args.eval_clips,
                 len(val_rows), record["peak_gpu_gib"], gpu_memory)
        return

    best = trainer.model.merge_and_unload() if args.method == "lora" else trainer.model
    best.config.use_cache = True
    best.save_pretrained(out / "best")
    processor.save_pretrained(out / "best")
    print(f"best validation WER {trainer.state.best_metric:.4f}; model saved to {out / 'best'}")


def estimate(step_times: list[float], history: list[dict], steps: int, eval_steps: int,
             eval_clips: int, trial_clips: int, peak: float | None, gpu_memory: int) -> None:
    step = float(np.median(step_times)) if step_times else float("nan")
    evals = [e for e in history if "eval_runtime" in e]
    per_clip = evals[-1]["eval_runtime"] / trial_clips if evals else float("nan")
    eval_seconds = per_clip * eval_clips
    total = steps * step + (steps // eval_steps) * eval_seconds
    if peak is not None:
        print(f"peak GPU memory {peak:.1f} of {gpu_memory / 2**30:.1f} GiB")
    print(f"{step:.1f} s per step; validation on {eval_clips} clips about "
          f"{eval_seconds / 60:.1f} min (measured on the longest transcripts, so on the high side)")
    print(f"estimated full run of {steps} steps: {total / 3600:.1f} h")


if __name__ == "__main__":
    main()
