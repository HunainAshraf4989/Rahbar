"""Fine-tune a Whisper-family model on the prepared UrduSpeech manifests.

    python -m ml.asr.train --root DATA --base openai/whisper-large-v3-turbo --out RUNS/turbo-full

Run the same command again to resume an interrupted run from its last checkpoint.
The validation set is scored during training and the checkpoint with the lowest
validation WER is saved to OUT/best. The benchmark is never read here.
"""

import argparse
import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from transformers import (
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)

from ml.asr.data import MAX_SECONDS, read_jsonl
from ml.eval.metrics import clip_counts, rates
from ml.eval.text import normalise

MAX_LABEL_TOKENS = 448  # Whisper's decoder length


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


def git_revision() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


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
    args = p.parse_args()
    lr = args.lr or (1e-5 if args.method == "full" else 1e-4)

    processor = WhisperProcessor.from_pretrained(args.base, language=args.language,
                                                 task="transcribe")
    train_rows, val_rows, data_summary = select_rows(args, processor)
    print(json.dumps(data_summary, indent=2))
    if not train_rows or not val_rows:
        raise SystemExit("no usable training or validation clips; check the manifests")
    model = build_model(args)

    def compute_metrics(prediction) -> dict:
        labels = np.where(prediction.label_ids == -100, processor.tokenizer.pad_token_id,
                          prediction.label_ids)
        references = processor.tokenizer.batch_decode(labels, skip_special_tokens=True)
        hypotheses = processor.tokenizer.batch_decode(prediction.predictions,
                                                      skip_special_tokens=True)
        keep = [i for i, r in enumerate(references) if normalise(r)]
        counts = clip_counts([references[i] for i in keep], [hypotheses[i] for i in keep])
        return {k: float(v) for k, v in rates(counts.sum(axis=0)).items()}

    use_gpu = torch.cuda.is_available()
    training = Seq2SeqTrainingArguments(
        output_dir=str(args.out),
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
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="wer",
        greater_is_better=False,
        predict_with_generate=True,
        generation_max_length=MAX_LABEL_TOKENS,
        logging_steps=25,
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
        dataloader_num_workers=args.workers,
        remove_unused_columns=False,
        label_names=["labels"],
    )
    class MixedPrecisionTrainer(Seq2SeqTrainer):
        """Generate under fp16 autocast too; plain float32 generation is very slow on a 3060."""

        def prediction_step(self, *a, **k):
            with torch.autocast("cuda", dtype=torch.float16, enabled=use_gpu):
                return super().prediction_step(*a, **k)

    trainer = MixedPrecisionTrainer(
        model=model,
        args=training,
        train_dataset=Clips(train_rows, args.root, processor),
        eval_dataset=Clips(val_rows, args.root, processor),
        data_collator=Collator(processor, model.config.decoder_start_token_id),
        compute_metrics=compute_metrics,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    record = {"args": {k: str(v) for k, v in vars(args).items()}, "learning_rate": lr,
              "data": data_summary, "code_revision": git_revision(),
              "torch": torch.__version__, "device": torch.cuda.get_device_name(0) if use_gpu else "cpu"}
    (args.out / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    resume = any(args.out.glob("checkpoint-*"))
    trainer.train(resume_from_checkpoint=resume)

    best = trainer.model.merge_and_unload() if args.method == "lora" else trainer.model
    best.config.use_cache = True
    best.save_pretrained(args.out / "best")
    processor.save_pretrained(args.out / "best")
    history = [e for e in trainer.state.log_history if "eval_wer" in e or "loss" in e]
    record.update(best_checkpoint=trainer.state.best_model_checkpoint,
                  best_val_wer=trainer.state.best_metric, history=history)
    (args.out / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"best validation WER {trainer.state.best_metric:.4f}; model saved to {args.out / 'best'}")


if __name__ == "__main__":
    main()
