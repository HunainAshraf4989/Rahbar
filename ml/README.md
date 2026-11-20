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
| `eval/sanity.py` | Checks on saved outputs: runaway repetition, over-long or empty outputs, stray tokens |
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
| `test` | benchmark | 6.0 | Final results only |
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
- The benchmark has folders named both `PODCAST` and `Podcast`, each with its own
  transcript file. Both are read; they are stored as `.case1` and `.case2` files so
  that neither overwrites the other on a disk that ignores letter case.

## 1. Machine check

```
nvidia-smi
python --version
```

Note the GPU model, its memory and the free disk space. The steps below need
Python 3.10 or newer and about 80 GB free while downloading (about 10 GB after).
They were written for a 12 GB GPU.

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

`manifest` prints `split_fingerprint`. It must be `93e5ddfff5a87f3c`; any other
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
cuts each longer training and validation clip once, at the longest pause between
two neighbouring words that both have alignment times, leaving both parts within
30 s. Numbers and other words without letters get no alignment times, so a cut is
never placed next to one. It prints how many clips were split and how many were
left whole (those are not trained on). Each part records its alignment score,
pause length and loudness at the cut. Then check the split and copy 20 pairs for
listening (half with a number near the cut, half with the shortest pauses):

```
python -m ml.asr.split_long --root DATA --check --listen LISTEN
```

The check must print `all pairs pass`. Fill in the verdict column of
`LISTEN/pairs.tsv`.

To split again (after a change to `split_long`), rebuild the manifest first. The
16 kHz files already written are reused, so the original WAV files are not needed:

```
python -m ml.asr.data manifest --root DATA
python -m ml.asr.data audio --root DATA
python -m ml.asr.split_long --root DATA
python -m ml.asr.split_long --root DATA --check --listen LISTEN
```

## 4. Cost on a 12 GB GPU

Training all weights of these models needs more than 12 GB: the weights, their
gradients and the optimiser state of the 809M-parameter turbo model alone come to
about 13 GB. Every run therefore uses LoRA, which trains small added matrices and
keeps the original weights fixed.

`LORA` in the commands below stands for:

```
--method lora --gradient-checkpointing --batch-size 8 --grad-accum 2
```

Measured on an RTX 3060 with the turbo model: 10.2 s per step of 16 clips, so one
pass over the 26,127 training clips is 1,633 steps or 4.6 hours; scoring 600
validation clips takes 5.6 minutes. If the GPU runs out of memory, halve
`--batch-size` and double `--grad-accum`.

During training, 600 validation clips are scored with greedy decoding every 500
steps to pick the best checkpoint. Choices between runs are made on the whole
validation set with `transcribe` and `score`, as shown below.

Before every long run, add `--preflight` to the same command. It takes about five
minutes: 10 steps on the clips with the longest transcripts, validation on the 32
longest, then it prints peak GPU memory, seconds per step and the estimated time
of the full run, in `OUT/preflight`. Start the real run only if the memory is below
the GPU's size and the estimate is what you expect. On Windows, memory above the
GPU's size is lent from system memory without an error and makes every step far
slower; both scripts print a warning when that happens. A training run stops by
itself if the loss becomes not-a-number, and refuses to resume when its settings
or the manifest have changed.

## 5. Experiments

Every choice in this table is made on `val`. The step counts are fixed here,
before any result on `test` is seen.

| Run | Start from | Changes | Steps | Compared on |
|---|---|---|---|---|
| `lr-1e-4`, `lr-3e-4`, `lr-1e-3` | turbo | learning rate | 500 | `val` |
| `scope-decoder` | turbo | LoRA in the decoder only | 500 | `val`, against the chosen `lr-*` run |
| `base-large` | large-v3 | base model | 500 | `val`, against the chosen `lr-*` run |
| `full-seed0`, `full-seed1`, `full-seed2` | chosen base | seed | 4000 | `val`, then `test` |
| `rank-16`, `rank-32`, `rank-64`, `rank-128` (optional) | chosen base | LoRA rank | 1500 | `val` |
| `heldout-machine-seed0..2` | `full-seed0/best` | published transcripts | 400 | `test` |
| `heldout-human-seed0..2` | `full-seed0/best` | corrected transcripts | 400 | `test` |

