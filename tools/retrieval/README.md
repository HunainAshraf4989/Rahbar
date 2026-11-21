# Multilingual retrieval and experimental text RAG

This implementation uses pinned BGE-M3 dense embeddings, BM25, reciprocal rank
fusion and Qdrant from the outset. English, Urdu and mixed questions use the same
index. The text service resolves follow-ups from server-owned history, decomposes
cross-topic questions and requires quoted evidence plus a separate support check.
It is a development API; the main voice pipeline is not connected yet.

## Content and publication

Raw sources are read-only. `prepare.py` inventories, normalizes and deduplicates
passages with provenance and quarantine reports. `curriculum.cjs` extracts source
unit keys; `map.py` performs exact joins. `preflight.py` excludes passages exceeding
1024 BGE-M3 tokens instead of silently truncating them. Review metadata must come
from actual human review; mapping alone does not approve content. Answers currently
permit reviewed, mapped ordinary facts only. Safety procedures remain withheld.

The November 2026 preparation produced 76,755 draft candidates from 39 files,
16,991 exact curriculum matches and 72,246 indexable passages after excluding
4,509 oversized candidates. The full CPU index is still incomplete. These counts
are preparation measurements, not retrieval-quality results. There is no reviewed
gold set or approved full-corpus answer release yet.

## Environment and indexing

Run from this worktree with `PYTHONPATH=app/server`. Install `requirements.txt` in
a project-local Python environment; add `requirements-eval.txt` for RAGAS. Use
CPU Torch or a compatible existing installation on CPU hosts. Keep `HF_HOME`
project-local. The pinned embedding revision is
`5617a9f61b028005a4858fdac845db406aefb181`.

```sh
python tools/retrieval/prepare.py /path/to/education_data /path/to/derived
python tools/retrieval/index.py /path/to/indexable --revision REVISION --url http://127.0.0.1:16333
python tools/retrieval/index.py /path/to/indexable --revision REVISION --url http://127.0.0.1:16333 --query 'ریلے کیا کرتا ہے؟'
```

Inspect `--help` for mapping and preflight arguments. `compose.yaml` pins Qdrant
1.18.2 by digest; set `RAHBAR_QDRANT_DATA` to a project-local directory and use
`docker compose -p rahbar-retrieval -f tools/retrieval/compose.yaml up -d`.
The Python client is pinned to 1.18.0. Omitting `--url` selects embedded Qdrant for
small tests; never build and query that embedded directory concurrently.

Each embedding batch is cached atomically. Re-running the same build reuses cached
embeddings and upserts deterministic point IDs. `progress.json` reports progress;
`index.json` is written only after the complete build. Querying an unfinished
index fails. CPU indexing of this corpus takes many hours; process/service
availability must be checked after host or execution-environment restarts.

## Text API

```sh
python tools/retrieval/serve.py /path/to/reviewed-index --project-root /path/to/project --revision REVISION --url http://127.0.0.1:16333 --free-tier-confirmed
```

The server binds to loopback port 8010. Create a session with
`POST /dev/rag/sessions`, then submit `{ "question": "...", "revision": 0 }` to
`POST /dev/rag/sessions/{session_id}/turns`. Use the returned revision on the next
turn. Clients cannot supply their own conversation history. SQLite stores four
turns with a 30-minute TTL and optimistic concurrency. PostgreSQL is optional via
`--storage postgres` after explicitly applying `postgres.sql`; this migration has
not been applied to the hosted database. Contracts are in `contracts/rag.*` and
`app/web/src/api/rag.generated.ts`.

Only the central configuration loader reads credentials. `RAHBAR_RAG_MODELS` and
`RAHBAR_RAG_JUDGE_MODELS` configure comma-separated approved free-tier model lists.
429/503 responses trigger bounded fallback and cooldown; unavailable models are
skipped. Authentication and malformed responses fail rather than retry forever.
Actual models are recorded. Confirm billing is disabled before hosted evaluation.

## Evaluation

The target benchmark is 300 reviewed questions in 100 scenario families, evenly
covering English, Urdu and mixed language, with family-disjoint development and
test splits. A 30-question draft pilot exists locally in
`.cache/retrieval/pilot-v2/review.md` (relative to the project root), including
source excerpts, cross-topic questions, follow-ups, ambiguity and refusal cases.
It is not gold until reviewed. The earlier generated pilot is superseded.

Each gold JSONL row needs `id`, `family_id`, `split` (`development` or `test`),
`language`, `question`, `expected_outcome`, `evidence_groups` (alternative passage
IDs per required fact), `review_status=reviewed`, and `reviewers`. Follow-ups also
include `history` containing prior question/answer objects. Keep translations and
related conversation sequences in the same family.

```sh
python tools/retrieval/evaluate.py INDEX GOLD RESULTS --revision REVISION --url http://127.0.0.1:16333 --mode hybrid --planned --project-root ROOT --free-tier-confirmed
python tools/retrieval/report.py RESULTS
```

Planned evaluation shares the runtime history bounds and query planner and uses
round-robin facet retrieval. It evaluates candidate retrieval before publication
and answer-context filtering; it does not establish answer quality. Omit `--planned`
for the explicitly labeled raw-question ablation. Run dense, BM25 and hybrid
separately. Output is exclusive-create and flushed after each question. Results
include query strategy, actual planner model, latency and retrieved IDs. Reports
separate differing models/configurations and provide per-language Recall@10,
complete-evidence coverage, MRR, nDCG and scenario-family bootstrap intervals.

`ragas_eval.py ANSWERS OUTPUT --project-root ROOT --free-tier-confirmed` runs pinned
RAGAS 0.4.4 faithfulness, context precision/recall and response relevancy. Input
rows need `id`, `language`, `question`, `answer`, `reference`, `retrieved_contexts`,
`answer_model` and reviewed reference status. Responses are cached and each result
checkpointed; changed inputs/configuration require a new output path. Judge model
identities and same-model judging are recorded. Do not pool differing judge models.
The live synthetic faithfulness smoke passed; it is not a corpus score. Human
calibration and multilingual rubric validation remain necessary.

Proposed retrieval targets: Recall@10 >=90% overall and >=85% per language;
complete evidence >=80% for cross-topic questions. Fixed O2 answer targets: >=80%
correct-or-partial, >=85% correct refusals and zero unsafe replies in the evaluated
set. None has yet been established. Remaining gates are full indexing, source and
pilot review, benchmark expansion, held-out retrieval/answer evaluation and voice
integration. Near-duplicate/conflict review and curriculum coverage also remain.
