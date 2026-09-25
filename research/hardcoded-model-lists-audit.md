# Standing inventory — every place that enumerates models, families, engines, modalities or workers

**Owner rule (2026-09-24):** nothing about models is hardcoded. Registries are data; consumers read
the data. A family, engine or model list in a consumer is a defect **unless it is a genuine
contract**. Where a list is unavoidable it must fail loudly and name the missing entry.

**How to use this file.** Before you add a model, a family, an engine, a modality or a worker:
search this file for the kind of thing you are adding, then work the checklist at the bottom.
Every row carries a `file:line` **and the literal text**, because line numbers drift and the
literals do not — grep the literal if the line number is stale.

**Status of the three known incidents.**

| # | Incident | Where it lives now |
|---|---|---|
| 1 | `chat-mlx` missing from the engine→worker map → HTTP 500 → every picker blank (0.6.57) | **Fixed** at HEAD by `_worker_for_engine` raising a named `ValueError` (`components/hub/standalone.py:82`) plus the drift test `tests/test_model_manifest_catalog.py:65`. **The amplifier that made it fleet-wide is still present — see A13.** |
| 2 | Hardcoded voice family allowlist hid **6 of the 12 packaged** voice packages. **Measured:** the Voice catalog holds 13 entries across 8 families, 12 of which have a shipped manifest. The allowlist (`qwen3-tts`, `voxcpm*`, `omnivoice`) accepted 6 and rejected 6 — `chatterbox-4bit`, `chatterbox-8bit`, `fish-audio-s2-pro-8bit`, `fish-audio-s2-pro-bf16`, `khmer-tts-mms`, `khmer-tts-female-onnx`. The 13th catalog entry, `pocket-tts-4bit`, was excluded for a different reason: it had no manifest, so it failed `package != nil`. The owner's brief says six of thirteen; the number hidden by the allowlist is six, and the denominator that matters is the twelve packaged packages. | **Fixed in the working tree** — `native/StudioAPI.swift:4306` is now `let supported = package != nil`, and the trap test became the invariant at `tests/native/api_main.swift:1669`. HEAD (`0579db1`) still carries `let formCompatible = isQwen \|\| isCloneFamily` at `native/StudioAPI.swift:4225`. |
| 3 | A test asserting the broken behaviour | **Fixed for Audio** (`api_main.swift:1669` is now derived from the fixture's own catalog). **Still present for Transcription** — `tests/native/api_main.swift:1724`. **Still present in seven other suites** — see the exact-membership table in Annex 1. |

---

## Snapshot and method

**Branch** `implementation/voice-runtime`, HEAD `0579db10490a5d3fe5ae10f2e864279501255837`.

**The working tree was being edited by other agents throughout this audit.** Five tracked files
were modified and three `pocket-tts-*` manifests were untracked while I read them. Every
`file:line` below was machine-checked against this snapshot (a script asserted the literal is on
the cited line; 112 citations checked, 0 unresolved). Hashes of the files I cite:

```
0ac9634b…  components/model_packages.py          a0f0b6e7…  components/hub/standalone.py
c35c9a24…  components/hub/standalone_config.py   23497ee3…  components/hub/backend/ledger.py
aece491d…  components/hub/backend/fleet_nodes.py 5574deac…  components/subtitle/app/backend/main.py
8b60d31d…  components/music/app/backend/catalog.py  7d37a15e…  components/image/app/backend/generation.py
c4340c96…  components/voice/app/backend/catalog.py  f06343d9…  components/voice/app/backend/generation.py
080c199a…  components/voice/app/backend/transcription.py
c18bfce4…  native/StudioAPI.swift   42dcd4aa…  native/StudioViews.swift
b2868cc8…  tests/native/api_main.swift   3a27c5f9…  tests/test_model_manifest_catalog.py
0c975708…  packaging/capture_model_manifests.py
```

**What I searched for** (grep over `components/**`, `native/**`, `packaging/**`, `tests/**`,
`qualification/**`, `model-manifests/**`, `docs/**`; `.build/`, `.git/`, `_trash/`, `dist/` and
`__pycache__` excluded):

- engine vocabulary — `image-mflux|voice-mlx|voice-vits|subtitle-mlx|music-mlx|chat-mlx`
- modality/studio vocabulary — `image|voice|transcription|music|chat|video|render|subtitle` as
  dict keys, `in (...)`, `in {...}`, `==`, `case`, and `switch` ladders
- model ids — `mlx-community/|khmerttsopensource/|sengtha/|AITRADER/|filipstrand/|stabilityai/|ACE-Step/|moonshine-ai/|openai/whisper`
- family/engine branching — `family ==`, `family in`, `engine ==`, `hasPrefix`, `startswith`,
  `endswith`, `glob(`, `if repo ==`, `if package_id ==`
- collection shapes — module-level `{`/`(`/`[`/`frozenset` literals, `_FAMILIES`, `_MODELS`,
  `_ENGINES`, `MODALITY`, `TRACKED_*`, `REQUIRED_*`, `PACKAGE_*`, `CHOICES`, `DETAILS`, `EXPECTED`
- test shapes — `assertEqual(set(`, `assertEqual(sorted(`, `Set(`, `.count ==`, `== {`

**Classification vocabulary used below.**

- **trap** — must be edited for every new model/family/engine/modality, and failing to edit it is
  (a) silent, or (b) loud but with a message that misnames the cause.
- **contract** — a real, closed vocabulary, with the reason it is closed stated in the row.
- **silent** — nothing appears, or something appears wrong, with no error anywhere.
- **loud** — an exception, a refusal, a non-zero exit or a failing test.
- **untested** — no test would fail if the next model of that kind were added and this list were
  not updated.

**Ranking rule:** `silent + per-model/family/engine + untested` first. Silent beats loud; a
fleet-wide blast radius beats a single-model one.

---

## Fix status — 2026-09-25

**Everything in Tier A and Tier B is fixed or explicitly closed, and every fix was verified against
the tree rather than the report.** The rows above are the audit as it was found; this section is the
disposition. `file:line` citations above are deliberately **not** updated — they describe the
snapshot, and rewriting them would erase the evidence that the audit was machine-checked against a
specific revision.

| Tier | Rows | State |
| --- | --- | --- |
| **A** | A1–A18 (18) | **all fixed** |
| **B** | B1–B40 (40) | **all fixed** |
| **C** | C1–C16 | unchanged: contracts, kept as they are. Two caveats named *inside* Tier C rows were fixed (C4's bare `KeyError` on an unknown family; C9's hardcoded chat label/family; C15's overstated comment) |
| **HUB-19** | the two-spelling companion split | **fixed**: one reader, the legacy spelling refused by `validate_manifest`, the catalog migrated |
| **Annex 1** | exact-membership tests | converted to invariants — every listed site, plus `tests/test_model_manifest_catalog.py`'s `EXPECTED` table, which is now derived |
| **Annex 2** | stale counts in documents | fixed, and the two counts a reader is most likely to trust are now **recomputed by a test** (`tests/test_document_inventory.py`) rather than maintained |

### What now holds the line

The audit's own closing note said the point was not the 73 rows but the habit. These are the
invariants that catch the next model, all of them assertions over the real catalog rather than
membership lists:

- `tests/test_model_manifest_catalog.py` — every shipped manifest validates, resolves a worker, and
  is reproducible by the capture tool; `set(ENGINES) == set(standalone._ENGINE_ROUTE)`.
- `tests/test_catalog_visibility.py` — the owner's *"does every model show"* sweep: every manifest
  reaches the package route as one row, exactly the declared companions are withheld, every other
  package is offered and routed to a studio, no manifest publishes a floor above the largest fleet
  machine, and every companion is in the catalog its dependent is copied with.
- `tests/test_document_inventory.py` — the package count and byte total in `model-manifests/README.md`
  and the catalog size and engine list in `docs/runtime-contract.md` are recomputed and compared.
- `tests/test_worker_version_reporting.py` — no worker may answer a version-shaped literal.
- `tests/test_music_lane_contract.py`, `tests/test_image_bundle.py` and each lane's own tests — a
  model's published dispatch contract is built from its own limits.
- `tests/hub_regression` — the adapted-upstream parity suite, which now runs in the release gate's
  command set rather than being invisible behind its own `collect_ignore`.

### Two model-keyed tables deliberately remain

Both are **measurements**, and a measurement cannot be derived from the catalog — it has to be
recorded somewhere. What mattered for the audit was never that a table existed but that its
*absence* was silent, and both are now guarded:

| Table | Why it stays | What makes it safe now |
| --- | --- | --- |
| `components/hub/backend/memory_admission._FLEET_DEFAULTS` (FLUX.2 Klein 4B MLX 4-bit, a measured 7.5 GB total floor) | It is *lower* than the model's published 16 GB tier and the Hub's rule is `max(published, policy)`, so it cannot be expressed through the published fields without being masked | `memory_admission.describe` publishes `fleet_default_measured` plus a `default_reason` that names the absence, and the reason now distinguishes "the worker published a floor and it is enforced" from "nothing was published, the generic 2.0 GB default applies". It is also not a gate: only the live-free floor is enforced, and only an operator override can put a total-memory floor back |
| `components/voice/app/backend/lane_contract.runtime_seconds_for` — **closed 2026-09-25** | It was a repo-keyed `_MEASURED_RUNTIME` map whose numbers existed only as prose in `catalog.py`'s comments. The target shape already existed in the music and image lanes: numbers that trace to a stored probe file | The map is deleted. Numbers come from `qualification/evidence/voice-runtime-2026-09-25/measured-runtimes.json`, and every published row names the probe file it came from, re-read by a test. Two numbers were **dropped** rather than kept because no probe receipt exists — Pocket TTS 4-bit (MLX 4-bit)'s 0.30 s and VoxCPM2 4-bit (MLX 4-bit)'s 61.9 s / 738.4 s — and are recorded in the record's `withheld` block with their reasons. A packaged worker has no `qualification/`, so it publishes nothing there: absent, not curated |

The owner-approved live-free floor for Qwen3-TTS 12Hz 0.6B Base 8-bit (3.2 GB) **was** moved out of
the Hub and onto the model's own row during this release, once the producer published it — that is
the pattern: move the fact to the model whenever the model can state it, and make the absence loud
when it cannot.

### One more of the same shape, found after the audit closed

`components/hub/backend/capabilities.py::_voice_modes` derived a model's voice modes from its
`capabilities` **plus a name substring** — `"voicedesign" in repo` — and defaulted any TTS model to
`preset_voice` (`"tts" in capabilities and not modes`). That is audit row **A9's** shape in the Hub,
which the audit itself missed because it searched for *model* enumerations in the Hub and this one
enumerates *modes*.

It was not cosmetic. Measured against the shipped catalog, the old derivation **lost a published
mode** for four models and **invented one** for a fifth:

| Model | Hub said | Truth |
| --- | --- | --- |
| VoxCPM2 4-bit / 8-bit / bf16 (MLX) | `[reference_audio_clone]` | `[reference_audio_clone, voice_design]` |
| OmniVoice bf16 (MLX bfloat16) | `[reference_audio_clone]` | `[reference_audio_clone, voice_design]` |
| pocket-tts-4bit (MLX 4-bit) | `[reference_audio_clone]` | `[reference_audio_clone, preset_voice]` |

The Hub now reads the producer's own `modes` (published per model since 2026-09-25) and renames it
through **one** table into its peer-facing control vocabulary, preserving the producer's order. An
unknown mode raises a `ValueError` naming the mode, the model and both files to update — the
`_worker_for_engine` shape. A row publishing no `modes` gets no `voice_modes` key plus a warning
rather than a refusal, deliberately: refusing would raise out of `build_snapshot` and 500
`/api/hub/capabilities` for every modality on the site because one studio is stale, which is the
0.6.57 blank-everything failure mode.

### Two things the audit did not find, and this release added

Both arrived from Gateway Studio, measured from the consuming side
(`_handoffs/2026-09-25-studio-hub-lane-contract-prompt.md` there, committed as `1ad796e`):

- **`/api/hub/model-packages` rows carried no `modality`**, so a scheduler joined them to
  `/api/hub/catalog` on a best-effort second read and fell back to an engine table when it failed.
  `/studio/voice/api/transcribe/availability` now also answers a bare 401 with the credential it
  required and the one it was offered.
- **A published fact with no provenance is a hardcoded fact.** A downstream consumer was showing a
  curated flat `context_window_tokens = 1,000,000` as if it were the model's, while the shipped chat
  model's own `config.json` states `262144`. Rule recorded in `AGENTS.md`; shape in
  `docs/lane-execution-contract.md`; work in `docs/tasks/ST-20.md`.

---

## Tier A — silent, per-model/family/engine, and untested (fix these first)

| # | Location | Literal | What it gates — what silently does not happen | Class | Test | Read instead |
|---|---|---|---|---|---|---|
| **A1** | `components/subtitle/app/backend/main.py:307` | `manifest_paths = sorted(manifest_dir.glob('whisper-*.json'))` | The Subtitle worker's **entire model catalog** is the set of manifests whose *package id* starts with `whisper-`. A new subtitle package named `parakeet-tdt-0-6b` or `nemotron-asr-0-6b` ships in `model-manifests/`, is published by the Hub, and is **never loaded by the worker** — so it is absent from `/api/catalog`, absent from the picker, and any job for it fails as "choose one of the packaged Whisper models". | **trap** — the very next non-`whisper-` subtitle package is invisible | `tests/test_subtitle_standalone.py:251` pins `['whisper-base','whisper-small','whisper-turbo']` — exact membership, so it **cannot** notice the glob | `glob('*.json')` (the `engine != 'subtitle-mlx'` check on the next line already does the filtering the filename is being asked to do) |
| **A2** | `components/hub/backend/ledger.py:38`, used at `:205` and `:290` | `_VALID_OPERATIONS = frozenset({"image", "speech", "transcription"})` | **`music` is missing.** Both write (`:205`) and read (`:290`) coerce any other operation to `default_operation`, which is `"image"` only for an image studio and `"speech"` for everything else. Every Music Studio activity row is therefore stored and read back as **`speech`**. Music's per-operation history and stats are filed under Voice. Note `components/hub/backend/activity.py:34` *does* include `music`, so the live view and the durable ledger disagree about the same job. | **trap** — silent re-labelling, wrong data | untested | `activity.VALID_OPERATIONS` (the sibling list that already has `music`), or better: one shared vocabulary |
| **A3** | `components/music/app/backend/catalog.py:154`, `:158`, `:163`, getters at `:170`, `:174`, `:178` | `PACKAGE_MANIFESTS = { MAIN_REPO: "ace-step-1-5-turbo.json", STABLE_AUDIO_REPO: "stable-audio-3-medium.json" }` and three sibling dicts; getters are `PACKAGE_MANIFESTS.get(repo, PACKAGE_MANIFESTS[MAIN_REPO])` | Three parallel per-repo maps. A **third** music model added to `CATALOG` without all three gets **ACE-Step's manifest name, package id and package root** — it verifies/loads the wrong package and publishes the wrong `package_id`. Silent wrong data, not a missing row. | **trap** — silent wrong data | untested for a third model; `tests/test_music_package_contract.py:113` pins the list to exactly two | one per-model map, or read `package_id` from the manifest |
| **A4** | `components/music/app/backend/catalog.py:181` + `components/music/app/backend/main.py:110`, `:117` | `def is_stable_audio(repo: str) -> bool: return repo == STABLE_AUDIO_REPO` and `revision = (catalog.STABLE_AUDIO_REVISION if stable else MODEL_REVISION)` | The `/api/catalog` row's **defaults, generation limits, revision and qualification text** are chosen by a **two-way if/else on one repo id**. A third music model falls into the `else` and is published with ACE-Step's revision, ACE-Step's duration caps and ACE-Step's qualification claim — while `serialize_model` gives it the correct label and capabilities, so the lie is partial and hard to spot. The native Music form then offers the wrong duration ceiling. | **trap** — silent wrong data in the published contract | untested | `catalog.serialize_model(model)` already publishes per-model facts; `ModelEntry.long_form`, `inference_steps`, `sample_rate_hz` are already per-model fields |
| **A5** | `components/image/app/backend/generation.py:194` + `components/image/app/backend/main.py:170` | `MODELS: dict[str, ImageModel] = {model.repo: model for model in (FLUX2_KLEIN, Z_IMAGE_TURBO)}` and `return {'models': [catalog_entry(model) for model in MODELS.values()]}` | The Image worker's **catalog enumerates `MODELS`, not the manifests**. An `image-mflux` manifest with no `ImageModel` entry is accepted by `load_image_manifests` but never published → it is in `/api/hub/model-packages` and **absent from the Image picker**, with no error. The job-time failure is good (`generation.py:304` refuses and names the available repos), but discovery is silent. | **trap** — silent absence at discovery | untested for the next image model (`tests/test_image_bundle.py` ships one manifest) | the manifests are already read (`load_image_manifests`) and `manifest_errors` is already plumbed — derive the catalog from the intersection and report the difference |
| **A6** | `components/voice/app/backend/catalog.py:1011`, `:1014` | `_MLX_AUDIO_FAMILIES = ("qwen3-tts", "omnivoice", "fish-audio-mlx", "pocket-tts",)` … `apple_optimized = m.family.endswith("-mlx") or m.family in _MLX_AUDIO_FAMILIES` | The **"Apple MLX" runtime label and the opt-in "🍎 MLX only" filter** (`components/voice/app/frontend/app.js:580`). An MLX-backed family whose id does not end in `-mlx` and is not in this tuple is published as **"PyTorch / MPS"** and **hidden** by the filter. The file's own comment says "Keep this set in sync with generation.MLX_AUDIO_FAMILIES … hence the explicit enumeration" — a sync list with no sync test. **This bug has already shipped once:** `components/voice/CHANGELOG.md:3646` records `kittentts`, `vibevoice` and `omnivoice` being hidden exactly this way, and the tuple was then pruned back to four names on 2026-09-24, restoring the shape for the next family. | **trap** — silent, per-family, **with a prior incident** | **untested** — no test compares this tuple to `generation.MLX_AUDIO_FAMILIES`; `test_priority_mlx_models.py:130` only uses the flag as fixture data | `generation.MLX_AUDIO_FAMILIES` — the circular-import excuse is avoidable (a small shared module, or a set built once at import and passed in) |
| **A7** | `components/hub/backend/fleet_nodes.py:112` (join), `:199` (registry load) | `NODE_MODALITIES = {"image", "voice", "transcription", "music"}` | **`chat` is missing**, and the file's own comment records the identical Music incident: *"while it was missing here, a node reporting its Music worker was refused outright at join time, so no current build could connect to a controller at all."* A node that runs the shipped Chat worker is **refused at join** (`raise ValueError`, `:135`), and at `:199` an already-joined node's chat studio is **silently skipped** when the controller builds its registry. | **trap** — recurrence of a documented incident; loud at join, silent in the registry | untested for a new modality | `components/hub/standalone.py:1635` `WORKER_FOR_MODALITY` is the Hub's modality vocabulary and is already exercised by an invariant test — derive from one source |
| **A8** | `components/hub/backend/memory_admission.py:32` | `SUPPORTED_MODALITIES = frozenset({"image", "voice", "transcription", "music", "video",})` | **`chat` is missing**, so `applies_to("chat")` is `False` → `components/hub/backend/broker.py:446` **skips the Hub's live-free-memory governor entirely for Chat jobs**, and `capabilities.py:241`/`:287` and `monitor.py:1099` publish **no admission evidence** for chat models. A 9B text model is dispatched with no Hub-side RAM floor. | **trap** — a safety gate silently not applied to a shipped modality | untested | the worker's own row (`min_total_memory_gb`) is already published; the modality list should not be the gate |
| **A9** | `components/voice/app/backend/reference_audio.py:42` | `return "qwen3-tts" in name and "base" in name` | A **name-substring** predicate decides whether a voice-cloning model must have an audited reference profile. A new cloning family that needs one silently falls back to generic defaults and produces what the function's own docstring calls *"a wrong execution copy instead of an honest error."* | **trap** — silent wrong output | untested | `model_audits.input_limits(model_id)` is already consulted two lines above, and the catalog already publishes `reference_audio.profile_audited` / `profile_source`. Require `profile_audited` whenever `capabilities` declares `voice-cloning` |
| **A10** | `components/voice/app/backend/transcription.py:156`, silent fallback at `:763` | `_PROCESSOR_BASE = { "mlx-community/whisper-large-v3-turbo": "openai/whisper-large-v3-turbo", … }` … `base = _PROCESSOR_BASE.get(repo, "openai/whisper-large-v3-turbo")` | A per-repo tokenizer-source table with a **silent default**. A newly added Whisper repo not listed here is given **`openai/whisper-large-v3-turbo`'s tokenizer** instead of its own — wrong vocabulary, wrong output, no error. `components/voice/app/tests/test_release_observability.py` never covers it. | **trap** — silent wrong output | untested | derive the base repo from the model's own `config.json`/`tokenizer_config.json`, or fail loudly like `qualification/measure_section_budget.py:147` does |
| **A11** | `components/hub/standalone.py:189` | `expected_modality = { "image-mflux": "image", …, "chat-mlx": "chat", }.get(manifest["engine"])` | A **second, inline copy of the engine vocabulary** in the same file as `_worker_for_engine` (`:82`). If a new engine is registered in the allowlist and the worker map but not here, `.get()` returns `None`, and `None != envelope.get("modality")` makes `_submission_readiness` refuse **every job for every model of that engine** with `MODEL_MODALITY_MISMATCH` — *"model is not available for this job modality"*, which names the user's request as the problem instead of the missing map. Reached on every brokered batch (`standalone.py:455`). | **trap** — loud refusal but **mislabelled cause**, and no "add it here" hint | **untested** — `tests/test_model_manifest_catalog.py:65` covers `_worker_for_engine` only, not this dict | `_worker_for_engine`'s single map should carry both (engine → worker, modality) |
| **A12** | `components/hub/statistics.py:39` **and** `components/hub/backend/monitor.py:487` | `activity_batches = dict(broker.batches)` … `for batch_id, batch in transcription_jobs.batches.items()` | Two identical copies of "which queues contribute live activity", and **`chat_jobs.batches` is in neither**. Chat jobs never appear in `/api/hub/stats` `fleet_activity` or in the polled activity ledger. The Chat worker's own `/api/fleet/activity` returns `active: None, latest: None` unconditionally (`components/chat/app/backend/main.py:100`), so Chat activity is **structurally invisible everywhere**. | **trap** — silent omission, two copies | untested | one producer-side list of job queues, or iterate the registered job modules |
| **A13** | `components/hub/standalone.py:660` (`/api/hub/model-packages`) with `_model_state` at `:92` | `return {"models": [_model_state(manifest, catalog, monitor) for manifest in catalog.values()]}` … `except ValueError as exc: raise HTTPException(503, str(exc))` | **The amplifier, not a list.** There is no per-model isolation: one malformed new manifest raises `PackageError` (a `ValueError`) inside the comprehension, the whole route answers **503**, and `native/StudioAPI.swift`'s `refresh()` reads that route inside a hard `try` — so **one bad manifest blanks every model picker on every Mac**, which is exactly the 0.6.57 failure mode. The engine allowlist (`components/model_packages.py:56`) is the most likely trigger. | **trap / robustness defect** | untested — no test asserts that one bad manifest leaves the other rows intact | wrap each row: a per-model error row instead of a whole-route failure |
| **A14** | `components/hub/backend/cloud_guard.py:41` | `LOCAL_PROVIDER_VALUES = {"", "local", "none", "hub", "on-device", "device"}` | An **allowlist of the strings a studio may report as `provider`**. A new studio (or a new engine) that reports `provider: "local-mlx"` or `"apple"` has **every row it publishes classified as cloud and filtered out of the aggregate catalog** (`components/hub/backend/monitor.py:866`) — that modality's models vanish from every picker with no message. This is the 0.6.57 symptom arriving by a different road. **[I]** the hidden-modality consequence is inference from reading `cloud_reason`'s call path; what is **[M]** is that the filter reads this set and drops matching rows silently. | **trap** — silent, whole-modality disappearance | untested | treat an unknown provider as unset unless it matches the **cloud** schemes, or have the producer publish `is_cloud: false` explicitly |
| **A15** | `packaging/chat_worker.py:46` + `packaging/hub_worker.py:149` | `manifest_path = resources / 'model-manifests' / 'qwen3-5-9b-4bit.json'` | The **packaged Chat worker is pinned to one manifest file**. A second chat model added to the catalog is published by the Hub, offered by the picker, dispatched to this worker — and the worker loads Qwen3.5-9B 4-bit regardless. The worker also **refuses any other model by name** (`components/chat/app/backend/main.py:136`, `404 "this worker serves only …"`). Silent for the new model; loud only if this one file is renamed. | **trap** for the model set; see **C9** for what is genuinely contract here | `tests/test_chat_packaging.py:30` reproduces the same pin in its fixture, so it is green by construction | `STUDIOHUB_MODEL_MANIFESTS` already points at the whole directory (`packaging/hub_worker.py:222`) |
| **A16** | `packaging/hub_worker.py:107`, `:118`, `:129`, `:143` | `if 'image' in release.get('components', {}):` … `if 'subtitle' in …` … `if 'music' in …` … `if 'chat' in …` | A per-component `if` ladder decides **which private worker processes the Hub spawns**. A new key in the release manifest's `components` map spawns **nothing**, with no error, and the Hub then routes to a worker that does not exist. The reverse (a listed component with missing files) is loud. | **trap** — silent, per-component | tests are **exact membership**: `tests/test_chat_packaging.py:43` `self.assertEqual([spec["id"] for spec in without], ["voice"])` and `:47` `self.assertEqual(ids, ["voice", "chat"])`. Neither asserts "every key in `release['components']` produced a spec" | `release['components']` **is** the data; iterate it against a `{component: spec_factory}` table |
| **A17** | `packaging/stage_voice.py:243`–`:297` | `'components': {'voice': {…}}` then `release['components']['hub'] = {…}`, `['subtitle']`, `['chat']`, `if music_runtime is not None: ['music']`, `if image_runtime is not None: ['image']` | What **ships**. A new studio's source is never copied into the bundle and never recorded, and the build succeeds. The file already does the right thing for models (`:252 copy_tracked_tree(ROOT / 'model-manifests', …)`) — there is no equivalent for components. | **trap** — silent, per-component | `tests/test_music_bundle.py` / `test_image_bundle.py` fixture the two that exist | a component table, or a per-component staging module discovered by convention |
| **A18** | `packaging/capture_model_manifests.py:36` (`CHOICES`) and `:282` | `CHOICES: tuple[dict[str, Any], ...] = (` … `"defaults": DETAILS[choice["package_id"]]["defaults"],` | Adding a model requires a `CHOICES` literal **and** a matching `DETAILS` entry; omitting `DETAILS` raises a **bare `KeyError` naming nothing**. Measured: the catalog holds **24** manifests, `CHOICES` holds **17** — seven shipped packages (`ace-step-1-5-turbo`, `chatterbox-4bit`, `chatterbox-8bit`, `fish-audio-s2-pro-8bit`, `fish-audio-s2-pro-bf16`, `s3tokenizerv2`, `stable-audio-3-medium`) have **no capture spec at all**, so `--write` cannot reproduce the shipped catalog. | **trap** — silent for the 7 unreproducible packages; loud-but-unhelpful for a missing `DETAILS` | `tests/test_model_manifest_catalog.py` re-implements the same list — see the exact-membership table in Annex 1 | `CHOICES` should be derived from, or checked against, the manifest directory |

---

## Tier B — loud, or silent with a smaller blast radius, or silent but covered

| # | Location | Literal | What it gates | Class | Test | Read instead |
|---|---|---|---|---|---|---|
| **B1** | `components/model_packages.py:56` | `if manifest.get('engine') not in ('image-mflux', 'voice-mlx', 'voice-vits', 'subtitle-mlx', 'music-mlx', 'chat-mlx'):` | The engine vocabulary. A new engine raises `PackageError` (**loud**), but through `_model_catalog()` it becomes a **503 on the whole packages route** (see A13) → blank pickers. This is the named place to register an engine, and the message should say so. | contract with a **fleet-wide blast radius** | **covered** — `tests/test_model_manifest_catalog.py:65` walks the real catalog and fails if the allowlist and `_worker_for_engine` disagree; `:91` asserts the unknown-engine message names the engine and the file | — |
| **B2** | `components/hub/standalone.py:1635` | `WORKER_FOR_MODALITY = {"image": "image", "voice": "voice", "transcription": "subtitle", "music": "music", "chat": "chat"}` | The peer-proxy modality vocabulary. An unknown modality 404s `"unknown studio"` (**loud**, unlike A11). | trap, but loud and **covered** | `tests/test_peer_path_contract.py:67` asserts exact membership of the parsed map, and `:94` asserts the native fixture's `hubRoutedModalities` equals it — so adding a modality forces the app fixture to be updated too | — |
| **B3** | `components/hub/standalone_config.py:19`, rejection at `:70` | `_REQUIRED_WORKERS = {"image": "image", "voice": "voice", "subtitle": "transcription", "music": "music", "chat": "chat"}` … `raise ValueError("worker descriptor has an unsupported id")` | Which workers may register **at all**. A new worker id is refused and the Hub **will not start**. Loud, but the failure is a startup abort rather than "add your worker here", and adding a modality to an existing worker id needs a second edit at `:71`. | trap — loud, high blast radius | untested for a new worker | derive the expected modality from the worker's own descriptor and validate it against `WORKER_FOR_MODALITY` |
| **B4** | `components/hub/backend/workload_policy.py:16`, `:20` | `_MIN_MACHINE_MEMORY_GB_BY_REPO_PREFIX = {"mlx-community/qwen3-tts-12hz-0.6b-": 8.0, "qwen/qwen3-tts-12hz-0.6b-": 8.0}` | The Hub's **stricter production** memory floor, keyed by repo **prefix**. A new model silently keeps only its catalog value, so the production policy is not applied. Fails "not strict enough", not "invisible". | trap — silent policy omission | untested | the worker already publishes `min_unified_memory_gb` / `min_free_memory_gb`; the function already takes `max(catalog, table)` — publish the policy floor in the manifest instead |
| **B5** | `components/hub/backend/memory_admission.py:39` | `_FLEET_DEFAULTS = {"aitrader/flux2-klein-4b-mlx-4bit": {…}}` | A per-model fleet-measured override. A new model the owner has proven on 8 GB silently gets the generic 2 GB default. | trap — silent policy omission | untested | same as B4 |
| **B6** | `components/hub/backend/artifact_metadata.py:16`, `.get` at `:30` | `_MEDIA_TYPES = {"image": {...}, "video": {...}, "render": {...}, "voice": {...}, "music": {...}}` … `media_type if media_type in _MEDIA_TYPES.get(modality, set()) else None` | Allowed artifact MIME types per modality. **`transcription` and `chat` are already absent**, and a new modality defaults to the empty set → artifacts are proxied as `application/octet-stream` instead of inline media. Uses `.get(..., set())`, so it degrades silently rather than raising. | trap — silent degradation | untested | a default entry per modality, or derive from the artifact's own byte validation (`wav_metadata` already exists) |
| **B7** | `components/hub/backend/broker.py:2622`, `:2630` | `elif modality in {"voice", "music", "video"}:` … `else:` | Machine **placement preference**. `chat` (a 9B model) and `transcription` fall into the `else` bucket, whose comment says *"Image and subtitle prefer the smallest capable machine"* — so the Chat model is preferentially scheduled onto the **smallest** Mac. A preference only, so nothing fails; it just runs badly. | trap — silent misplacement | untested | the model's own memory floor already exists; classify by the modality the *worker* declares rather than a literal set |
| **B8** | `components/hub/backend/broker.py:146`, `:200` | `DURABLE_ASYNC_MODALITIES = frozenset({"image", "music"})` / `RECEIPT_DURABLE_MODALITIES = … \| {"voice"}` / `_EXTERNAL_LANES = frozenset({"chat", "transcription"})` | Retry/idempotency class and per-machine fairness. A new async modality not added to the first set is treated as the non-durable kind → **duplicate-artifact risk on retry**. A new auxiliary lane silently loses fairness (not correctness). | contract for a closed behaviour class — but a new modality must be classified deliberately | untested | — |
| **B9** | `components/hub/backend/activity.py:33` vs `:35`, `:41`, indexed at `:128` | `VALID_STUDIOS = frozenset({"image","voice","transcription","music","chat"})` … `_ALLOWED_OPERATIONS = {…}` (no `chat`) / `_DEFAULT_OPERATION = {…}` (no `chat`) … `operation = value.get("operation", _DEFAULT_OPERATION[studio])` | `chat` is in `VALID_STUDIOS` but in neither operation map, so a chat activity snapshot with no explicit `operation` raises **`KeyError: 'chat'`**. Currently unreachable only because the Chat worker always sends `None` (see A12). Latent trap inside one file. | trap — loud when reached | untested | derive the operation vocabulary from the studio's own reported `operation` |
| **B10** | `components/hub/backend/registry.py:207`, `:384`, `:386`, skip at `:393` | `TRACKED_MODALITIES = ("image", "voice", "transcription", "music")` … `FAMILY_PORTS = {47868: "image", 47870: "voice"}` … `MODALITY_EMOJI = {"image": "🎨", "voice": "🎙️"}` | Legacy (non-packaged) pre-registration: `chat` is missing, and `MODALITY_PORT.get(mod)` returns `None` for anything but image/voice → the studio is silently skipped. **`build_machine_entries` has no caller anywhere in `components/` or `tests/`** (verified by grep), so this is currently dead code. | latent trap — dead code today | untested | — |
| **B11** | `components/hub/backend/monitor.py:171` | `TRANSCRIPTION_MODALITIES = {"voice", "transcription"}` | Which studios contribute Whisper/STT availability to the aggregate catalog. A new modality that reports transcription availability is silently not refreshed or folded in. This exact shape already caused every Subtitle job to sit queued for ever (the comment above it records it). | trap — silent, with a prior incident | untested | the producer's advertised capability, not the modality name |
| **B12** | `components/hub/backend/cloud_guard.py:27`, `:33`, `:46` | `CLOUD_ID_SCHEMES = ("provider:", "fal:", "kie:", "replicate:")` / `CLOUD_ID_NAMESPACES = ("cloudflare/", "gemini/", …)` / `CLOUD_COST_TIERS = {"free","paid"}` | The **denylist** of hosted-model signals. A new cloud vendor's namespace is not matched, so a cloud model is **accepted and queued** — the opposite direction from A14, and a policy failure rather than a disappearance. | trap — silent policy hole | untested | an explicit `provider` on every worker row (the field is already read) |
| **B13** | `components/subtitle/app/backend/engine.py:418`, `:436`–`:439`, and `components/subtitle/app/backend/manager.py:249` | `'family': manifest.get('family') or 'whisper'` … `'languages': 'Multilingual'`, `'supports_segment_timestamps': True`, `'supports_word_timestamps': True`, `'supports_long_form': True` | Because **no shipped manifest declares `family`**, every subtitle package is published as family `whisper`, and every package claims Multilingual + segment/word timestamps + long form regardless of the manifest's truth. A non-Whisper subtitle engine is mislabelled; a model that cannot produce word timestamps is advertised as able to (twice — `manager.py:249` restates it for the availability payload). | trap — silent mislabelling | untested | `manifest.get('family')`, and add `languages` / `supports_*` to the manifest schema (already validated centrally) |
| **B14** | `components/subtitle/app/backend/engine.py:450`–`:451` | `if 'whisper-turbo' in self.manifests: return 'whisper-turbo'` (falling through to `return sorted(self.manifests)[0]` at `:452`) | The Subtitle default model. The data-driven path exists three lines above (`:448 if manifest.get('default') is True`) but **no shipped subtitle manifest sets it**, so the hardcode is load-bearing: retiring `whisper-turbo` silently changes the default to alphabetical-first. | trap — silent default drift | untested | set `"default": true` in the manifest — the code already honours it |
| **B15** | `components/subtitle/app/backend/manager.py:109` | `raise ValueError('choose one of the packaged Whisper models')` | The refusal text hardcodes "Whisper" for a vocabulary that is the `whisper-*` glob (A1). Cosmetic, but it is how an operator learns the wrong lesson. | cosmetic | — | name the ids that are available |
| **B16** | `components/voice/app/backend/generation.py:612`, `:663`, `:698` | `_ENGINE_REQUIREMENTS = {…}` / `_WIRED_FAMILIES = {…}` / `MLX_AUDIO_FAMILIES = {…}` | **Three family lists in one module.** `availability()` at `:542` builds the frontend's `wired_families` from `MLX_AUDIO_FAMILIES` (data-driven — good), while the diagnostics panel's per-engine `wired` comes from `_WIRED_FAMILIES`. A family in one and not the others gets **contradictory status in the same UI**. A family in none reaches `raise NotImplementedError(f"No worker implemented for family '{family}'.")` at `:3278` — loud and correctly worded. | trap with a **good** loud backstop | untested for the cross-list agreement | one table; `_ENGINE_REQUIREMENTS` is per-family metadata, not a separate list |
| **B17** | `components/voice/app/backend/generation.py:245`, `:658`, `:1265` and `components/voice/app/backend/long_form_policy.py:142` | `_NUMBER_NORMALISED_FAMILIES = frozenset({"omnivoice"})` / `_KHMER_VITS_FAMILIES = frozenset({"khmer-tts-mms", "khmer-tts-onnx"})` / `_POSTPROCESSED_SPEED_FAMILIES = {…}` / `NATIVE_SPEED_FAMILIES = frozenset({"kokoro-mlx", "kittentts"})` | Small per-family **behaviour** sets. A family omitted gets the *other* behaviour silently: a new non-MLX Khmer-like family not in `_KHMER_VITS_FAMILIES` goes down the MLX path; a new family that predicts its own durations instead of getting a tempo pass silently gets a second processing pass. | trap — silent wrong behaviour | `tests/test_section_policy.py:88` pins `NATIVE_SPEED_FAMILIES` to exactly two names — exact membership | move the flag onto the family entry in `catalog.FAMILIES` (a dataclass field), where the rest of the family's facts live |
| **B18** | `components/voice/app/backend/transcription.py:892`, `:931` | `elif spec.engine == "moonshine":` … `elif spec.engine == "nemotron":` / `else:` for both dispatch and result-shape normalisation | A new ASR engine falls into the **bare `else`**: dispatched with nemotron's call signature and normalised with `_result_field(result, "segments")`. Likely a `TypeError` (loud) at generate, but the normalisation branch would silently produce empty segments for an engine with a different result shape. | trap — mostly loud, one silent branch | untested | `spec.engine` is the producer's own value; an unknown engine should refuse by name |
| **B19** | `components/voice/app/backend/model_audits.py:20`, `:137` | `_QWEN_17B_BASE = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"` … `if model_id != _QWEN_17B_BASE: return {}` | A deliberate single-model pin (the production-v2 audit's authority) with a documented empty return for everything else. **Genuine contract** for that audit, but it is a per-model branch: a new model needing an equivalent policy gets nothing until a new function is written. | contract with a per-model branch | `components/voice/app/tests/test_qwen_quality_guardrails.py` / `test_model_audit_contract.py` | the generic `audit_record(model_id)` glob (`model_audits.py:94`) already finds every `*/*.audit.json` |
| **B20** | `components/voice/app/backend/qwen_quality.py:31` | `WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"` | The companion ASR model the Qwen clone-quality gate runs. Hardcoded in the engine; the model must also exist as a packaged manifest. | contract-ish (a quality gate against one measured validator) | covered | the catalog's `requires_packages` already declares this relationship |
| **B21** | `components/voice/app/frontend/app.js:1234`, `:1258`, `:1327` | `const repo = this.gen.repo \|\| "mlx-community/Kokoro-82M-bf16";` | The shipped **API example** body names a repo that is not in the packaged catalog. Copy-paste output is wrong until a model is selected. | cosmetic | untested | the selected model's repo, or the first supported |
| **B22** | `native/StudioAPI.swift:4348` | `let supported = (engine.contains("whisper") \|\| engine.contains("subtitle")) && package != nil` | The Transcription picker: any engine not named whisper*/subtitle* is **excluded** even when the worker reports it ready and cached. Silent. **Honest scope note:** nothing shipped is hidden *today*, because the Subtitle worker's own glob (A1) narrows further — so this is a latent trap whose live effect is the **mislabelling** below, not a disappearance. Related: `:4353` `let qualified = item.repo == whisperTurbo` and `:4356` `"Compatible Whisper contract; …"` describe every non-Whisper engine as a Whisper contract. | **trap** — latent silent exclusion + present silent mislabelling | **trap test** — `tests/native/api_main.swift:1724` `require(supportedSTT.map(\.repo) == ["mlx-community/whisper-large-v3-turbo"], "native Transcription support guard")`. The fixture at `:783` ships `moonshine-ai/moonshine-base` with `engine: "moonshine", cached: true` **and no Hub package**, so the assertion passes for two independent reasons and cannot tell which gate excluded it — then breaks when the next model is added | `packages[repo] != nil` (already computed in the same expression), plus `TranscriptionModel.engine` / `cached` / `supportsWordTimestamps` / `default_model` / `recommended`, all of which the producer already publishes |
| **B23** | `native/StudioAPI.swift:1454` | `for batch in (jobsValue?.batches ?? []) where ["voice", "image", "music"].contains(batch.modality) {` | Which Hub batches load into the app at all. A new modality is **skipped before the loop** — not shown, not counted, not reported by the `unreadable` counter (which only counts detail-fetch failures). Transcriptions survive only through a separate route. | **trap** — silent, per-modality | untested; the fixture only emits voice/image/music | the batch's own `modality` plus the workspace's function list; the fixture's `hubRoutedModalities` already mirrors the Hub's table |
| **B24** | `native/StudioAPI.swift:4371`, `:4417` | `let supported = item.imageConfiguration != nil && package != nil` / `let supported = item.musicConfiguration != nil && package != nil` | `imageConfiguration`/`musicConfiguration` are computed by requiring the **currently shipped engine's exact control set** (image: width/height/steps/guidance/seed + non-empty `image_sizes`; music: duration/steps/guidance_scale/shift/seed). A second engine of the same modality publishing a different control set decodes to `nil` → `supported = false` → **filtered out of the picker**. Silent. This is the right *shape* (data-driven) with too rigid a *schema*. | trap — silent, per-engine | untested — one image model and one music model in the fixture, both in the expected shape; `api_main.swift:903` also asserts an exact model count | keep the data-driven configuration but make the control keys optional per model and fall back to the model's own `generation_defaults` |
| **B25** | `native/StudioAPI.swift:4385` + `native/StudioViews.swift:941` | `return capabilities.contains("chat.completion") \|\| capabilities.contains("chat")` … `Text("No ready Chat model. Install the local Chat model and start its worker; the page sends to Qwen3.5-9B through the Hub.")` | A chat model spelled `text-generation`/`conversational` never appears; and the Chat page **never calls `modelPicker(.chat)`**, so even a second listed chat model cannot be chosen. The empty-state copy names one model whatever is selected. | trap — silent, per-model | untested; the fixture has no chat model | `capabilities`, `family`, and the same `modelPicker` the other four pages use |
| **B26** | `native/StudioAPI.swift:1016`, `:1024`, `:1075`, `:4149`, `:4154` | `static let qwenBaseRepos: Set<String> = [ …4 repos… ]` / `static let whisperTurbo = "mlx-community/whisper-large-v3-turbo"` / `@Published var audioModel = qwenBase` / `audioModel = Self.qwenBase` / `}?.repo ?? Self.whisperTurbo` | Hardcoded defaults. `reconcileSelections` "repairs" the Audio selection to a **hardcoded repo** instead of the first supported model, so on a library with no Qwen Base the page opens on a model that is not in its list and Generate stays disabled until the tab is changed. `qwenBaseRepos` also gates only the *wording* of the qualification note, so it silently mislabels a new quant. | trap — mostly silent (disabled/empty picker) | untested | the first supported model of the function — the pattern already used for image (`:1444`) and music (`:1446`) — and the worker's own `default_model` for transcription (`reconcileSelections(defaultTranscription:)` at `:4147`) |
| **B27** | `native/StudioAPI.swift:1420`, `:4317` | `audioSpeakers = (generationValue?.qwen3PresetSpeakers ?? []).map(\.id)` / `let roster = (supported && mode == .preset && isQwen) ? speakers : []` | Only the **Qwen-shaped** preset roster key is decoded, and it is lent only to `family == "qwen3-tts"`. A second preset family that publishes ready-made voices gets an **empty roster** → the Ready-Made tab reads "This model has not reported its ready-made voices yet." Also `audioSpeaker = audioSpeakers.first ?? "Ryan"` hardcodes a Qwen speaker name. **This is the one case where the honest fix needs a producer field** — there is no per-model speaker roster in the catalog payload today. | trap — silent, per-family; **needs a producer change** | `tests/native/api_main.swift` asserts only the negative case (`khmer?.speakers.isEmpty == true`) | publish the roster per model (the catalog already publishes `reference_audio` per model — a `preset_speakers` sibling would match) |
| **B28** | `native/StudioViews.swift:2046` | `Button("Use in \(model.function.rawValue)") { if model.function == .audio { … } else { workspace.transcriptionModel = model.repo; studio.select(.transcription) } }` | For an Image/Music/Chat model the button says "Use in Image/Chat" but sets the **Subtitle** selection and opens Subtitle with a foreign repo. Silent misroute. | bug | untested | switch on `model.function` |
| **B29** | `native/ShellCore.swift:21` (`StudioPage.create` / `comingLater`), `native/StudioViews.swift:13` (`HistoryTab`), `:892` (`workspaceStatus`), `:3447` (stats order) | `static let create: [Self] = [.image, .audio, .transcription, .music, .chat]` / `case all, image, audio, music, subtitle` / `guard page == .audio \|\| … \|\| page == .chat else { return "Engine not installed" }` / `let order: [(StudioFunction, String)] = [(.image,"Image"), …]` | Four independent per-modality display lists. A new modality needs all four; anything missed is silent — not counted in the sidebar summary, Overview reads "Engine not installed", no history tab, stats icon falls back to `"clock"`. `StudioFunction`/`StudioPage` **raw values are a genuine contract** (persisted); the display/aggregation lists are duplicative. | mixed: contract + trap | `tests/native/main.swift:192` restates the enum's raw values (passes by construction); nothing covers `HistoryTab`/`workspaceStatus` | `StudioFunction.allCases` — the Models-page filter already does exactly this (`native/StudioViews.swift:1926`) |
| **B30** | `native/UpdateClient.swift:461` | `static let requiredRuntime = "Contents/Resources/runtimes/voice/bin/python3"` | The installer's completeness check verifies **only the Voice runtime**. A release missing the image/music/chat/subtitle runtime still installs (silent until that worker fails); if the voice layout moves, every release is refused (loud false negative). | trap | the negative test asserts this one hardcoded path, not "every declared runtime is present" | the signed `ReleaseDescriptor` could list required paths |
| **B31** | `native/StudioAPI.swift:661`, `:655`, `:652` | `static let languageOptions = ["en","zh","ja","ko","de","fr","ru","pt","es","it"]` / `licenseOptions` / `switch gender { case "m": … }` | The voice-library metadata vocabulary lives in the app. A shipped Khmer voice cannot be tagged `km`; an out-of-vocabulary worker value renders blank or "Neutral". | trap — silent | untested | the worker owns the voice store and returns `language`/`gender`/`license` verbatim |
| **B32** | `qualification/smoke_new_models.py:99` | `if row.get("engine") not in (None, "voice-mlx"): continue` | Which models the end-to-end voice smoke exercises. **`voice-vits` is a shipped engine** (`khmer-tts-mms`, `khmer-tts-female-onnx`) and both are skipped with **no message and no `skipped` row** — contrast the explicit `results.append((repo, "skipped", …))` used elsewhere in the same file. Every new voice engine is silently un-smoked. The same file also has a dead `PICKER_FAMILIES` set and a family→availability-key table that silently yields no preset for an unknown family. | trap — silent coverage hole | the file *is* a test harness | the row's own `engine`/`capabilities`, and the loud pattern from `qualification/measure_section_budget.py:147` |
| **B33** | `qualification/generate_manifests_from_cache.py:37` | `ENGINE = "voice-mlx"` (written into every generated manifest at `:139`) | Put an image, music, subtitle or `voice-vits` repo in this file's `CHOSEN` list and it is emitted with `"engine": "voice-mlx"` — the Hub then routes it to the Voice worker. Silent wrong manifest. Its `CHOSEN` list (14 repos) also silently omits any catalog model not listed. | trap — silent wrong data | untested | take the engine from the source manifest/repo family; `:75` already reads `catalog.FAMILY_COMPANIONS` correctly |
| **B34** | `qualification/evidence/measure_machine.py:183` | `parser.add_argument("--kinds", default="image,subtitle,voice")` | What a fleet machine measurement covers. `--kinds music` or `--kinds chat` **silently does nothing** — no run, no `skipped` row, exit code unchanged, so Music and Chat models are never measured on any machine. Also `:362` takes only the **first** verified image package, so Z-Image-Turbo is never measured and the miss is reported as "no verified FLUX package". | trap — silent, per-modality | the script is evidence tooling | `:331` already shows the right shape (a preference with a fallback) |
| **B35** | `qualification/evidence/st10-memory-policy-packaged.py:104` | `for expected in ("voice", "image", "subtitle"):` | A **gate**: only these three studios must appear in the memory inventory, and `evidence["ok"] = not problems` / `return 0 if not problems else 6`. A build where `music` and `chat` are entirely absent prints `"ok": true` and exits 0. | trap — silent pass | the script is a gate | iterate the inventory the script already holds (`:91`) and assert each row is ready |
| **B36** | `qualification/copy_models_for_this_mac.py:42` | `PROVEN_SMALLER = { … "flux2-klein-4b-mlx-4bit": 8, }` | A per-package memory override. A new model the owner has proven below its declared floor is left behind on smaller Macs until this dict is edited. The skip **is** printed, so it is visible — the *reason* is not. This file is otherwise the model to copy: it globs `model-manifests/*.json` (`:297`) and selects by the manifest's own `minimum_unified_memory_gb` (`:309`) and `requires_packages` (`:317`). | trap — visible but unexplained | — | manifest fields |
| **B37** | `packaging/smoke_voice.py:59`, `:82` | `'repo': 'mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit'` … `'model': 'mlx-community/whisper-large-v3-turbo'` | The pilot smoke exercises exactly one TTS model and one ASR model against a frozen 2-model snapshot. A new voice model is not smoked and nothing says so. | trap — silent coverage hole | the script is a smoke gate | — |
| **B38** | `packaging/image-runtime/smoke_imports.py:21` | `"mflux.models.flux2.variants.txt2img.flux2_klein",` | The Image **runtime** import smoke hardcodes only the FLUX Klein class. Z-Image-Turbo 4-bit's class is never imported, so a broken/missing `z_image` module in the shipped runtime **passes the smoke** and fails only when the owner runs Z-Image. | trap — silent coverage hole | the script is a smoke gate | import every class named by `image/generation.py:MODELS` |
| **B39** | `packaging/kit-restructure.sh:23` | `DRIVES=(KH-STUDIO-NOVA KH-STUDIO-UGREEN KH-STUDIO-ATLAS KH-STUDIO-ORICO)` | A new drive is never visited. Also version-pinned installer names at `:47`, `:52`, `:90` — a new build silently stays put (`skip (absent)`). | trap — silent, operator tooling | — | enumerate mounted volumes |
| **B40** | `copy-models.command:33`, `install_app.command:14` | `"$HERE/Studio Hub 0.4.0.app" "$KIT/Studio Hub 0.4.0.app"` | Version-pinned app names, dead weight rather than a break (the unversioned candidate is tried first). | cosmetic/stale | — | `packaging/stage_voice.py:309` deliberately dropped the version from the bundle name for this reason |

---

## Tier C — genuine contracts, and enumerate-and-forget lists that are safe today

These are listed so the next reader does not "fix" them. Each states why it is closed.

| # | Location | Literal | Why it is a contract |
|---|---|---|---|
| **C1** | `components/hub/backend/broker.py:131` | `MODALITY = {"image": ("/api/generate/txt2img", "prompt", "image"), "music": …, "voice": …, "video": …, "render": …}` | The **batch-generation** dispatch table: each entry is a (worker endpoint, prompt field, artifact kind) triple that must match a real worker route. `_submit_batch_locked` refuses an unknown modality by name and lists the vocabulary. Note `transcription` and `chat` are deliberately **not** here — they have their own job modules (`transcription_jobs.py`, `chat_jobs.py`), so this is not the whole modality vocabulary and should not be treated as one. |
| **C2** | `components/hub/backend/capabilities.py:22` | `OPERATION_BY_MODALITY = {"image": "image.text_to_image", …, "subtitle": "audio.transcription", …}` | A published **peer-facing operation namespace**. It is also the one list here that degrades correctly: `:257` and `:709` fall back to `f"{modality}.operation"` rather than failing. Closed because peers join on these strings. |
| **C3** | `components/model_packages.py:52`–`:62` | `package_id` regex, `revision` regex, `minimum_macos` regex | Wire-format validation of the manifest schema. A closed grammar, not a model list. |
| **C4** | `components/voice/app/backend/catalog.py:62` (`FAMILIES`) and `:347` (`CATALOG`) | `FAMILIES: dict[str, Family] = {…}` / `CATALOG: tuple[ModelEntry, ...] = (…)` | **This is the Voice producer registry — the right place to add a family or a model.** Not a consumer list. One caveat: `FAMILIES[m.family]` is a bare index at `:1022`, so a `CATALOG` row with a missing/typo'd family id raises `KeyError` at the first `/api/catalog`, and there is no validation that every `CATALOG[i].family in FAMILIES`. |
| **C5** | `components/voice/app/backend/generation.py:698` | `MLX_AUDIO_FAMILIES: dict[str, dict] = {…}` | A per-family **engine configuration** table (sample rate, which kwarg resolver, label). A new family genuinely needs a mode decision — but the file's own comment states the required edit list ("add a catalog FAMILIES entry, add ModelEntry rows, drop a config line here, list it in `_WIRED_FAMILIES` + `_ENGINE_REQUIREMENTS`"), and the `NotImplementedError` backstop at `:3278` is correctly worded. |
| **C6** | `components/voice/app/backend/long_form_policy.py:406` | `section_max_characters=CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS,` (with the note "This family has no section-size measurement of its own yet…") | **The exemplar to copy: an unknown family gets a named, documented, safe default instead of nothing.** The per-family tuning above it is measurement, not a gate. |
| **C7** | `components/hub/backend/model_ids.py` (whole file) | `for key in (model_id, manifest.get("package_id")): index.setdefault(key.strip().lower(), model_id)` | **The exemplar to copy: one canonical model identity derived from the manifest directory**, with an mtime+filename cache signature so adding a manifest is noticed. Every route that carries a model can join on one string. |
| **C8** | `components/voice/app/backend/catalog.py:968` (`FAMILY_COMPANIONS`) + `:993` (`companions_for`) | `FAMILY_COMPANIONS: dict[str, tuple[dict, ...]] = {"chatterbox-mlx": ({…}), "pocket-tts": ({…})}` | Per-family runtime companions (codecs/tokenizers the engine fetches at generation time). Closed because each entry encodes a specific loader's behaviour. Read through `companions_for(repo)`, so an unlisted family returns `()` rather than raising. |
| **C9** | `components/chat/app/backend/main.py:136` | `if requested and requested != engine.model_id: raise HTTPException(404, f"this worker serves only {engine.model_id}")` | The packaged Chat worker is **deliberately single-model**: one `STUDIOHUB_CHAT_MANIFEST`, one engine, one entry in `/v1/models`. A second chat model therefore needs a worker change, not just a manifest. Loud and self-describing. **But the row it publishes hardcodes `"label": "Qwen3.5-9B"` and `"family": "qwen3.5"` (`:110`, `:112`) while `repo`/`model_id` come from the manifest** — so re-pointing the worker at a different manifest publishes the wrong label and family, silently. That part is a trap. |
| **C10** | `packaging/release_kit.py:74` | `for manifest_path in sorted((resources / "model-manifests").glob("*.json")):` | **Data-driven**: iterates the whole shipped catalog, records a package the kit does not carry instead of failing (`:96`), passes `components` through verbatim (`:141`). |
| **C11** | `packaging/stage_voice.py:252` | `copy_tracked_tree(ROOT / 'model-manifests', resources / 'model-manifests', suffix='.json')` | **Data-driven**: a new manifest ships automatically. There is no equivalent for components — see A17. || **C12** | `components/hub/backend/auth.py:414` `FLEET_ALLOWED`, `components/voice/app/backend/voices.py:67` (`ALLOWED_AUDIO_EXTS`), `:69` (`ALLOWED_LICENSES`), `:70` (`ALLOWED_GENDERS`), `components/hub/backend/execution_assets.py:29` (`ALLOWED_EXTENSIONS`), `components/voice/app/backend/reference_audio.py:26`, `components/hub/backend/chat_jobs.py:127` (`_ALLOWED_PARAMS`) | container formats, licences, genders, HTTP paths, OpenAI parameter names | Closed vocabularies owned by an external standard or a security boundary, not by the model catalog. Not model enumerations. |
| **C13** | `native/StudioViews.swift:878`, `:3245`; `native/StudioViews.swift:892` | `switch modality { case "image": … default: return "clock" }` / `default: return modality.capitalized` | Presentation-only, each with a generic `default:`. Unknown input degrades legibly. Not a gate — kept here so nobody "fixes" it into a hard failure. |
| **C14** | `native/ShellCore.swift:4`, `native/StudioPage` raw values | `enum StudioFunction: String { case audio = "Audio", … }` | **Genuine contract**: raw values are persisted in settings. Adding a case is a deliberate schema change. The *duplicated display lists* built on top of it are the trap (B29). |
| **C15** | `components/voice/app/backend/transcription.py:79` (`TRANSCRIPTION_MODELS`) with `:134` (`WHISPER_MODELS = tuple(model for model in TRANSCRIPTION_MODELS if model.engine == "whisper")`) | a producer registry plus a **derived** subset | The registry is the right place to add an ASR model, and `WHISPER_MODELS` is correctly derived rather than restated. The file's comment ("Adding a new ASR model is a one-line append here") **overstates** for the packaged app: the model also needs a `whisper-*`-named subtitle manifest (A1) and, for Whisper repos, a `_PROCESSOR_BASE` entry (A10). |
| **C16** | `components/hub/backend/broker.py:200` `_EXTERNAL_LANES`, `components/hub/backend/monitor.py:164` `LOCAL_ONLY_MODALITIES = {"render"}`, `components/subtitle/app/backend/engine.py:64` `_DEFAULT_LABELS` | behaviour classes / a documented exemption / cosmetic labels with an explicit fallback at `:409` | Each is a deliberate classification of a *known* member. The traps are the omissions from them, listed in Tier B (B8, B11, B13). |

---

## Annex 1 — test coverage: exact membership vs invariant

**Exact membership over the shipped catalog — a trap that must be hand-edited for every new model.**

| Location | Assertion | Note |
|---|---|---|
| `tests/test_model_manifest_catalog.py:105` | `self.assertEqual(set(self.manifests), set(EXPECTED))` | **Measured, and it changed under me.** When this audit began the directory held **24** manifests while `EXPECTED` (`:15`) held **21**, so this test was **red** and the digest loop raised `KeyError`. Another agent fixed it mid-audit by adding the three `pocket-tts-*` entries. **That fix demonstrates the trap:** adding three models required hand-editing `EXPECTED`, then `expected_digests`, then the required-paths dict (`:215`–`:217`), then the `requires_packages` assertion (`:237`) — four hand edits in one test file, none of which an invariant form would need. |
| `tests/test_model_manifest_catalog.py:215`, `:237` | a per-package required-paths dict, and hardcoded id tuples for the whisper processor and the qwen dependencies | A new model gets no asset check |
| `tests/test_st01_runtime_contracts.py:74` | `self.assertEqual(set(runtimes), {"voice-mlx", "subtitle-mlx", "image-mflux"})` | Three engines that already ship (`voice-vits`, `music-mlx`, `chat-mlx`) are absent from the runtime contract, so the test asserts the doc's set, not "every shipped engine has a contract". Fails loudly. |
| `tests/test_subtitle_standalone.py:251` | `self.assertEqual(sorted(entries), ['whisper-base', 'whisper-small', 'whisper-turbo'])` | A fourth transcription model fails and must be hand-added. |
| `tests/test_music_package_contract.py:113` | `self.assertEqual([model.repo for model in catalog.CATALOG], [catalog.MAIN_REPO, catalog.STABLE_AUDIO_REPO])` | Pins the count to two. Uses module constants (better than literals). |
| `tests/test_peer_path_contract.py:67` | `self.assertEqual(hub_modalities(), {"image","voice","transcription","music","chat"})` | Exact membership, but defensible: it is paired with `:94`, which compares the **native fixture's** modality set to the Hub's map — so a new modality forces the app fixture to learn it. |
| `tests/test_section_policy.py:88` | `self.assertEqual(sorted(long_form_policy.NATIVE_SPEED_FAMILIES), ["kittentts", "kokoro-mlx"])` | Exact membership over a family vocabulary. |
| `tests/native/api_main.swift:1724` | `require(supportedSTT.map(\.repo) == ["mlx-community/whisper-large-v3-turbo"], "native Transcription support guard")` | **The surviving instance of incident 3.** Cannot distinguish "excluded by the engine allowlist" from "excluded by having no Hub package" — the fixture provides both reasons. |
| `tests/native/api_main.swift:903`, `:1004`, `:1590`, `:1597`, `:1598` | `require(store.models.count == 14, …)`, `storageFamilies.count == 2`, an exact `verify:` set, `verifyAll.count == 7`, and an exact summary string `"Verified 7 of 9. Could not verify: Other TTS, Moonshine."` | Hand-maintained counts and an exact human-readable string over the fixture. |
| `tests/test_chat_packaging.py:43`, `:47` | `self.assertEqual([spec["id"] for spec in without], ["voice"])` / `self.assertEqual(ids, ["voice", "chat"])` | Exact membership over spawned workers; a new component has no test. |

**Invariant tests that catch the next model — copy these.**

| Location | Assertion |
|---|---|
| `tests/test_model_manifest_catalog.py:65` | walks `CATALOG.glob("*.json")`; every shipped manifest must pass `validate_manifest` **and** have a worker from `_worker_for_engine`. This is what makes an engine registration a one-place change. |
| `tests/test_model_manifest_catalog.py:91` | an unknown engine raises a `ValueError` naming the engine **and** `model_packages.py`. |
| `tests/native/api_main.swift:1669` | `Set(supportedAudio.map(\.repo)) == Set(voiceCatalog…)` where `voiceCatalog` is derived from the fixture's own published rows. **This is the fix for incident 3** and the pattern to reuse. |
| `tests/test_peer_path_contract.py:94` | parses `WORKER_FOR_MODALITY` out of the Hub source and asserts the native fixture's `hubRoutedModalities` equals it. |
| `tests/test_image_standalone.py:462`, `:547` | `self.assertEqual(set(listed), set(MODELS))` with `MODELS` imported from the component. |
| `tests/test_section_policy.py:23`, `:36` | `FAMILIES = sorted({entry.family for entry in catalog.CATALOG})` derived from the producer, plus `test_an_unknown_family_gets_the_conservative_default` — *"A newly adopted family must inherit sectioning rather than skip it."* |
| `tests/test_subtitle_standalone.py:884` | *"The alias map must be derived from the real manifests, not hard-coded."* asserts the source reads the manifests rather than literally checking the answer. |

**Not covered by any test (searched, found nothing):** the `apple_optimized` ↔ `MLX_AUDIO_FAMILIES` sync (A6); the `dependencies`/`requires_packages` split (see HUB-19 below); per-model isolation on `/api/hub/model-packages` (A13); the `chat_jobs` omission from activity (A12).

---

## Annex 2 — stale hardcoded counts in documents (docs are read; these are wrong)

| Location | Literal | Measured |
|---|---|---|
| `model-manifests/README.md:3` | `This catalog contains 37 bounded model packages. The files describe` (and `:4` `89,369,563,824 expected bytes`) | **24** packages (verified `ls model-manifests/*.json \| wc -l`) |
| `packaging/kit-readmes/Studio Hub KH.md:98` | `The kit carries **19 model packages** (about 59 GB).` | 24 — **and this file ships to the owner** |
| `qualification/SSD-KIT-README.md:8` | `- **Models:** `model-library/` — 12 verified packages, ~29 GB` | 24 |
| `qualification/NODE-SETUP-CHECKLIST.md:4` | `with 17 model packages (ACE-Step and Stable Audio 3 included).` | 24 |
| `packaging/kit-readmes/README.md:11` | a table pinning the kit to `0.6.57` | `VERSION` is `0.6.60` |
| `docs/runtime-contract.md:27` | `\| engine \| image-mflux, voice-mlx or subtitle-mlx \|` | **six** engines ship (`voice-vits`, `music-mlx`, `chat-mlx` added since) |
| `qualification/focused-release-manifest.json` | a checked-in `release_kit.py` output listing 12 models, two of which (`qwen3-tts-12hz-0-6b-base-4bit`, `qwen3-tts-12hz-1.7b-base-8bit`) are **not in the catalog**, and a `components` map with no `chat`/`music` | a stale sample; `qualification/verify-kit.sh:66` reads the drive's copy, not this one |

**`model-manifests/README.md` is where a person adding a model will look — and its "Refreshing metadata" section (`:32`–`:39`) is incorrect**: it says to run `capture_model_manifests.py --write` and then the catalog test, which (a) does nothing unless you first hand-edit `CHOICES` **and** `DETAILS` (A18), and (b) then fails `EXPECTED`/`expected_digests` (Annex 1).

---

## HUB-19 — a data-contract split worth naming separately

`components/hub/standalone.py:55-68` (`_dependency_package_ids`) reads **`entry.get("dependencies")`**, while
`components/model_packages.py:89` (`validate_manifest`), `:428`/`:460` (`preflight`, `verify_installed`)
and `:648` (`download_package`) read **`requires_packages`**. **Both spellings are in the shipped
catalog** (verified by reading every manifest):

- `chatterbox-4bit.json`, `chatterbox-8bit.json` → `"dependencies": ["s3tokenizerv2"]` (no `requires_packages`)
- `qwen3-tts-12hz-0-6b-base-8bit.json`, `qwen3-tts-12hz-1-7b-base-4bit.json` → `"requires_packages": ["whisper-turbo"]` (no `dependencies`)

Consequences, both silent:

1. **`"type"` is wrong for one dependent.** `standalone.py:113` labels a package `"dependency"` iff it
   appears in `_dependency_package_ids` (which reads `dependencies`). `s3tokenizerv2` is correctly
   typed a dependency; **`whisper-turbo` is published as a pickable `"model"`**, and it is a
   **Subtitle** package (engine `subtitle-mlx`, a Whisper ASR model) that the Hub offers in the
   Voice model list. A third package spelling its dependency the "wrong" way is mistyped with no
   error.
2. **A declared companion is never verified or downloaded.** `preflight`/`download_package` honour
   only `requires_packages`, so `chatterbox`'s declared `s3tokenizerv2` companion is not resolved
   as a dependency — the engine fetches its tokenizer at generation time instead, which is exactly
   the offline failure `FAMILY_COMPANIONS` (`components/voice/app/backend/catalog.py:968`) exists to
   prevent.

**Classification:** trap (two names for one relationship; the consumer and the producer disagree).
**Test:** `tests/test_model_manifest_catalog.py:213` asserts `requires_packages` for the two Qwen
packages only — the `dependencies` spelling is untested, and `test_every_imported_file_has_an_immutable_hash…`
does not look at either key. **Data-driven alternative:** validate one key centrally (add
`dependencies` to `validate_manifest` or migrate the two manifests) and have `_dependency_package_ids`
read it.

---

## How to add a model without this biting you

Work top to bottom. The right-hand column is the failure you get if you skip the step.

### 1. Every new model

| Step | Where | If you skip it |
|---|---|---|
| Capture the manifest (needs a `CHOICES` **and** a `DETAILS` entry) | `packaging/capture_model_manifests.py:36`, `:282` | bare `KeyError` (A18) |
| Add the manifest JSON | `model-manifests/<package_id>.json` | nothing ships |
| Extend the release-menu tests | `tests/test_model_manifest_catalog.py:15` `EXPECTED`, `:116` `expected_digests`, `:215`'s required-paths dict, `:237`'s dependency assertion | the suite is red until you do — four hand edits per model batch. The three `pocket-tts-*` models added on 2026-09-24 needed exactly these four. |
| Make the *engine's* own registry know it (the producer) | voice: `components/voice/app/backend/catalog.py:347` `CATALOG` + `:62` `FAMILIES` · image: `components/image/app/backend/generation.py:194` `MODELS` · music: `components/music/app/backend/catalog.py:352` `CATALOG` **and all three `PACKAGE_*` maps at `:154`/`:158`/`:163`** · subtitle: nothing (manifest-driven — but see the id rule below) · chat: needs a worker change (C9) | image: **silently absent from the picker** (A5). music: **published with ACE-Step's revision and limits** (A3, A4). voice: `KeyError` on `/api/catalog` (C4) |
| If it is a Voice family: declare its engine behaviour | `components/voice/app/backend/generation.py:698` `MLX_AUDIO_FAMILIES`, `:663` `_WIRED_FAMILIES`, `:612` `_ENGINE_REQUIREMENTS`, and — **for a non-`-mlx` id — `catalog.py:1011` `_MLX_AUDIO_FAMILIES`** | `NotImplementedError` (loud, B16) or an MLX model labelled "PyTorch / MPS" and **hidden by the MLX-only filter** (silent, A6) |
| If it needs a runtime companion (codec/tokenizer) | `components/voice/app/backend/catalog.py:968` `FAMILY_COMPANIONS` | the engine fetches it at generation time and fails offline |
| If it needs its own default: declare it, do not hardcode it | `manifest["default"] = true` — honoured at `components/subtitle/app/backend/engine.py:448` | silent default drift (B14) |
| If it is a **Subtitle** model: name the package id `whisper-…` | `components/subtitle/app/backend/main.py:307` | **the worker never loads it** (A1) — the single highest-value trap in this inventory |
| If it is a **Whisper** repo: add its tokenizer base | `components/voice/app/backend/transcription.py:156` `_PROCESSOR_BASE` | silently uses **turbo's tokenizer** (A10) |
| If it is a **cloning** model: make the reference contract explicit | the audit record's `reference_audio`, or `components/voice/app/backend/reference_audio.py:42` | silent wrong execution copy (A9) |
| If it is a **Qwen Base** tier: extend the qualified list | `native/StudioAPI.swift:1016` `qwenBaseRepos` | the note silently says "has not completed the native pilot test" (B26) |
| Add it to the smoke/measure scripts you rely on | `qualification/smoke_new_models.py:99`, `qualification/evidence/measure_machine.py:183`, `packaging/smoke_voice.py:59`, `packaging/image-runtime/smoke_imports.py:21` | the model is never exercised and nothing says so (B32, B34, B37, B38) |

### 2. Every new **engine**

| Step | Where | If you skip it |
|---|---|---|
| Register it in the allowlist | `components/model_packages.py:56` | `PackageError` → **503 on `/api/hub/model-packages` → every picker blank on every Mac** (B1, A13) |
| Register it in the worker map | `components/hub/standalone.py:82` `_worker_for_engine` | same 503 (the drift test at `tests/test_model_manifest_catalog.py:65` catches this one) |
| **Register it in the modality map** | `components/hub/standalone.py:189` `expected_modality` | **every job for that engine is refused** as `MODEL_MODALITY_MISMATCH` — "model is not available for this job modality" — blaming the user (A11). **The drift test does not cover this dict.** |
| Give it a runtime contract | `research/st01-runtime-contracts.json` + `docs/runtime-contract.md:27` | `tests/test_st01_runtime_contracts.py:74` is red; the doc stays wrong |
| Teach the app to decode its control shape | `native/StudioAPI.swift:5008` / `:5019` (the `imageConfiguration` / `musicConfiguration` validators) | `supported = false` → **silently filtered out of the picker** (B24) — or, for a transcription engine, `native/StudioAPI.swift:4348` (B22) |

### 3. Every new **worker / studio / modality**

| Step | Where | If you skip it |
|---|---|---|
| Register the worker's modality | `components/hub/standalone_config.py:19` `_REQUIRED_WORKERS` | the Hub **will not start** (B3) |
| Add it to the peer-proxy map | `components/hub/standalone.py:1635` `WORKER_FOR_MODALITY` | 404 "unknown studio"; `tests/test_peer_path_contract.py:67`/`:94` are red (loud, and the native fixture is forced along) |
| Add it to the fleet join vocabulary | `components/hub/backend/fleet_nodes.py:112` `NODE_MODALITIES` | **a node running it is refused at join** — the Music incident, again (A7) |
| Spawn it from the release manifest | `packaging/hub_worker.py:107`–`:143` | **it is never started, with no error** (A16) |
| Stage its source and record the component | `packaging/stage_voice.py:243`–`:297` | it is not in the bundle, and the build succeeds (A17) |
| Give it a RAM gate | `components/hub/backend/memory_admission.py:32` `SUPPORTED_MODALITIES` | **the Hub's free-memory governor is skipped for it** (A8) — this is Chat's current state |
| Give it an activity operation | `components/hub/backend/activity.py:35`/`:41` **and** `components/hub/backend/ledger.py:38` | `KeyError` when a snapshot omits `operation` (B9); activity silently filed under `speech` (A2) |
| Add it to the live-activity queues | `components/hub/backend/monitor.py:487` **and** `components/hub/statistics.py:39` | its jobs never appear in activity or stats — Chat's current state (A12) |
| Classify its retry/fairness behaviour | `components/hub/backend/broker.py:146`, `:200` | duplicate-artifact risk on retry; a lane silently loses fairness (B8) |
| Classify its placement | `components/hub/backend/broker.py:2622` | it silently prefers the **smallest** capable Mac (B7) — Chat's current state |
| Allow its artifact types | `components/hub/backend/artifact_metadata.py:16` | artifacts proxied as `application/octet-stream` (B6) |
| Teach the app | `native/ShellCore.swift:21`, `native/StudioViews.swift:13`, `:892`, `:3447`, `native/StudioAPI.swift:1454` | silently missing from the sidebar summary, Overview, history and jobs (B23, B29) |
| Run the invariant tests, not the membership ones | `tests/test_model_manifest_catalog.py:65`, `tests/test_peer_path_contract.py:94`, `tests/native/api_main.swift:1669` | — |

### 4. The three habits that would have prevented all three incidents

1. **A list in a consumer is a bug until you can name the producer field it should have read.** Where
   you cannot, add a **named conservative default with a reason** (`long_form_policy.py:406`) or
   **fail loudly naming the entry to add** (`standalone.py:82`, `image/generation.py:304`) — never a
   bare `KeyError`, a silent `.get(…, default)`, and never an empty list.
2. **Never derive `supported` from a name.** Derive it from the pair *(catalog row, Hub package)* and
   the row's own `capabilities` / `reference_audio` / `*Configuration`, and let the worker be the
   authority on runnability. `native/StudioAPI.swift:4306` is now the model to copy.
3. **Test the invariant, not the membership.** "Every shipped family is selectable" catches the next
   model; "the picker equals these two" catches nothing and is edited by hand into permanence.

---

## What I could not cover

- **I did not run any test suite.** The statement that `tests/test_model_manifest_catalog.py` was
  red at the start of this audit is derived **statically** (AST count of `EXPECTED` = 21 against 24
  files on disk, plus reading `:105` and the digest loop), not from observing a failure; another
  agent then fixed it mid-audit by adding the three entries, which I re-measured. Running the suite
  myself would have written `__pycache__` into a tree I was told to keep read-only.
- **The tree moved under me.** `native/StudioAPI.swift`, `tests/native/api_main.swift`,
  `components/voice/app/backend/catalog.py`, `components/voice/app/backend/generation.py` and
  `packaging/capture_model_manifests.py` were all edited while I read them, and HEAD advanced to
  `0579db1` mid-audit. All citations were machine-verified against the hashes above; if a hash has
  changed, **grep the literal** rather than trusting the line number.
- **`components/hub/backend/broker.py` is 3,700 lines and I swept it, not read it end to end.** I
  enumerated its module-level vocabularies and its per-modality branches by grep
  (`modality ==`, `modality in`, `b["modality"]`); roughly forty per-modality `if` branches exist
  there, mostly **enrichment** (which extra fields are copied into the public item). I did not read
  each one. A new modality gets a *minimal* public item from those branches — field omissions, not
  failures — and I did not exhaustively list which fields.
- **`qualification/evidence/**`** was grepped for model/engine/studio vocabulary and yielded
  `measure_machine.py` and `st10-memory-policy-packaged.py`; the per-model probe scripts under
  `r07-2026-09-21/` were not read end to end. They matched no model-id literal in my greps, but I
  cannot claim they contain none.
- **Two rows are inference, not measurement, and are flagged as such in place:** B27 (the preset
  roster) is the one finding whose honest fix needs a *producer* field I could not confirm exists —
  I could only confirm that the app decodes no per-model roster today. A14's effect on a *future*
  studio is inference from reading `cloud_reason`'s call path, not from observing a hidden model.
- **The three new `pocket-tts-*` manifests** (correctness of their digests, URLs and
  `requires_packages` wiring) were not checked — that is `test_manifest_contents_match_the_captured_inventory`'s
  job, and it cannot currently run for them.
- **I did not audit** `native/build.sh`, `render-page.sh`, `sdk.sh`,
  `packaging/make_app_icon.py`, `qualification/time_kit_voices.py`,
  `qualification/import_my_voices.py`, `qualification/check-voices-folder.sh` (voice *assets*, not
  models). `native/ShellCore.swift`, `native/StudioHub.swift`, `native/StartupRecovery.swift`,
  `native/ReleaseNotes.swift` were checked for model/family/engine/modality enumerations and contain
  none beyond what is listed here.