Learning rate (repeat with `3e-4` and `1e-3`):

```
python -m ml.asr.train --root DATA --base openai/whisper-large-v3-turbo LORA --lr 1e-4 --warmup-steps 50 --eval-steps 250 --out RUNS/lr-1e-4 --max-steps 500
```

LoRA scope and base model, with the learning rate just chosen:

```
python -m ml.asr.train --root DATA --base openai/whisper-large-v3-turbo LORA --lora-scope decoder --lr <chosen> --warmup-steps 50 --eval-steps 250 --out RUNS/scope-decoder --max-steps 500
python -m ml.asr.train --root DATA --base openai/whisper-large-v3 LORA --lr <chosen> --warmup-steps 50 --eval-steps 250 --out RUNS/base-large --max-steps 500
```

Compare any two runs on the whole validation set:

```
python -m ml.asr.transcribe --root DATA --split val --subsets US-CS --name lr-3e-4 --model RUNS/lr-3e-4/best
python -m ml.asr.transcribe --root DATA --split val --subsets US-CS --name scope-decoder --model RUNS/scope-decoder/best
python -m ml.eval.score --root DATA --split val --systems lr-3e-4 scope-decoder --baseline lr-3e-4
```

Decoder-only LoRA is adopted only if its validation WER is not worse than the
full-scope run's by more than the interval `score` prints for their difference.
Record the seconds per step of each run; the base and scope comparisons are at
equal steps, not equal time.

The main model, once per seed:

```
python -m ml.asr.train --root DATA --base <chosen> LORA --lr <chosen> --out RUNS/full-seed0 --max-steps 4000 --seed 0
```

Running a command again resumes it from its last checkpoint. A checkpoint is written
after each validation pass, so stopping in the middle of one loses the steps since
the previous checkpoint. `RUNS/<name>/run.json` holds the arguments, data counts,
code revision and the validation curve. A validation WER that rises while the
training loss keeps falling means overfitting; `best/` holds the checkpoint from
before that point.

The held-out pair differs only in transcripts:

```
python -m ml.asr.train --root DATA --base RUNS/full-seed0/best LORA --lr <chosen> --warmup-steps 40 --eval-steps 100 --train-split heldout --out RUNS/heldout-machine-seed0 --max-steps 400 --seed 0
python -m ml.asr.train --root DATA --base RUNS/full-seed0/best LORA --lr <chosen> --warmup-steps 40 --eval-steps 100 --train-split heldout --labels corrected.jsonl --out RUNS/heldout-human-seed0 --max-steps 400 --seed 0
```

`corrected.jsonl` has one `{"id": ..., "text": ...}` per line. Clips without a
corrected transcript are left out, so if any are missing give the first command a
`--labels` file with the published transcripts of the same clips.

Other conditions use the same script: `--lora-r 64` for the rank, `--train-hours 25`
for a random subset, `--subsets US-CS US-Std US-EngPk` to add the monolingual data,
`--whole-clips-only` to leave out the parts made by `split_long`.

Training and validation use mixed precision (weights kept in 32-bit, computation in
16-bit). `transcribe` loads Whisper and Seamless models in 16-bit on a GPU.

## 6. Benchmark results

Every system is transcribed under one protocol, recorded with its outputs in
`DATA/hyps/<split>/<name>.json`:

- audio over 30 s (84 code-switched and 13 Urdu-only test clips) is cut at its
  quietest 0.2 s into pieces of at most 30 s, the same for every system;
- greedy decoding, with no beam search, sampling or temperature fallback;
- output capped at 30 tokens per second of audio plus 20, at most 440 per piece
  (references need at most 30 per second), and every clip that reaches the cap is
  counted;
