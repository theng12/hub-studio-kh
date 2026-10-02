"""Private standalone Image API for the registered, qualified mflux image models."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import platform
import subprocess

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import loading_mode, memory_policy
from .generation import (MEDIA_TYPE_BY_SUFFIX, MODEL_ID, MODELS, QUALIFIED_MINIMUM_MEMORY_BYTES,
                         SIZE_PRESETS, GenerationManager, defaults_in_force, final_matches,
                         lane_contract, lane_parameters, loading_modes,
                         missing_runtime_model_reason)

from components import app_version, model_defaults


DEFAULT_MODEL = MODELS[MODEL_ID]


def _host_facts() -> dict:
    """What this Mac is, before any model is considered."""
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        return {'eligible': False, 'reason': 'Apple Silicon macOS is required'}
    try:
        memory = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'],
                                             text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'eligible': False, 'reason': 'Unified memory could not be measured'}
    version = platform.mac_ver()[0]
    if not version:
        return {'eligible': False, 'reason': 'macOS version could not be measured'}
    if int(version.split('.')[0]) < 14:
        return {'eligible': False, 'reason': 'macOS 14 or later is required'}
    return {'eligible': True, 'macos': version, 'unified_memory_bytes': memory,
            'qualified_minimum_memory_bytes': QUALIFIED_MINIMUM_MEMORY_BYTES,
            'qualification': '16 GB 1K txt2img historical source receipt; this host still needs packaged proof'}


def memory_warning(model, host: dict) -> str | None:
    """The tier warning for one model on one host.

    The worker warns and does not refuse; the Hub owns the decision, and since 2026-09-30 it
    enforces the model's RAM requirement (measured, else manifest, else this catalog row) as a
    hard eligibility floor. FLUX.2 Klein is qualified at 16 GB but the fleet measured it on the
    owner's 8 GB Macs, so the Hub's requirement for it is 8 GB. Z-Image-Turbo's warning says what
    is actually known — its 8 GB tier is being measured — rather than refusing the machine.
    """
    memory = host.get('unified_memory_bytes') or 0
    if (not host.get('eligible') or not memory or not model.below_tier_warning
            or memory >= model.qualified_minimum_memory_gb * 1e9):
        return None
    return model.below_tier_warning.format(memory_gb=memory / 1024**3)


def _host_eligibility() -> dict:
    """Host capability plus the default model's tier warning.

    Kept as the one host-reading entry point the API and the tests use; each catalog entry
    carries the warning for its own model.
    """
    host = _host_facts()
    return {**host, 'memory_warning': memory_warning(DEFAULT_MODEL, host)}


def create_app(manager: GenerationManager, token: str) -> FastAPI:
    if not token:
        raise ValueError('Private Image worker requires its owning Hub credential')
    app = FastAPI(title='Studio Hub Image', version='1')

    @app.middleware('http')
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get('x-studio-token') or request.headers.get('x-hub-token')
                   or request.headers.get('authorization', '').removeprefix('Bearer '))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({'detail': 'Owner credential required'}, status_code=401)
        return await call_next(request)

    def model_state(model) -> dict:
        """One model's readiness: its own installed files, on this host."""
        files = manager.preflight(model.repo)
        host = _host_facts()
        ready = files['state'] == 'files_verified' and host['eligible']
        return {'ready': ready, 'files': files, 'host': host,
                'runtime_identity': {'id': manager.runtime_sha256, 'compatible': True}}

    def readiness() -> dict:
        """The default model's readiness, for the worker-level paths that predate the table."""
        return model_state(DEFAULT_MODEL)

    @app.get('/api/health')
    def health() -> dict:
        active = [job for job in manager.list() if job['state'] in ('queued', 'running')]
        return {'ok': True, **app_version.version_fields(), 'generation': {
            'available': True, 'busy': bool(active), 'queued': sum(j['state']=='queued' for j in active),
            'running': sum(j['state']=='running' for j in active),
            'runtime_revision': manager.runtime_sha256}}

    @app.get('/api/fleet/activity')
    def fleet_activity() -> dict:
        """The optional private activity contract the Hub polls after health."""
        return manager.activity_snapshot()

    def catalog_entry(model, selected: str) -> dict:
        """One registered model, described entirely by its own registry entry.

        The app merges this list with the Hub's model packages and keys on `repo`, so a
        registered model appears in the picker with its own defaults, sizes and limits.
        """
        state = model_state(model)
        host = state['host']
        warning = memory_warning(model, host)
        # The values this worker will actually generate with on this Mac: what the model states,
        # overridden per parameter by what the operator chose (`model_defaults.json`). One call,
        # so the catalog's `generation_defaults`, its size table and the lane contract's per
        # parameter `default` cannot publish two answers to one question — and none of them can
        # publish a number `validate_request` would refuse, because `resolved()` validates on the
        # way out too.
        in_force = defaults_in_force(model, manager.data)
        # The geometry follows the aspect ratio in force, not the one the model shipped with: a
        # stored ratio whose width/height still came from another preset would be a form that
        # disagrees with the job it starts.
        default_width, default_height = SIZE_PRESETS[in_force['aspect_ratio']]
        sizes = [{'id': ratio, 'label': ratio, 'width': width, 'height': height}
                 for ratio, (width, height) in SIZE_PRESETS.items()]
        ready = state['ready']
        files = state['files']
        # How this machine's loading-size setting resolves for this model, from the model's own
        # MemoryPolicy: `selected` is the operator's choice, `effective` is what the next job
        # will actually load, and a model with no bounded configuration publishes no options.
        modes = loading_modes(model, manager.total_memory_bytes, selected)
        return {
            'label': model.label, 'name': model.label, 'repo': model.repo,
            'model_id': model.repo, 'family': model.family, 'modality': 'image',
            'package_id': model.package_id,
            'capabilities': ['txt2img'], 'hub_operation': 'image.text_to_image',
            'model_revision': model.revision, 'qualified_revision': model.revision,
            'qualified_revision_match': True, 'runtime_revision': manager.runtime_sha256,
            'runtime_compatible': True, 'runtime_ready': True,
            # `min_total_memory_gb` is the tier the model is qualified at. The worker does not
            # refuse on it: the Hub decides (its requirement floor - measured, else manifest, else
            # this row - is enforced), and `memory_warning` tells the operator their Mac is below.
            'memory_eligible': host['eligible'],
            'min_total_memory_gb': model.qualified_minimum_memory_gb,
            'qualified_minimum_memory_gb': model.qualified_minimum_memory_gb,
            # The key the Hub's own policy reads (`workload_policy.required_total_memory_gb`),
            # the last rung of its requirement ladder (override, measured, manifest, this row).
            'min_unified_memory_gb': model.qualified_minimum_memory_gb,
            # The floors the Hub's governor reads per model: `min_free_memory_gb` is the live
            # free-RAM guard before dispatch; `min_total_memory_gb`/`min_unified_memory_gb` feed
            # the requirement the Hub enforces as an eligibility floor (measured/manifest first).
            'min_free_memory_gb': model.min_free_memory_gb,
            'memory_warning': warning,
            # What this host will actually do with the model, and the bound it is choosing.
            # Bytes, not rounded GB, so the app can format it the way the rest of the fleet
            # contract does.
            'memory_policy': {
                'mode_for_this_host': modes['effective'],
                'total_memory_bytes': manager.total_memory_bytes,
                'low_ram_below_bytes': modes['threshold_bytes'],
                'measured_peak_gb': model.qualification.get('measured_peak_gb'),
            },
            # The machine-wide loading-size setting, per model: whether this model has a
            # bounded configuration to choose, which one this Mac's setting selects, and the
            # measured trade of each option in this model's own numbers. Empty `options` with
            # a `reason` is a model that has one loading mode, not a missing answer.
            'loading_modes': modes,
            'cache': {'state': 'cached' if files['state'] == 'files_verified' else files['state']},
            'readiness': {'ready': ready, 'files': files, 'host': host,
                          'reason': None if ready else files.get('reason') or host.get('reason')},
            'execution_ready': ready, 'available': ready,
            # The values in force, with the geometry derived from the aspect ratio in force. The
            # app pre-fills its form from this and sends every field, so this is the number an
            # operator's stored default has to reach it by.
            'generation_defaults': {**model.defaults, **in_force,
                                    'width': default_width, 'height': default_height},
            'guidance_ignored': model.guidance_ignored,
            'image_sizes': sizes,
            'generation_limits': {key: value for key, value in model.limits.items()
                                  if key != 'max_prompt'},
            'max_prompt_characters': model.limits['max_prompt'],
            'sizes': [{'aspect_ratio': item['id'], 'width': item['width'],
                       'height': item['height'], 'tier': '1K',
                       'default': item['id'] == in_force['aspect_ratio']} for item in sizes],
            # How to ask for this lane's work, per model: operation, accepted parameters and
            # their enforced bounds, artifact, state vocabulary and cancel semantics. Built from
            # this model's own limits so it cannot disagree with `validate_request`, with each
            # settable parameter's `default` resolved to the value in force on this machine.
            'lane_contract': lane_contract(model, manager.data),
            'qualification': dict(model.qualification),
        }

    def entry_without_a_runtime_model(manifest: dict) -> dict:
        """One published row for an Image package this runtime has no model for.

        The reverse of ``model_state``: a manifest the Hub publishes and the kits can install,
        which this worker cannot run. It is published rather than dropped — a row that says
        why, with ``execution_ready`` false — because an absent row is invisible everywhere.
        No generation controls are invented for it: defaults this runtime has never run would
        be a worse lie than the honest refusal at job time.
        """
        host = _host_facts()
        reason = missing_runtime_model_reason(manifest)
        return {
            'label': manifest.get('label') or manifest['package_id'],
            'name': manifest.get('label') or manifest['package_id'],
            'repo': manifest['model_id'], 'model_id': manifest['model_id'],
            'family': manifest.get('family'),
            'modality': 'image', 'engine': manifest['engine'],
            'package_id': manifest['package_id'],
            # The engine's own capability claim, so a reader can tell this is an image package
            # at all. It carries no `generation_defaults`/`generation_limits`, which is what
            # the app requires before it offers a model for selection.
            'capabilities': ['txt2img'],
            'hub_operation': 'image.text_to_image',
            'model_revision': manifest['revision'],
            'memory_eligible': host['eligible'],
            'cache': {'state': 'no_runtime_model'},
            'readiness': {'ready': False, 'state': 'no_runtime_model', 'reason': reason,
                          'files': {'state': 'no_runtime_model', 'reason': reason},
                          'host': host},
            'runtime_compatible': False, 'runtime_ready': False,
            'execution_ready': False, 'available': False,
        }

    @app.get('/api/catalog')
    def catalog() -> dict:
        # One read of the machine-wide setting for the whole catalog, so every row answers the
        # same question and a change mid-response cannot make two models disagree.
        selected = loading_mode.current(manager.data)
        entries = [catalog_entry(model, selected) for model in MODELS.values()]
        # The manifests are already read (`load_image_manifests`) and a registered model whose
        # manifest is missing reports itself per model; this is the other direction, published
        # instead of absent.
        entries.extend(entry_without_a_runtime_model(manifest)
                       for manifest in manager.packages_without_a_runtime_model())
        return {'models': entries}

    @app.post('/api/generate/txt2img')
    def submit(body: dict) -> dict:
        state = _host_eligibility()
        if not state['eligible']:
            raise HTTPException(409, state['reason'])
        try:
            return {'job': manager.submit(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/generate/jobs')
    def jobs() -> dict:
        return {'jobs': manager.list()}

    @app.get('/api/generate/jobs/{job_id}')
    def job(job_id: str) -> dict:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': value}

    @app.delete('/api/generate/jobs/{job_id}')
    def cancel(job_id: str) -> dict:
        value = manager.cancel(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': value}

    @app.get('/api/generate/jobs/{job_id}/image')
    def image(job_id: str):
        value = manager.get(job_id)
        if value is None or value['state'] != 'done':
            raise HTTPException(404, 'final image is unavailable')
        # The delivered file is the one the job names: the PNG, or the JPEG a job asked for.
        path = manager.final_path(value)
        if not final_matches(value, path):
            raise HTTPException(409, 'final image changed after generation')
        return FileResponse(path, media_type=MEDIA_TYPE_BY_SUFFIX.get(path.suffix, 'image/png'),
                            headers={'X-Content-SHA256': value['sha256']})

    @app.post('/api/generate/jobs/{job_id}/ack')
    def ack(job_id: str) -> dict:
        receipt = manager.ack(job_id)
        if receipt is None:
            raise HTTPException(404, 'completed Image job not found')
        return receipt

    # ── model memory policy ────────────────────────────────────────────────
    # The fleet contract every Studio publishes: how long a loaded model stays loaded, and the
    # release the Hub's machine-wide pass and fleet handoff ask for (see `memory_policy`).

    memory_policy.start_background(manager)

    @app.get('/api/memory-policy')
    def memory_policy_status() -> dict:
        return memory_policy.status(manager)

    @app.put('/api/memory-policy')
    def memory_policy_save(body: dict | None = None) -> dict:
        try:
            return memory_policy.save(str((body or {}).get('mode') or ''))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/memory/release')
    def memory_release() -> dict:
        try:
            return memory_policy.release_now('manual')
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ── loading-size setting (`loading_mode`) ───────────────────────────────
    # Deliberately separate from `/api/memory-policy` above: that is the release policy. This is
    # which mflux memory *configuration* the next job
    # loads with - `auto`, `low` or `full`. It is a setting, not a per-model fact: what each
    # model does with it (and whether it has a bounded configuration at all) is published per
    # model in the catalog's `loading_modes`, derived from the model's own MemoryPolicy.

    def loading_mode_status() -> dict:
        selected = loading_mode.current(manager.data)
        return loading_mode.status(
            manager.data, manager.total_memory_bytes,
            [(model.repo, loading_modes(model, manager.total_memory_bytes, selected))
             for model in MODELS.values()])

    @app.get('/api/loading-mode')
    def get_loading_mode() -> dict:
        return loading_mode_status()

    @app.put('/api/loading-mode')
    def put_loading_mode(body: dict | None = None) -> dict:
        try:
            loading_mode.save(manager.data, (body or {}).get('mode'))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return loading_mode_status()

    # ── per-model generation defaults (`model_defaults`) ────────────────────
    # Which numbers the next job generates *with*: `resolution`, `aspect_ratio`, `steps`,
    # `guidance` and `seed`, stored per model and per Mac, validated against the very bounds
    # `lane_contract` publishes, and applied by `validate_request` to a parameter the request
    # **omits** (an explicitly supplied parameter always wins).
    #
    # This is the other axis from `loading_mode` directly above, and the two are deliberately not
    # merged. A model default says *what to ask the model for*; `loading_mode` says *which mflux
    # memory configuration loads* to answer it. They are independent settings in independent
    # files (`model_defaults.json` here, `loading_mode.json` there): setting `steps` for
    # Z-Image-Turbo must not load a different memory configuration, and forcing low-RAM must not
    # change a single number in the request.
    #
    # A parameter the lane marks `required` (`prompt`) is not defaultable: it is per-request
    # content, and a stored value would answer with something nobody wrote this time.

    def model_defaults_status() -> dict:
        """Every registered model's published parameters and this Mac's values in force."""
        return model_defaults.status(
            manager.data,
            [(model.repo, lane_parameters(model)) for model in MODELS.values()])

    def registered_model(repo: object):
        """The registered model a route names, or a refusal that says which repos exist."""
        model = MODELS.get(repo) if isinstance(repo, str) else None
        if model is None:
            raise HTTPException(
                422, f'{repo!r} is not a registered image model; this worker serves: '
                     + ', '.join(sorted(MODELS)))
        return model

    @app.get('/api/model-defaults')
    def get_model_defaults() -> dict:
        return model_defaults_status()

    @app.put('/api/model-defaults')
    def put_model_defaults(body: dict | None = None) -> dict:
        values = (body or {}).get('values')
        if not isinstance(values, dict):
            raise HTTPException(422, 'values must be an object of parameter to default')
        model = registered_model((body or {}).get('repo'))
        try:
            model_defaults.save(manager.data, model.repo, values, lane_parameters(model))
        except ValueError as exc:
            # The shared reader, so all five Studios answer a bad PUT the same way: every refused
            # parameter with its own reason, or — if this is not one of our refusals — the message.
            raise HTTPException(422, detail=model_defaults.refusals(exc) or str(exc)) from exc
        return model_defaults_status()

    @app.delete('/api/model-defaults')
    def delete_model_defaults(body: dict | None = None, repo: str | None = None) -> dict:
        # Either a JSON body (the shape the app sends) or `?repo=`, so a caller that cannot put a
        # body on DELETE still reaches the same call.
        model = registered_model((body or {}).get('repo') or repo)
        model_defaults.clear(manager.data, model.repo)
        return model_defaults_status()

    return app


def from_environment() -> FastAPI:
    from components import model_packages
    from .generation import load_image_manifests
    import json
    data = Path(os.environ['STUDIOHUB_IMAGE_DATA_DIR'])
    library = Path(os.environ['STUDIOHUB_MODELS_DIR'])
    manifest_path = Path(os.environ['STUDIOHUB_IMAGE_MANIFEST'])
    manifest = json.loads(manifest_path.read_text())
    model_packages.validate_manifest(manifest)
    # The bundle names one Image manifest explicitly and ships the whole model-manifests
    # directory beside it (`packaging/stage_voice.py:252`), so every registered model's
    # manifest is read from there. A sibling that fails to load is reported per model in the
    # catalog; only the named manifest is fatal.
    manifests, errors = load_image_manifests(manifest_path.parent, manifest_path)
    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())
    identity = release['components']['image']['runtime_sha256']
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError('Release manifest lacks exact Image runtime identity')
    return create_app(GenerationManager(data, library, manifest, identity,
                                        manifests=manifests, manifest_errors=errors),
                      os.environ['STUDIOHUB_FLEET_TOKEN'])
