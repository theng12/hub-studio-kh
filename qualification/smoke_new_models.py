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
PICKER_FAMILIES = {"kokoro-mlx", "orpheus", "vibevoice", "voxtral-tts", "marvis"}
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
    include_all = "--all" in sys.argv
    results = []
    for row in models:
        repo = row.get("repo") or ""
        floor = row.get("min_unified_memory_gb")
        if not row.get("hub_cached") or not repo:
            continue
        if row.get("engine") not in (None, "voice-mlx"):
            continue
        if "--only" in sys.argv:
            wanted = sys.argv[sys.argv.index("--only") + 1].split(",")
            if not any(token in repo for token in wanted):
                continue
        if repo in SKIP and not include_all:
            results.append((repo, "skipped", f"declares {SKIP[repo]} GB; this Mac has 16 GB"))
            continue
        family = row.get("family") or ""
        preset = ""
        key = PRESET_KEY.get(family)
        if key:
            options = availability.get(key) or []
            first = options[0] if options else None
            if isinstance(first, dict):
                preset = first.get("id") or first.get("name") or first.get("voice") or ""
            else:
                preset = first or ""
        # `preset_speaker` only - the field the app's audio request carries - because that is the
        # contract under test. The picker families read `voice` internally; the worker now accepts
        # either name, so a smoke that injected the internal field would hide a client-facing break.
        params = {"language": "en", "speed": 1.0, "preset_speaker": preset}
        body = {
            "modality": "voice", "model": repo, "client_request_id": f"smoke-{uuid.uuid4().hex[:12]}",
            "items": [{"text": TEXT_TEMPLATE.format(uuid.uuid4().hex[:6]), "params": params}],
        }
        if not preset:
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
            artifact = bool(item.get("artifact_url"))
            if state in ("done", "error", "cancelled"):
                break
        verdict = "PASS" if state == "done" and artifact else "FAIL"
        results.append((repo, verdict, f"state={state} artifact={artifact} {error[:160]}"))
        print(f"  {verdict:4s} {repo:46s} {results[-1][2]}", flush=True)

    print("\n=== summary ===")
    for repo, verdict, note in results:
        print(f"  {verdict:12s} {repo:46s} {note[:110]}")
    passed = sum(1 for _r, v, _n in results if v == "PASS")
    print(f"\npassed {passed} of {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