- language forced to `ur` for Whisper models (`hi` for the Hinglish model), as for
  our own model; `--language auto` gives the detection condition;
- Whisper and Seamless in 16-bit on the GPU, MMS in 32-bit.

Run each system first with `--preflight 30` (30 clips per subset, the longest half
included). It prints the output checks of `ml.eval.sanity` and the estimated time
for the whole test set. Run the full command only if the checks print no warning.

```
python -m ml.asr.transcribe --root DATA --name whisper-turbo --model openai/whisper-large-v3-turbo --preflight 30
python -m ml.asr.transcribe --root DATA --name whisper-large-v3 --model openai/whisper-large-v3
python -m ml.asr.transcribe --root DATA --name whisper-turbo --model openai/whisper-large-v3-turbo
python -m ml.asr.transcribe --root DATA --name urdu-kingabzpro --model kingabzpro/whisper-large-v3-urdu
python -m ml.asr.transcribe --root DATA --name hinglish-trelis --model Trelis/whisper-hinglish-preview --language hi
python -m ml.asr.transcribe --root DATA --name mms-1b --model facebook/mms-1b-all --backend mms
python -m ml.asr.transcribe --root DATA --name seamless-v2 --model facebook/seamless-m4t-v2-large --backend seamless
python -m ml.asr.transcribe --root DATA --name omni-llm-1b --model omniASR_LLM_1B --backend omnilingual
python -m ml.asr.transcribe --root DATA --name ours --model RUNS/full-seed0/best
python -m ml.eval.sanity --root DATA --systems whisper-large-v3 whisper-turbo urdu-kingabzpro hinglish-trelis mms-1b seamless-v2 omni-llm-1b ours
python -m ml.eval.score --root DATA --systems whisper-large-v3 whisper-turbo urdu-kingabzpro hinglish-trelis mms-1b seamless-v2 omni-llm-1b ours --baseline ours
```

The table is printed per subset and saved to `DATA/results/test.json` with every
system's recorded settings. `score` refuses systems transcribed under different
protocols, and prints a warning for any system whose outputs fail the sanity
checks. `--max-seconds 30` scores only the clips that were not cut, as a
robustness check. A run that stops continues where it stopped, but only with
the same settings; if the GPU runs out of memory the batch size is halved and the
run goes on. A system that is not run through `transcribe` (a commercial service,
for example) is scored the same way once its output is saved as
`DATA/hyps/test/<name>.jsonl` with one `{"id": ..., "text": ...}` per line.

The omnilingual backend needs the `omnilingual-asr` package, which may not install
on Windows; its decoding is the package's own and is recorded as such.

## Metrics

Text is normalised before scoring (punctuation, diacritics, letter variants, digit
forms and case). Nothing is transliterated, so a word in the wrong script is an error.

| Column | Meaning |
|---|---|
| `wer`, `cer` | Word and character error rate, summed over clips |
| `ins_rate` | Inserted words per reference word; a high value means runaway output |
| `en_wer` | Errors on reference words written in Latin script, plus inserted Latin words, per reference Latin word |
| `en_script_kept` | Share of reference Latin words whose aligned output word is in Latin script |
| `en_exact` | Share of reference Latin words reproduced exactly |
| `latin_share` | Share of output words in Latin script; on the Urdu-only subset this is leakage |
| `devanagari_share` | Share of output words in Devanagari |

Intervals are 95% bootstrap intervals over speaker groups.

## Tested so far

On an RTX 3060 (12 GB, Windows): setup, download, splits, audio conversion, long-clip
splitting with the earlier cut rule, a 40-step LoRA run of the turbo model with
validation, and the earlier transcription protocol for two Whisper models. On CPU
with `openai/whisper-tiny`: all weights and LoRA with resume, decoder-only LoRA,
the held-out split with replacement transcripts, Whisper transcription under the
current protocol, sanity checks and scoring. Not yet run on a GPU: the current
`split_long`, `transcribe` and `--preflight`; not run on any machine: the `mms`,
`seamless` and `omnilingual` backends.
