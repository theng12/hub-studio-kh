"""Private standalone Subtitle API.

One owning Hub credential, one loopback port, its own job ledger and its own
artifact tree. The Voice-compatible ``/api/transcribe`` and
``/api/transcribe/availability`` shapes let the existing Hub transcription
lifecycle drive this worker the same way it drives Voice's STT subsystem;
everything under ``/api/generate/transcribe`` is the Subtitle Studio's own
public lifecycle.
"""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from components import app_version as app_version_module
from components import model_defaults

from . import memory_policy
from .engine import ENGINE as SUBTITLE_ENGINE
from .engine import ModelPackageError, host_eligibility
from .manager import SubtitleManager, safe_input_name, validate_request, MAX_INPUT_BYTES


UPLOAD_CHUNK_BYTES = 1024 * 1024


async def read_upload_limited(file, limit: int) -> bytes:
    """Read an upload, refusing the moment it passes the limit.

    `await file.read()` returned the entire body first and the size was checked on the result, so an
    oversized upload was buffered in this process before being refused - and this worker's limit is
    2 GiB, on a Mac that is also holding a model in unified memory. Reading in chunks stops at the
    first byte past the limit, so the refusal costs one chunk instead of the whole file.
    """
    from fastapi import HTTPException as _HTTPException
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
        except TypeError:
            # An upload double whose `read` takes no size (Starlette's real UploadFile does, and so
            # does the Hub's client): one read, then the limit is applied to what came back.
            chunk = await file.read()
            total += len(chunk)
            if total > limit:
                raise _HTTPException(
                    413, f'upload exceeds the {limit // (1024 * 1024)} MB per-file limit')
            chunks.append(chunk)
            break
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _HTTPException(
                413, f'upload exceeds the {limit // (1024 * 1024)} MB per-file limit')
        chunks.append(chunk)
    return b"".join(chunks)


def read_app_version() -> str:
    """The release this worker was staged from, through the one shared reader.

    `/api/health` answered the literal `"standalone"`, and the Hub publishes whatever a worker
    says there as `workers[].studio_version` — so three of four workers carried a non-version in
    a version field. The answer comes from `components/app_version.py`, which reads the release
    manifest this worker was launched with (`STUDIOHUB_RELEASE_MANIFEST`), reports the checkout's
    own version for a development run, and says `"unknown"` only when it has neither — a value
    that cannot be mistaken for a release number.
    """
    return app_version_module.release_version()


