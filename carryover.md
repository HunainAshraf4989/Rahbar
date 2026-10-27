# Rahbar — copy-over list

Updated **2026-10-27**. Source paths below are relative to the preliminary
repository, `/home/hunain/FYP`. The core files in section 1 and the curriculum
snapshot have been copied into this repository and verified against their
sources with SHA-256. Optional demo/experiment files and optional code seeds
were not imported.

**Current repository policy:** the full `education_data/` corpus is local only
and ignored by Git. `agents.md` and `handoff.md` are also local only and must
never be staged, committed or pushed, but are not listed in `.gitignore`.
Do not publish duplicate copies of those context files under other filenames.

Hunain's decisions: carry **usable code and assets**, including the relay
infographic and its generator, and **the entire `education_data/` corpus**. Do
not carry all experiments. The final product is the React/FastAPI Rahbar system;
there will be **no standalone HTML demo in the final system**.

## 1. Carry these

Preserve the code/data paths below in the new repository so existing relative
references keep working. Directory entries mean the full directory unless an
exclusion is explicitly stated.

| Source | Destination in new repository | Why |
|---|---|---|
| `rahbar_handoff/agents.md` | `agents.md` (local only; existing copy preserved and updated) | Complete agent rules, user decisions and Karpathy guidelines |
| `rahbar_handoff/handoff.md` | `handoff.md` (local only; existing copy preserved) | Self-contained implementation requirements, architecture, evaluation, lessons and next work |
| `rahbar_handoff/carryover.md` | `carryover.md` | This transfer checklist |
| `Rahbar.pdf` | `Rahbar.pdf` | Authoritative proposal |
| **`education_data/` — entire folder** | **`education_data/` (local only; ignored by Git)** | RAG source corpus, including all raw/cleaned/chunked transcripts and QA variants; keep read-only |
| `reference/TEVTA_Auto_Electrician_3month_curriculum.pdf` | Same path | Curriculum provenance and content checking |
| `asset_gen/render3d.py` | Same path | Analytic ray tracer, part-ID buffer, aligned overlays, exterior/cutaway/footprint and turntables |
| `asset_gen/generate_assets.py` | Same path | Component specifications, part maps and older parametric generator; metadata reuse, not the target relay appearance |
| **`asset_gen/out/` — entire folder** | Same path | Ready-to-use infographic examples, part maps, SVGs, PNGs, frame JPGs and JSON manifests |
| `demo/infographic/content/` — entire folder | Same path initially | Relay/fuse lesson fixtures and retrieved-content example; review before promoting to authored course content |
| `demo/infographic/prompt_builder.py` | Same path initially | Reusable loading, part-vocabulary and validation ideas; fix defects and obsolete text rules listed in handoff |
| `demo/infographic/scenarios.json` | Same path initially | Concrete part-selection and cross-asset behavior examples |
| `demo/infographic/build_demo.py` | Same path, reference only | Existing view/frame/overlay and playback-control code to port into React; no requirement to ship or run an HTML demo |

Do not copy `.venv/`, `__pycache__/` or `.pyc` files if present inside any source
folder. `asset_gen/out/` contains generated **product assets/examples**, not an
experiment archive; keep the images as well as their generator.

The supplied converter will also be carried when Hunain adds it. Its filename
is not known yet: keep its source, dependencies, correction lexicon/rules and
relevant tests together, and record the actual paths when supplied. Do not claim
it is already included in this list's existing files.

### Curriculum mapping source already fetched into this folder

Carry this small selected-source snapshot (not the whole audit directory):

```text
audit_output/rahbar_context_audit/curriculum_source/
```

Suggested new destination: `reference/curriculum_source/`. It contains selected
files from `isramansoor9/fyp` at revision
`335c10af835023f61cb9b69b4cf6588f9fbee04c`, including:

