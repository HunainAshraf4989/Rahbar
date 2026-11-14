# Speech recognition adaptation

Scripts for adapting a Whisper-family model to Pakistani Urdu-English speech on
[ASLP-lab/UrduSpeech](https://huggingface.co/datasets/ASLP-lab/UrduSpeech) and for
scoring it against other systems on the dataset's human-corrected benchmark.

| File | Purpose |
|---|---|
| `asr/data.py` | Download the dataset, build the splits, convert audio to 16 kHz |
| `asr/split_long.py` | Split 30–35 s training clips into two transcript-aligned parts |
| `asr/train.py` | Fine-tune (all weights or LoRA), resumable, best checkpoint chosen on validation WER |
| `asr/transcribe.py` | Transcribe a split with any supported model |
| `eval/score.py` | Results table with bootstrap confidence intervals |
| `eval/text.py`, `eval/metrics.py` | Scoring normaliser and metric definitions |

Run every command from the repository root. `DATA` and `RUNS` stand for two
folders outside the repository, on a disk with enough space; nothing under them
is committed.

## Splits

All splits are fixed by `data.py manifest` with seed 0 on a pinned dataset revision.

| Split | Source | Code-switched hours | Used for |
|---|---|---|---|
| `train` | corpus | 72.7 | Training |
| `val` | corpus | 8.0 | Choosing checkpoints and settings |
| `heldout` | corpus | 5.0 | Kept out of training; later fine-tuned on with machine or human-corrected transcripts |
| `test` | benchmark | 5.6 | Final results only |
| `excluded` | corpus | 1.3 | Shares text or audio with the benchmark |

- **Test** is the benchmark, scored against its `ground_truth` field. It is never
  used to train, to choose a checkpoint or to choose a setting.
- **Benchmark overlap**: a corpus clip that shares a run of 8 words with any
  benchmark clip is excluded, and after `audio` so is any clip with identical samples.
- **Validation** is 10% of each content category, taken as contiguous runs of
  diarised speakers so that neighbouring speakers of one recording stay together.
- **Held-out** is a stratified random sample of code-switched training clips of at
  most 28 s: the same share of every content category, at least 5 hours in total.
- The dataset has no recording identifier, and the benchmark comes from the same
  programmes as the corpus, so speakers are not guaranteed disjoint between splits.
- Duplicate rows, rows without audio and rows with empty text are dropped.

## 1. Machine check

```
nvidia-smi
python --version
```

Note the GPU model, its memory and the free disk space. The steps below need
Python 3.10 or newer and about 80 GB free while downloading (about 10 GB after).

## 2. Setup

Create an environment, install the PyTorch build for the machine's CUDA version
using the command from pytorch.org, then:

```
pip install -r ml/requirements.txt
python -m pytest ml/tests
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

All tests must pass and the last line must print `True`.

## 3. Data

```
python -m ml.asr.data download --root DATA
python -m ml.asr.data manifest --root DATA
```

`manifest` prints `split_fingerprint`. It must be `f31207c8c8ca8f04`; any other
value means the splits differ from the ones above, so stop and find out why.

```
python -m ml.asr.data audio --root DATA --delete-raw
python -m ml.asr.split_long --root DATA
```

`download` fetches the transcripts of every subset and the audio of the
code-switched corpus and the benchmark (44.1 kHz stereo). `audio` writes 16 kHz
mono copies and, with `--delete-raw`, removes each original afterwards.
`DATA/manifests/stats.json` records what every step kept and dropped.

Whisper reads at most 30 s and corpus clips are capped at 35 s, so `split_long`
cuts each longer training clip once, at the longest pause that leaves both parts
within 30 s. It prints how many clips were split and how many were left whole
(those are not trained on). Each part records its alignment score, pause length
and loudness at the cut. Listen to 20–30 pairs before training; if cuts fall
inside words, raise `--min-score`.

## 4. Trial run

```
python -m ml.asr.train --root DATA --base openai/whisper-large-v3-turbo --out RUNS/trial --max-steps 200 --eval-steps 100
```

This checks memory and speed. If the GPU runs out of memory, halve `--batch-size`
and double `--grad-accum`; for the larger model also add `--gradient-checkpointing`.
Use the settings that work for every run that follows.

## 5. Experiments

| Run | Start from | Data | Steps | Scored on |
|---|---|---|---|---|
| `select-turbo` | `openai/whisper-large-v3-turbo` | `train` | 1500 | `val` |
| `select-large` | `openai/whisper-large-v3` | `train` | 1500 | `val` |
| `full-seed0`, `full-seed1`, `full-seed2` | the better of the two | `train` | same for all three | `val`, then `test` |
| `heldout-machine` | `full-seed0/best` | `heldout`, published transcripts | same for both | `test` |
| `heldout-human` | `full-seed0/best` | `heldout`, corrected transcripts | same for both | `test` |

Choosing the base model:

```
python -m ml.asr.train --root DATA --base openai/whisper-large-v3-turbo --out RUNS/select-turbo --max-steps 1500
python -m ml.asr.train --root DATA --base openai/whisper-large-v3 --out RUNS/select-large --max-steps 1500
python -m ml.asr.transcribe --root DATA --split val --subsets US-CS --name select-turbo --model RUNS/select-turbo/best
python -m ml.asr.transcribe --root DATA --split val --subsets US-CS --name select-large --model RUNS/select-large/best
python -m ml.eval.score --root DATA --split val --systems select-turbo select-large --baseline select-turbo
```

Full fine-tuning, once per seed:

```
python -m ml.asr.train --root DATA --base <chosen> --out RUNS/full-seed0 --max-steps 6000 --seed 0
```

Running a command again resumes it from its last checkpoint. `RUNS/<name>/run.json`
holds the arguments, data counts, code revision and the validation curve. A
validation WER that rises while the training loss keeps falling means overfitting;
`best/` holds the checkpoint from before that point. If the validation WER is still
falling at the last step, repeat with more steps.

The held-out pair differs only in transcripts:

```
python -m ml.asr.train --root DATA --base RUNS/full-seed0/best --out RUNS/heldout-machine --train-split heldout --max-steps <n>
python -m ml.asr.train --root DATA --base RUNS/full-seed0/best --out RUNS/heldout-human --train-split heldout --labels corrected.jsonl --max-steps <n>
```

`corrected.jsonl` has one `{"id": ..., "text": ...}` per line. Clips without a
corrected transcript are left out, so run the first command with the same file
restricted to machine transcripts if any are missing.

Other conditions use the same script: `--method lora --lora-r 32`, `--train-hours 25`
for a random subset, `--subsets US-CS US-Std US-EngPk` to add the monolingual data,
`--whole-clips-only` to leave out the parts made by `split_long`.

## 6. Benchmark results

```
python -m ml.asr.transcribe --root DATA --name whisper-large-v3 --model openai/whisper-large-v3
python -m ml.asr.transcribe --root DATA --name whisper-turbo --model openai/whisper-large-v3-turbo
python -m ml.asr.transcribe --root DATA --name urdu-kingabzpro --model kingabzpro/whisper-large-v3-urdu
python -m ml.asr.transcribe --root DATA --name hinglish-trelis --model Trelis/whisper-hinglish-preview --language hi
python -m ml.asr.transcribe --root DATA --name mms-1b --model facebook/mms-1b-all --backend mms
python -m ml.asr.transcribe --root DATA --name seamless-v2 --model facebook/seamless-m4t-v2-large --backend seamless
python -m ml.asr.transcribe --root DATA --name omni-llm-1b --model omniASR_LLM_1B --backend omnilingual
python -m ml.asr.transcribe --root DATA --name ours --model RUNS/full-seed0/best
python -m ml.eval.score --root DATA --systems whisper-large-v3 whisper-turbo urdu-kingabzpro hinglish-trelis mms-1b seamless-v2 omni-llm-1b ours --baseline ours
```

The table is printed per subset and saved to `DATA/results/test.json`. A system
that is not run through `transcribe` (a commercial service, for example) is scored
the same way once its output is saved as `DATA/hyps/test/<name>.jsonl` with one
`{"id": ..., "text": ...}` per line.

## Metrics

Text is normalised before scoring (punctuation, diacritics, letter variants, digit
forms and case). Nothing is transliterated, so a word in the wrong script is an error.

| Column | Meaning |
|---|---|
| `wer`, `cer` | Word and character error rate, summed over clips |
| `en_wer` | Errors on reference words written in Latin script, plus inserted Latin words, per reference Latin word |
| `en_script_kept` | Share of reference Latin words whose aligned output word is in Latin script |
| `en_exact` | Share of reference Latin words reproduced exactly |
| `latin_share` | Share of output words in Latin script; on the Urdu-only subset this is leakage |
| `devanagari_share` | Share of output words in Devanagari |

Intervals are 95% bootstrap intervals over speaker groups.

## Tested so far

On CPU with `openai/whisper-tiny` and about 180 clips: download, splits, audio
conversion, long-clip splitting, full and LoRA fine-tuning with resume, fine-tuning
on the held-out split with replacement transcripts, Whisper transcription and scoring.
The `mms`, `seamless` and `omnilingual` backends and all GPU settings have not been
run yet.