def create_app(manager: SubtitleManager, token: str, *, app_version: str | None = None) -> FastAPI:
    if not token:
        raise ValueError('Private Subtitle worker requires its owning Hub credential')
    fields = dict(app_version_module.version_fields())
    if app_version is not None:
        fields['app_version'] = app_version
    app = FastAPI(title='Studio Hub Subtitle', version=fields['app_version'])
    # `/api/health` reports these; the Hub republishes the version as the worker's studio version.
    app.state.version_fields = fields
    # One resident Whisper model, released when idle. Without this a Subtitle model stayed
    # loaded for the life of the process, which is the memory that matters on an 8 GB Mac.
    memory_policy.start_background(manager)

    @app.middleware('http')
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get('x-studio-token') or request.headers.get('x-hub-token')
                   or request.headers.get('authorization', '').removeprefix('Bearer '))
        if not hmac.compare_digest(offered, token):
            # A bare 401 across a fleet is unattributable, so the refusal names what it required
            # and what arrived. A controller does not hold this credential and must not: this
            # worker accepts only the Hub token it was launched with, and the Hub that owns it
            # re-signs every proxied request with that token before forwarding it.
            offered_via = [name for name, value in (
                ('X-Studio-Token', request.headers.get('x-studio-token')),
                ('X-Hub-Token', request.headers.get('x-hub-token')),
                ('Authorization: Bearer', request.headers.get('authorization')),
            ) if value]
            return JSONResponse({
                'detail': ('Owner credential required: this Subtitle worker accepts only the Hub '
                           'token it was launched with (X-Studio-Token, X-Hub-Token or an '
                           'Authorization: Bearer header). Offered: '
                           + (', '.join(offered_via) if offered_via else 'no credential')
                           + '. A controller reaching this Mac through its Hub does not present '
                             'this token; the owning Hub re-signs the request with it.'),
                'required': 'hub-token', 'offered': offered_via,
            }, status_code=401)
        return await call_next(request)

    def public_job(job: dict | None) -> dict | None:
        if job is None:
            return None
        value = dict(job)
        value.pop('input', None)
        return value

    # ── health / catalog ───────────────────────────────────────────────────

    @app.get('/api/fleet/activity')
    def fleet_activity() -> dict:
        """The optional private activity contract the Hub polls after health."""
        return manager.activity_snapshot()

    @app.get('/api/health')
    def health() -> dict:
        active = [job for job in manager.list() if job['state'] in ('queued', 'running')]
        return {'ok': True, **app.state.version_fields, 'transcription': {
            'available': True,
            'busy': bool(active),
            'queued': sum(job['state'] == 'queued' for job in active),
            'running': sum(job['state'] == 'running' for job in active),
            'runtime_revision': manager.engine.runtime_sha256,
            'loaded_package': manager.engine.loaded_package}}

    @app.get('/api/catalog')
    def catalog() -> dict:
        return {'models': manager.catalog(),
                'default_model': manager.default_package(),
                'default_rule': manager.engine.default_rule,
                'export_source': manager.engine.export_source(),
                'host': host_eligibility()}

    # ── model memory policy ────────────────────────────────────────────────
    # Path names are the fleet contract every Studio publishes, matching the Voice worker's
    # `GET/PUT /api/memory-policy` and `POST /api/memory/release`. The Hub's
    # FleetMemoryControl reads all of them through this one shape.

    @app.get('/api/memory-policy')
    def memory_policy_status() -> dict:
        return memory_policy.status()

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

    # ── per-model operator defaults ────────────────────────────────────────
    # The same three paths Voice publishes, in this Studio's own state directory beside
    # `memory_policy.json`. The parameters offered are the lane's own — the list `lane_contract`
    # publishes, which is the list this app's own form fields were checked against — so a
    # parameter the route drops is never offered a default.

    def defaults_repo(spelling: object) -> str:
        """The catalogue repository for one model name, or a refusal that names it."""
        wanted = str(spelling or '').strip()
        repo = manager.repository_for(wanted)
        if repo is None:
            raise HTTPException(404, f'Unknown repo: {wanted}')
        return repo

    def refusals(exc: ValueError) -> dict:
        """`model_defaults.save`'s per-parameter reasons, never flattened into one message.

        The module raises ``ValueError`` whose message is the JSON object of refusals, keyed by
        parameter, so every bad value the operator typed is answered at once and by name. That
        object *is* the ``detail`` - the same shape the other Studios answer with, so one Models
        tab can read every worker - and a message that is not that object is passed through rather
        than swallowed.
        """
        try:
            reasons = json.loads(str(exc))
        except ValueError:
            return {'detail': str(exc)}
        return reasons if isinstance(reasons, dict) else {'detail': str(exc)}

    def route_refusals(values: dict) -> dict:
        """The reasons this app's own request validation refuses, by parameter.

        `model_defaults` checks a value against the bound the lane *publishes*. This route enforces
        one more thing the contract cannot publish as a bound - the shape of the language hint -
        so a proposed default is put through the route's own validator as well: a sentence typed
        into the language field would otherwise store cleanly and turn every later request that
        omitted language into a 422.
        """
        refused = {}
        for name, value in values.items():
            try:
                validate_request({'model': manager.default_package(), 'formats': ['srt'],
                                  str(name): value},
                                 packages=sorted(manager.engine.manifests),
                                 default_package=manager.default_package(),
                                 aliases=manager.model_aliases())
            except ValueError as exc:
                refused[str(name)] = str(exc)
        return refused

    def defaults_payload() -> dict:
        return model_defaults.status(manager.data, manager.registered_models())

    @app.get('/api/model-defaults')
    def get_model_defaults() -> dict:
        """Every registered model's published parameters and the defaults in force for each."""
        return defaults_payload()

    @app.put('/api/model-defaults')
    def put_model_defaults(body: dict | None = None) -> dict:
        """Store one model's defaults, refusing any value its own lane would refuse.

        Nothing is written while any value stands refused, so a 422 leaves the previous defaults
        exactly as they were instead of half-applying.
        """
        payload = body if isinstance(body, dict) else {}
        repo = defaults_repo(payload.get('repo'))
        values = payload.get('values')
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise HTTPException(422, {
                'values': ('must be an object of parameter names to values; received '
                           f'{type(values).__name__}')})
        parameters = manager.engine.lane_parameters()
        # The lane's published bound first, then the lane's own enforcement: every refusal is
        # reported at once, by parameter.
        _accepted, refusals_found = model_defaults.validate(parameters, values)
        for name, reason in route_refusals(values).items():
            refusals_found.setdefault(name, reason)
        if refusals_found:
            raise HTTPException(422, refusals_found)
        try:
            model_defaults.save(manager.data, repo, values, parameters)
        except ValueError as exc:
            raise HTTPException(422, refusals(exc)) from exc
        return defaults_payload()

    @app.delete('/api/model-defaults')
    def delete_model_defaults(repo: str = '', body: dict | None = None) -> dict:
        """Return one model to what the lane itself states, by body or query."""
        wanted = repo or str((body or {}).get('repo') or '')
        if not wanted.strip():
            raise HTTPException(400, 'repo is required')
        model_defaults.clear(manager.data, defaults_repo(wanted))
        return defaults_payload()

    # ── applying them ──────────────────────────────────────────────────────
    # A request that supplies a parameter always wins; only a field the form did not send takes
    # the value in force. The anchor is the model the request names (or this worker's declared
    # default when it names none), so the row whose defaults apply is the row the operator edited.

    def defaults_for_request(spelling: str) -> dict:
        anchor = manager.repository_for(spelling) or manager.repository_for(
            manager.default_package() or '')
        return manager.defaults_in_force(anchor) if anchor else {}

    async def with_model_defaults(http_request: Request, model: str, language: str,
                                  word_timestamps: bool,
                                  activity_id: str) -> tuple[str, str, bool, str]:
        """The form's own values, with the values in force filling the fields the form omitted.

        The map is `model_defaults.resolved`'s whole in-force map - every parameter the lane
        defaults, with the operator's stored choice over it - so a parameter the lane fixes and
        offers no control for is still carried.

        A field counts as supplied when its key is in the submitted form, so an explicit
        ``word_timestamps=false`` is still the caller's answer and a stored ``true`` cannot
        override it.
        """
        supplied = set((await http_request.form()).keys())
        in_force = defaults_for_request(model)

        def effective(name: str, value):
            return value if name in supplied else in_force.get(name, value)

        return (effective('model', model),
                effective('language', language),
                effective('word_timestamps', word_timestamps),
                effective('activity_id', activity_id))

    @app.get('/api/transcribe/availability')
    def transcribe_availability() -> dict:
        return manager.availability()

    # ── synchronous Hub transcription contract ─────────────────────────────

    @app.post('/api/transcribe')
    async def transcribe(
        http_request: Request,
        file: UploadFile = File(...),
        model: str = Form(''),
        language: str = Form(''),
        word_timestamps: bool = Form(False),
        activity_id: str = Form(''),
    ) -> dict:
        """Transcribe one upload synchronously: text + segments + SRT + VTT.

        Shape and error mapping match the retained Voice endpoint, so the Hub's
        transcription lifecycle can target either worker without a new job API.
        """
        try:
            name = safe_input_name(file.filename or '')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        data = await read_upload_limited(file, MAX_INPUT_BYTES)
        model, language, word_timestamps, activity_id = await with_model_defaults(
            http_request, model, language, word_timestamps, activity_id)
        try:
            request = validate_request(
                {'model': model or manager.default_package(),
                 'language': language or None,
                 'word_timestamps': bool(word_timestamps),
                 'formats': ['srt', 'vtt', 'text']},
                packages=sorted(manager.engine.manifests),
                default_package=manager.default_package(),
                aliases=manager.model_aliases())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        readiness = manager.readiness(request['model'])
        if not readiness['ready']:
            raise HTTPException(409, readiness['reason']
                                or f'{request["model"]} is not ready')
        source = manager.store_input(
            (activity_id.strip() or 'sync')[:32], name, data)
        job = manager.submit(request, source, activity_id=activity_id.strip() or None)
        result = await _await_job(manager, job['id'])
        if result['state'] != 'done':
            raise HTTPException(409 if result.get('error_code') in
                                ('MODEL_FILES_UNAVAILABLE', 'TRANSCRIPTION_UNAVAILABLE')
                                else 422,
                                result.get('error') or 'transcription failed')
        artifacts = result.get('artifacts') or {}
        payload = {
            'text': result.get('text'), 'language': result.get('language'),
            'duration': result.get('media_duration_seconds'),
            'model': result.get('model'),
            # The segments the engine already produced, not `None`. A Qwen3-TTS Base clone needs a
            # timestamped transcript to pick a bounded window out of a reference longer than its
            # 15-second maximum, and the app's Transcribe button is how that transcript is
            # obtained. Dropping the timestamps here is what forced the operator to paste words
            # that the engine had just measured.
            # `words` only when the engine measured them (word_timestamps on): model qualification
            # reads per-word timings through the fleet transcribe relay (0.6.147).
            'segments': [{'start': s.get('start'), 'end': s.get('end'), 'text': s.get('text'),
                          **({'words': s['words']} if s.get('words') else {})}
                         for s in (result.get('segments') or [])],
            'srt': _read(artifacts, 'srt'), 'vtt': _read(artifacts, 'vtt'),
            'elapsed_seconds': result.get('duration_seconds'),
            'studio_task_id': result['id'], 'task_id': result['id'],
            'job_id': result['id'],
            'model_revision': result.get('model_revision'),
            'package_id': result.get('package_id'),
        }
        return payload

    # ── Subtitle Studio public lifecycle ───────────────────────────────────

    @app.post('/api/generate/transcribe')
    async def submit(
        http_request: Request,
        file: UploadFile = File(...),
        model: str = Form(''),
        language: str = Form(''),
        word_timestamps: bool = Form(False),
        formats: str = Form('srt,vtt,text'),
        activity_id: str = Form(''),
    ) -> dict:
        try:
            name = safe_input_name(file.filename or '')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        data = await read_upload_limited(file, MAX_INPUT_BYTES)
        model, language, word_timestamps, activity_id = await with_model_defaults(
            http_request, model, language, word_timestamps, activity_id)
        try:
            request = validate_request(
                {'model': model or manager.default_package(),
                 'language': language or None,
                 'word_timestamps': bool(word_timestamps),
                 'formats': [item.strip() for item in formats.split(',') if item.strip()]},
                packages=sorted(manager.engine.manifests),
                default_package=manager.default_package(),
                aliases=manager.model_aliases())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        source = manager.store_input('studio', name, data)
        try:
            job = manager.submit(request, source,
                                 activity_id=activity_id.strip() or None)
        except ModelPackageError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'job': public_job(job)}

    @app.get('/api/generate/transcribe/jobs')
    def jobs() -> dict:
        return {'jobs': [public_job(job) for job in manager.list()]}

    @app.get('/api/generate/transcribe/jobs/{job_id}')
    def job(job_id: str) -> dict:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': public_job(value)}

    @app.delete('/api/generate/transcribe/jobs/{job_id}')
    def cancel(job_id: str) -> dict:
        value = manager.cancel(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': public_job(value)}

    @app.get('/api/generate/transcribe/jobs/{job_id}/artifact')
    def artifact(job_id: str, format: str = 'srt'):
        found = manager.artifact_path(job_id, format)
        if found is None:
            raise HTTPException(404, 'subtitle artifact is unavailable')
        path, media_type, suffix = found
        value = manager.get(job_id) or {}
        receipt = (value.get('artifacts') or {}).get(format) or {}
        return FileResponse(path, media_type=media_type,
                            filename=f'{job_id}.{suffix}',
                            headers={'X-Content-SHA256': str(receipt.get('sha256') or '')})

    @app.post('/api/generate/transcribe/jobs/{job_id}/ack')
    def ack(job_id: str) -> dict:
        receipt = manager.ack(job_id)
        if receipt is None:
            raise HTTPException(404, 'completed Subtitle job not found')
        return receipt

    return app


def _read(artifacts: dict, fmt: str) -> str:
    receipt = artifacts.get(fmt)
    if not receipt:
        return ''
    try:
        return Path(receipt['path']).read_text(encoding='utf-8')
    except OSError:
        return ''


async def _await_job(manager: SubtitleManager, job_id: str) -> dict:
    """Wait for one synchronous request's job to reach a terminal state."""
    import asyncio
    while True:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        if value['state'] in ('done', 'error', 'cancelled'):
            return value
        await asyncio.sleep(0.05)


def load_subtitle_manifests(manifest_dir: Path) -> list[dict]:
    """Every manifest in this bundle that *declares* the Subtitle engine.

    One ``model-manifests`` directory ships in the bundle and holds every engine's packages
    (``packaging/stage_voice.py`` copies the whole tracked tree), so a package's file name
    says nothing about which worker runs it. Selecting on the manifest's own ``engine`` is
    what lets the next subtitle package — a ``parakeet-tdt-0-6b``, a ``nemotron-asr-0-6b`` —
    reach ``/api/catalog`` with no code change. The old ``whisper-*.json`` glob made this
    worker's entire catalog "the packages whose id starts with whisper-": anything else was
    loaded by nobody, absent from the picker, and refused at job time with a message about
    Whisper models.

    A file that does not parse, or that declares another engine, is skipped: it belongs to a
    sibling worker and this one must not fail to start over it. A manifest that *does* declare
    the subtitle engine and then fails validation is fatal and names its file — dropping it
    silently would be the same invisibility this selection rule exists to remove.
    """
    import json
    from components import model_packages

    manifests: list[dict] = []
    for path in sorted(manifest_dir.glob('*.json')):
        try:
            candidate = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(candidate, dict) or candidate.get('engine') != SUBTITLE_ENGINE:
            continue
        try:
            model_packages.validate_manifest(candidate)
        except ValueError as error:
            raise ValueError(
                f'{path.name} declares the {SUBTITLE_ENGINE} engine but is invalid: {error}'
            ) from error
        manifests.append(candidate)
    if not manifests:
        raise ValueError(
            f'Subtitle model catalog is missing from the bundle: no manifest under '
            f'{manifest_dir} declares "engine": "{SUBTITLE_ENGINE}"')
    return manifests


def from_environment() -> FastAPI:
    import json
    data = Path(os.environ['STUDIOHUB_SUBTITLE_DATA_DIR'])
    library = Path(os.environ['STUDIOHUB_MODELS_DIR'])
    voice_app = Path(os.environ['STUDIOHUB_VOICE_APP_DIR'])
    manifest_dir = Path(os.environ['STUDIOHUB_SUBTITLE_MANIFESTS'])
    manifests = load_subtitle_manifests(manifest_dir)
    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())
    identity = (release.get('components', {}).get('subtitle') or {}).get('runtime_sha256')
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError('Release manifest lacks exact Subtitle runtime identity')
    return create_app(
        SubtitleManager(data, library, manifests, identity, voice_app_dir=voice_app,
                        offline=os.environ.get('HF_HUB_OFFLINE') == '1'),
        os.environ['STUDIOHUB_FLEET_TOKEN'],
        app_version=read_app_version())
