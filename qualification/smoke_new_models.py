#!/usr/bin/env python3
"""Prove the adopted models actually generate audio, not merely that their files verify.

A manifest says the bytes are right; it does not say the model loads or produces sound. This submits
one short job per model family through the Hub, waits for it, and reports what came back - the state,
the worker's own error when there is one, and whether an artifact exists.

  python3 qualification/smoke_new_models.py            # every model that fits this Mac
  python3 qualification/smoke_new_models.py --all      # include the ones this Mac cannot run

Preset-voice families take a voice from `/api/generate/availability`; clone families take a saved
voice from the library, because that is what their engines require.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request
import uuid

READY = pathlib.Path.home() / "Library/Application Support/StudioHub Standalone/hub-ready.json"

# family -> how a job for it is built
PRESET_KEY = {
    "kokoro-mlx": "kokoro_voices",
    "orpheus": "orpheus_voices",
    "vibevoice": "vibevoice_voices",
    "voxtral": "voxtral_voices",
    "voxtral-tts": "voxtral_voices",
    "marvis": "marvis_voices",
}
# Bark picks its voice from its own preset list, not a shared voice field.
BARK_PRESET_KEY = "bark_voice_presets"


def qwen_request(repo: str, availability: dict, library_voice_id: str | None) -> dict | None:
    """The parameters one Qwen3-TTS tier actually needs.

    The three tiers are three different requests: CustomVoice needs a preset speaker, VoiceDesign a
    written description, and Base a reference voice from the library. The smoke sent only a shared
    `preset_speaker`, so every Qwen row failed with a message about a missing parameter rather than
    anything about the model - which is what "13 of 25" was really reporting.
    """
    name = repo.rsplit("/", 1)[-1].lower()
    if "customvoice" in name or "custom-voice" in name:
        speakers = availability.get("qwen3_preset_speakers") or []
        return {"preset_speaker": speakers[0]["id"]} if speakers else None
    if "voicedesign" in name or "voice-design" in name:
        examples = availability.get("qwen3_voice_design_examples") or []
        return {"voice_design_prompt": examples[0]} if examples else None
    if "base" in name:
        # Base clones, so it needs a saved voice (with its transcript) rather than a speaker name.
        return {"_needs_library_voice": True} if library_voice_id else None
    return {}
# The engines this harness knows how to build a request for. It is a contract, not a menu: a
# request body is engine-shaped, so a new engine needs a recipe here. Where a list is unavoidable
# it must fail loudly and name the missing entry, and the `skipped` row below does exactly that -
# it named nothing before, so `voice-vits` (khmer-tts-mms, khmer-tts-female-onnx, both shipped)
# was skipped with no message and no row at all, and every new voice engine would follow it.
DRIVEN_ENGINES = frozenset({"voice-mlx"})
SMOKE_FILE = "qualification/smoke_new_models.py"
SKIP = {  # cannot load on a 16 GB Mac; named so the report is explicit rather than a silent omission
    "mlx-community/fish-audio-s2-pro-8bit": 24,
    "mlx-community/fish-audio-s2-pro-bf16": 32,
    "mlx-community/echo-tts-base": 24,
}
# Unique per run: the Hub answers an identical repeat of an earlier request from its own record
# (same model, same text), so a re-run would otherwise report the first attempt's failure.
TEXT_TEMPLATE = "Studio Hub smoke test, one two three. Run {}."


def call(base: str, token: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        base + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read() or b"{}")


def main() -> int:
    ready = json.loads(READY.read_text())
    base, token = ready["url"], ready["token"]
    availability = call(base, token, "/studio/voice/api/generate/availability")
    library = call(base, token, "/studio/voice/api/voices")
    voices = library.get("voices") or library.get("items") or []
    library_id = (voices[0] or {}).get("id") if voices else None

    catalogue = call(base, token, "/api/hub/catalog")
    models = catalogue.get("models", catalogue)
    # Which models this build can actually schedule. The aggregate catalogue still lists models whose
    # manifest this release no longer ships (F5-TTS), and the Hub refuses them at submission with a
    # 409 that says nothing about the model.
    packages = call(base, token, "/api/hub/model-packages").get("models") or []
    packaged = {row.get("model_id") for row in packages}
    include_all = "--all" in sys.argv
    results = []
    # Cached rows this voice smoke is not the tool for. Counted and printed rather than dropped
    # by an `engine not in (...)` test, because a row that vanishes without a word is how a whole
    # modality stops being smoke-tested.
    outside: dict[str, list[str]] = {}
    for row in models:
        repo = row.get("repo") or ""
        floor = row.get("min_unified_memory_gb")
        if not row.get("hub_cached") or not repo:
            continue
        # The row's own published modality decides whether this smoke is its subject. The
        # aggregate catalogue also carries image, music and chat rows, which the Hub refuses for
        # a voice job; a previous `engine not in (None, "voice-mlx")` test dropped them *and*
        # every non-MLX voice engine in the same silent `continue`.
        modality = row.get("hub_modality") or row.get("modality")
        if modality is not None and modality != "voice":
            outside.setdefault(str(modality), []).append(repo)
            continue
        # Only speech models. The voice catalogue also carries ASR rows.
        capabilities = list(row.get("capabilities") or [])
        if "tts" not in capabilities:
            results.append((repo, "skipped", f"capabilities {capabilities} declare no 'tts'"))
            continue
        # The engine is the row's own value, and an engine with no recipe here is a skipped row
        # naming it - never a silent `continue`.
        engine = row.get("engine") or "voice-mlx"
        if engine not in DRIVEN_ENGINES:
            results.append((repo, "skipped",
                            f"engine {engine!r} has no request recipe in {SMOKE_FILE}, so this "
                            f"model was not exercised; add one there (see the loud pattern in "
                            f"qualification/measure_section_budget.py)"))
            continue
        # A model the Hub has no package for cannot be scheduled at all, so a failed submission
        # would be about packaging rather than about the model. `execution_ready` is absent on some
        # rows (the removed F5-TTS), so the Hub's own package list is the authority.
        if repo not in packaged:
            results.append((repo, "skipped", "no packaged manifest for this build"))
            continue
        if "--only" in sys.argv:
            wanted = sys.argv[sys.argv.index("--only") + 1].split(",")
            if not any(token in repo for token in wanted):
                continue
        if repo in SKIP and not include_all:
            results.append((repo, "skipped", f"declares {SKIP[repo]} GB; this Mac has 16 GB"))
            continue
        family = row.get("family") or ""
        extra: dict = {}
        needs_library = False
        preset = ""
        if family == "qwen3-tts":
            extra = qwen_request(repo, availability, library_id)
            if extra is None:
                results.append((repo, "skipped", "no usable request for this tier on this Mac"))
                continue
            needs_library = bool(extra.get("_needs_library_voice"))
            preset = str(extra.get("preset_speaker") or "")
        elif family == "bark":
            options = availability.get(BARK_PRESET_KEY) or []
            preset = str((options[0] or {}).get("id") or "") if options else ""
        else:
            key = PRESET_KEY.get(family)
            if key is None and "voice-cloning" not in capabilities:
                # No preset key and no cloning capability: there is no request this harness can
                # honestly build, and the old code submitted an empty preset that failed for a
                # reason naming nothing. Name the family and the table to extend instead.
                results.append((repo, "skipped",
                                f"family {family!r} has no preset-voice key in PRESET_KEY "
                                f"({SMOKE_FILE}) and the model does not declare 'voice-cloning', "
                                f"so this smoke cannot build a request for it"))
                continue
            options = availability.get(key) or [] if key else []
            first = options[0] if options else None
            if isinstance(first, dict):
                preset = first.get("id") or first.get("name") or first.get("voice") or ""
            elif first:
                preset = first
        # `preset_speaker` only - the field the app's audio request carries - because that is the
        # contract under test. The picker families read `voice` internally; the worker accepts either
        # name, so a smoke that injected the internal field would hide a client-facing break.
        # The Qwen-only fields (a written description) are the ones that tier genuinely requires.
        params = {"language": "en", "speed": 1.0, "preset_speaker": preset,
                  **{k: v for k, v in extra.items() if not k.startswith("_")}}
        body = {
            "modality": "voice", "model": repo, "client_request_id": f"smoke-{uuid.uuid4().hex[:12]}",
            "items": [{"text": TEXT_TEMPLATE.format(uuid.uuid4().hex[:6]), "params": params}],
        }
        if not preset or needs_library:
            if not library_id:
                results.append((repo, "skipped", "needs a reference voice and the library is empty"))
                continue
            body["voice_library_id"] = library_id
        try:
            submitted = call(base, token, "/api/hub/jobs", body)
        except Exception as exc:
            results.append((repo, "submit failed", str(exc)[:120]))
            continue
        batch = submitted.get("batch_id") or submitted.get("id")
        state, error, artifact = "unknown", "", False
        deadline = time.time() + 420
        while time.time() < deadline:
            time.sleep(10)
            try:
                detail = call(base, token, f"/api/hub/jobs/{batch}")
            except Exception as exc:
                error = f"poll failed: {exc}"
                break
            item = (detail.get("items") or [{}])[0]
            state, error = item.get("state") or "unknown", str(item.get("error") or "")
            # A dispatched job that fails leaves the *item* queued and the reason in the batch's
            # governor note, so reporting the item state alone said "queued" for a model that had
            # in fact run and failed. The note is the truth here.
            note = str(detail.get("governor_note") or "")
            if note and note not in error:
                error = f"{error} {note}".strip()
            artifact = bool(item.get("artifact_url"))
            if state in ("done", "error", "cancelled"):
                break
        verdict = "PASS" if state == "done" and artifact else "FAIL"
        results.append((repo, verdict, f"state={state} artifact={artifact} {error[:160]}"))
        print(f"  {verdict:4s} {repo:46s} {results[-1][2]}", flush=True)

    print("\n=== summary ===")
    for repo, verdict, note in results:
        print(f"  {verdict:12s} {repo:46s} {note[:110]}")
    if outside:
        # Not this smoke's subject, but said out loud: these rows were seen, and the tool that
        # owns them is named, so nothing is dropped without a word.
        print("\n=== cached rows this voice smoke does not cover ===")
        for modality, repos in sorted(outside.items()):
            print(f"  {modality:14s} {len(repos):2d} row(s)  "
                  f"qualification/evidence/measure_machine.py measures these")
    passed = sum(1 for _r, v, _n in results if v == "PASS")
    print(f"\npassed {passed} of {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