```text
app/api/course1/toc/route.ts
app/course2/learn/page.tsx
app/api/course3/toc-data.ts
lib/tocUrdu/course1TocUrdu.ts
lib/tocUrdu/course2TocUrdu.ts
lib/tocUrdu/course3TocUrdu.ts
app/api/course1/content/route.ts
app/api/course2/content/route.ts
app/api/course3/content/route.ts
```

The snapshot also includes the source README and supporting route/page files.
Use it to extract hierarchy, exact content joins and Urdu display labels; do not
adopt its application as Rahbar. It does not contain the remote repository's
large content JSON files. The local `education_data/` remains the RAG corpus.
Provenance, hierarchy differences and import rules are in handoff section 5.2.

## 2. Optional reference demo — never part of the final runtime

For a quick visual reference, additionally retain:

```text
demo/infographic/VoiceStudio_Infographic_Demo.html
```

The existing generated page embeds its pictures, overlays and historical replies;
it can show the visual behavior without recovering all experiment files. Treat
its old branding, experiment scores, simulated timing and UI as historical.
The quality reference is the ray-traced relay and registered part highlights,
not the demo's model-comparison screen.

**Only if you want to rebuild that reference page unchanged**, additionally keep
this single fixture at its original path:

```text
demo/infographic/runs/battery_gemini-2_5-flash.json
```

This is a small reusable response fixture, not a requirement to retain or rerun
the experiments. The builder scans `runs/battery_*.json`; one such fixture is
enough for populated playback examples. With the core list above, this was
verified by an offline rebuild on 2026-10-27. No API call was made. Keeping the
fixture is unnecessary for the final React player or for regenerating images.

The code and actual infographic assets remain available even if you omit both
the HTML file and saved response fixture.

## 3. Dependencies and regeneration

All custom renderer/generator code is in the two `asset_gen/` Python files.
Their dependencies, determined from source imports, are:

| Task | Dependencies |
|---|---|
| Generate older base assets/part maps | Python 3, Pillow, configured DejaVu fonts |
| Ray-trace relay views and rotation frames | Python 3, NumPy, Pillow; font configuration |
| Rebuild optional reference HTML | Python 3 standard library, generated assets/manifests, lesson/scenario data and one saved fixture |
| View assets in final system | React/TypeScript client consuming images/SVGs/manifests and real playback events; to implement |

No dedicated renderer requirements/lock file was found in this checkout. The
project-local environment at `killtest/whatsapp_channel_test/.venv/` and its
`requirements.txt` belong to the WhatsApp prototype; the inventory did not find
NumPy/Pillow there. Old prose points to an external interpreter, which was not
inspected under the folder restriction. Do not assume its dependencies will
come with the source files, or use guessed version pins.

Create the renderer environment inside the new project and pin NumPy/Pillow
versions after a smoke test. The generators also reference system font paths:
make fonts configurable/project-local before running under the inside-folder
rule. The older generator requires those fonts; the ray tracer has a default
font fallback. Do not copy unrelated environments or inspect external folders
without Hunain's approval.

Regeneration order, after those dependencies and font paths are ready:

```sh
python3 asset_gen/generate_assets.py
python3 asset_gen/render3d.py
python3 asset_gen/render3d.py --turntable
```

Use the new project's configured interpreter. These overwrite files in
`asset_gen/out/`; preserve reviewed assets first. `generate_assets.py` produces
metadata/older flat illustrations; `render3d.py` produces the realistic relay
views. Read handoff section 9.2 for settings, geometry, reference appearance and
React integration. Rendering was **not** rerun during this handoff update.

## 4. Optional code seeds — inspect and port selectively

These can save work, but are not required to understand or bootstrap Rahbar.
Do not copy their old defaults unchanged into the final system.

### WhatsApp transport prototype

If reusing it, carry only this explicit source list, preserving paths under
`killtest/whatsapp_channel_test/`:

```text
README.md
requirements.txt
wa_server.py
config.py
providers.py
store.py
audio.py
engine.py
selftest.py
mock_meta.py
mock_twilio.py
report.py
```

