# Rahbar v1 contracts

This package defines wire shapes and provider ports. It does not implement the
HTTP routes, engine, authorization, grounding, playback, or assessment grading.
Examples are synthetic development fixtures, never reviewed course content.

Activate the configured Python environment, then run from the repository root:

```
npm ci
make contracts PYTHON=python
make test-contracts PYTHON=python
make lint PYTHON=python
```

`requirements-contracts.lock` records the tested Python contract-tool dependencies.
It is not a lock for the full application or an instruction to replace an existing
ML environment. `PYTHON` accepts any interpreter path without encoding a developer's
home directory into the project. Node dependencies are locked by package-lock.json.

Python models are authoritative. `tools/export_contracts.py` exports the JSON
Schema, event catalog, and OpenAPI description. `json-schema-to-typescript` derives
consumer types. Regenerate after editing models; do not hand-edit generated files.
The WebSocket protocol is `rahbar.v1`; the event catalog records directions and
exact payload schemas. OpenAPI describes HTTP only. Audio uses explicit turn,
sentence, stream, chunk and connection identity; PCM16 input is mono at 16 kHz.

Consumers must validate incoming data before using it. Python `parse_event`
rejects unknown events and incorrect directions. Schema validity alone does not
establish current-turn identity, authorization, evidence support, safe procedural
order, valid review, or successful playback. These need runtime checks. Extra
fields are rejected by this version; compatible schema additions require updating
generated validators and consumers together. JSON Schema does not encode every
cross-field Python invariant (for example, matching passage versions or decoded
PCM sample alignment); boundary implementations must enforce those too.

Use public quiz/viva serializers for learner output; private models never belong
in HTTP responses. Session creation accepts requested IDs, not trusted passages,
answer keys, grading outcomes, or cursor positions. `AssetManifest.permits` checks
asset/version/view/frame/part together, including static frames represented by null.

Fixtures cover normal lessons/replies, refusals, changing transcripts, repeated
interruption, late audio, reconnection, provider failures/fallback readiness,
assessment outcome combinations, unusable viva speech, duplicate channel work,
invalid wire data, and per-frame visibility. Ordered event fixtures describe races;
passing their schema checks does not prove the future engine handles those races.
Maintain the fixture JSON files directly. Assessment pairs contain real public quiz
and viva result shapes; scenario IDs and expected completion are test metadata
outside the wire payload, for later platform integration tests.

Pending producer/consumer review: Esha (speech, normalization, generation) and Arfa
(platform, browser, content). Runtime acceptance remains with the corresponding
implementation tasks.