The engine/audio modules are imported by the prototype, so keep them together
when using it as a reference. Replace their Gemini/Roman-Urdu/legacy-TTS behavior
with the shared Rahbar engine and configured ElevenLabs/open-source adapters.
Fix signature enforcement, conversation ownership and durable message processing
as described in the handoff. Its SQLite store is a prototype, not the committed
PostgreSQL design. Do not run modules that load real credential files to inspect
them. Mock tests need synthetic config and a synthetic test audio fixture; create
that fixture locally rather than carrying private voice recordings.

Skip its `.venv/`, live launch/setup/probe scripts, local databases, logs, media,
evidence and credentials. A consented existing fallback clip is optional test
material, not Rahbar's speech implementation.

### Separate `s0-build` branch

The current working tree is `docs`. Some reusable code/assets exist **only on
`s0-build`**, inspected commit `03290d5a8722c858edf0a778dc9fcf8b6387d956`:

- `app/server/voicestudio/config.py`: loader patterns, with redaction review and
  obsolete defaults replaced.
- `app/server/voicestudio/contracts/`: selected reply/lesson/session/trace typing
  patterns; do not bring back studio/AGENT-1 or old assessment/text policies.
- `app/server/tests/test_config.py` and `test_reply_roundtrip.py`: test ideas to
  adapt to the new contracts.
- Expanded `asset_gen/` source/assets: 35 part maps, requiring the technical
  quality review recorded in handoff section 12.1.
- `pipeline/import_urdu_titles.py`: title-import ideas; use the linked curriculum
  source and new track mapping as current inputs.

These are **not present merely because the current folder is copied**. Extract
selected files deliberately if wanted; do not merge the whole branch or copy its
old documentation/config wholesale. The stub server is not a working voice
engine. The core relay package in section 1 is available on this checkout and
does not depend on switching branches.

## 5. Leave behind

- Full experiment archives, all other `demo/infographic/runs/` files and
  `runs/archive/`; old battery runners/reports are not startup dependencies.
- `demo/infographic/run_battery.py`, `rescore.py`, `summarise.py` unless deliberately
  porting the old evaluation tooling. `rescore.py` imports the battery runner;
  they are not needed by the retained renderer/demo builder. The runner has a
  forbidden direct credential-loading path; do not execute it unchanged.
- The rest of `audit_output/`, except the selected curriculum snapshot above.
  Findings needed for implementation are already recorded in `handoff.md`.
- Old VoiceStudio plan/architecture/design documents, handoff archives, proposal
  drafts/decks/SRS/SDS and architecture alternatives as implementation authority.
  Keep personal/academic archives separately if wanted; they are not prerequisites.
- `.git/`, old virtual environments, caches and generated private evidence.
- **Never transfer real `.env`, `whatsapp.txt`, service tokens, private chats,
  database dumps or unconsented voice recordings as repository content.** Configure
  credentials privately in the new environment.

## 6. After copying

1. Keep the context files at the destination paths in section 1. Keep
   `agents.md` and `handoff.md` local and untracked; do not create a publishable
   `AGENTS.md` duplicate. Only `carryover.md` is intended for version control.
2. Verify the entire `education_data/` tree was preserved; keep it read-only and
   generate curated/indexed derivatives elsewhere.
3. Check `asset_gen/out/relay_4pin.parts.json`, the view manifest, static views,
   footprint, both turntable manifests and every referenced JPG/SVG exist. The
   source checkout has 24 frames per exterior/cutaway view, 48 pairs in total.
4. Inspect the retained render and overlay before building new assets. Implement
   the actual React player against real audio events; no final HTML demo.
5. Begin the handoff implementation sequence. Use initial ElevenLabs STT/TTS,
   keep provider interfaces swappable, integrate the forthcoming converter, and
   use open source when limits run out or when replacing the initial providers.
