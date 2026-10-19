"""Preferred-hold residency: worker readers and idle-decision guards, isolated and unrun.

Deterministic subprocess proof against the REAL worker modules with fake engines/managers and
a temporary packaged layout (``<root>/hub/machine_flags.json`` beside ``<root>/chat``,
``<root>/voice`` and ``<root>/image``). No model is loaded, no network request leaves the
process, and no sleep is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import json
import os
import threading
import time
from pathlib import Path

from components import memory_modes
from components.chat.app.backend import memory_policy as chat_policy
from components.image.app.backend import memory_policy as image_policy
from components.voice.app.backend import memory_policy as voice_policy

root = Path(os.environ["STUDIOHUB_DATA_DIR"]).resolve()
for name in ("hub", "chat", "voice", "image"):
    (root / name).mkdir(parents=True, exist_ok=True)
flags_path = root / "hub" / "machine_flags.json"


def write_flags(row):
    flags_path.write_text(json.dumps({"local": row}), encoding="utf-8")


def hold(ago=0.0):
    return {"ended_wall": time.time() - ago, "boot_steady": time.monotonic() - ago}


def reservation(modality, budget=300.0, ago=0.0):
    return {"modality": modality, "owner": "tok",
            "started_wall": time.time() - ago,
            "started_steady": time.monotonic() - ago,
            "budget_s": budget}


# --- reader matrix: missing/malformed/disabled/wrong-modality read as today's modes ----------
flags_path.unlink(missing_ok=True)
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False
flags_path.write_text("{not json", encoding="utf-8")
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False
write_flags("yes")
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False

chat_work = {"enabled": True, "modality": "chat", "idle_seconds": 300}
write_flags({"preferred_work": {**chat_work, "enabled": False}, "preferred_hold": hold()})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False
write_flags({"preferred_work": {**chat_work, "modality": "voice"}, "preferred_hold": hold()})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False

# Active vs expired hold, active vs expired reservation, explicit immediate authority.
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=10)})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is True
assert memory_modes.hold_exempt(root / "chat", "chat", "auto") is True, \
    "an auto-resolved immediate worker is exempt too; only a stored immediate is not"
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=1000)})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False
write_flags({"preferred_work": chat_work,
             "preferred_reservation": reservation("chat", ago=10)})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is True
write_flags({"preferred_work": chat_work,
             "preferred_reservation": reservation("chat", ago=1000)})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=10)})
assert memory_modes.hold_exempt(root / "chat", "chat", "immediate") is False, \
    "a stored explicit immediate mode stays authoritative"

# Corrupt rows read as no exemption and never raise from the timer's reader.
for bad_policy in ({**chat_work, "enabled": 1}, {**chat_work, "enabled": "yes"}, "yes"):
    write_flags({"preferred_work": bad_policy, "preferred_hold": hold(ago=10)})
    assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False, bad_policy
for bad_hold in ("yes", {"ended_wall": "junk", "boot_steady": "junk"},
                 {"ended_wall": float("nan"), "boot_steady": float("inf")},
                 {"ended_wall": float("inf"), "boot_steady": float("nan")},
                 {"ended_wall": 10**400, "boot_steady": 10**400},
                 {"boot_steady": time.monotonic() - 10}):
    write_flags({"preferred_work": chat_work, "preferred_hold": bad_hold})
    assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False, bad_hold
write_flags({"preferred_work": chat_work, "preferred_reservation": {
    "modality": "chat", "owner": "tok", "started_wall": float("nan"),
    "started_steady": float("inf"), "budget_s": float("inf")}})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False
write_flags({"preferred_work": chat_work, "preferred_reservation": {
    "modality": "chat", "owner": "tok", "started_wall": time.time(),
    "started_steady": time.monotonic(), "budget_s": 10**400}})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is False, \
    "an over-range stored budget reads as no reservation"
write_flags({"preferred_work": chat_work, "preferred_reservation": {
    "modality": "chat", "owner": "tok", "started_wall": time.time(),
    "started_steady": time.monotonic(), "budget_s": 300.0}})
assert memory_modes.hold_exempt(root / "chat", "chat", "balanced") is True, \
    "a valid reservation still exempts"

# --- Chat idle timer: real MemoryPolicy.tick with a fake engine -----------------------------
(root / "chat" / "memory_policy.json").write_text(
    json.dumps({"mode": "balanced"}), encoding="utf-8")


class FakeEngine:
    def __init__(self, idle_s):
        self.idle_s = idle_s

    def loaded(self):
        return True

    def status(self):
        return {"busy": False}

    def last_activity_at(self):
        return time.time() - self.idle_s

    def release(self, reason="manual"):
        return {"released": True}


released = []
policy = chat_policy.MemoryPolicy(root / "chat", FakeEngine(700))
policy.release = lambda reason="manual": released.append(reason)
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=10)})
policy.tick()
assert released == [], "an active hold suppresses only the idle release"
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=1000)})
policy.tick()
assert released == ["idle"], released
released.clear()
(root / "chat" / "memory_policy.json").write_text(
    json.dumps({"mode": "immediate"}), encoding="utf-8")
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=10)})
policy.tick()
assert released == ["idle"], "stored immediate ignores the hold"

# A malformed row falls back to the ordinary timer and never raises out of tick().
released.clear()
(root / "chat" / "memory_policy.json").write_text(
    json.dumps({"mode": "balanced"}), encoding="utf-8")
write_flags({"preferred_work": chat_work, "preferred_hold": "yes"})
policy.tick()
assert released == ["idle"], "a corrupt hold reads off, so normal idle applies"
released.clear()
write_flags({"preferred_work": chat_work,
             "preferred_hold": {"ended_wall": 10**400, "boot_steady": 10**400}})
policy.tick()
assert released == ["idle"], \
    "an over-range stored stamp reads off and never kills the idle thread"

# --- Voice timer: real run_due_release with fake managers -----------------------------------
voice_policy.SETTINGS_FILE.write_text(json.dumps({"mode": "balanced"}), encoding="utf-8")
assert Path(voice_policy.SETTINGS_FILE) == root / "voice" / "memory_policy.json", \
    voice_policy.SETTINGS_FILE


class FakeGeneration:
    def has_active_jobs(self):
        return False

    def loaded_model_keys(self):
        return [["model"]]

    def last_activity_at(self):
        return time.time() - 700


class FakeTranscription:
    def is_active(self):
        return False

    def loaded_model_key(self):
        return None

    def last_activity_at(self):
        return None


voice_policy._GEN_MANAGER = FakeGeneration()
voice_policy._STT_MANAGER = FakeTranscription()
voice_policy._GPU_LOCK = threading.Lock()
voice_policy._LAST_RELEASE_AT = None
voice_released = []


def record_voice_release(reason):
    voice_released.append(reason)
    return {"released": True, "reason": reason}


voice_policy._release = record_voice_release

voice_work = {"enabled": True, "modality": "voice", "idle_seconds": 300}
write_flags({"preferred_work": voice_work, "preferred_hold": hold(ago=10)})
assert voice_policy.run_due_release() is None
assert voice_released == [], "an active hold suppresses only the idle release"
write_flags({"preferred_work": voice_work, "preferred_hold": hold(ago=1000)})
assert voice_policy.run_due_release() is not None
assert voice_released == ["automatic:balanced"], voice_released
voice_released.clear()
(root / "voice" / "memory_policy.json").write_text(
    json.dumps({"mode": "immediate"}), encoding="utf-8")
write_flags({"preferred_work": voice_work, "preferred_hold": hold(ago=10)})
voice_policy.run_due_release()
assert voice_released == ["automatic:immediate"], "stored immediate ignores the hold"

# --- Image loop decision: real _tick with a fake manager, pressure still wins ---------------
image_work = {"enabled": True, "modality": "image", "idle_seconds": 300}
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "auto"}), encoding="utf-8")


class FakeImageManager:
    data = str(root / "image")
    total_memory_bytes = 8 * 1024**3   # auto resolves to immediate on this tier

    def resident_idle_seconds(self):
        return 700.0

    def list(self):
        return []

    def resident_models(self):
        return []


image_policy._MANAGER = FakeImageManager()
image_released = []
image_policy.release_now = lambda reason, raise_if_busy=True: image_released.append(reason)
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=10)})
image_policy._pressure_is_high = lambda: False
image_policy._tick()
assert image_released == [], "an active hold suppresses only the idle release"
image_policy._pressure_is_high = lambda: True
image_policy._tick()
assert image_released == ["memory-pressure"], \
    "memory pressure is never exempted"
image_released.clear()
image_policy._pressure_is_high = lambda: False
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=1000)})
image_policy._tick()
assert image_released == ["idle"], image_released
image_released.clear()
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "immediate"}), encoding="utf-8")
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=10)})
image_policy._tick()
assert image_released == ["idle"], "stored immediate ignores the hold"

# Corrupt rows never become a fresh hold and never skip real memory pressure.
image_released.clear()
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "auto"}), encoding="utf-8")
write_flags({"preferred_work": {**image_work, "enabled": 1}, "preferred_hold": hold(ago=10)})
image_policy._pressure_is_high = lambda: False
image_policy._tick()
assert image_released == ["idle"], "a corrupt row falls back to the normal idle release"
image_released.clear()
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "performance"}), encoding="utf-8")
for bad_row in ({"preferred_work": {**image_work, "enabled": 1}, "preferred_hold": hold(ago=10)},
                {"preferred_work": image_work,
                 "preferred_hold": {"ended_wall": float("nan"),
                                    "boot_steady": float("inf")}},
                {"preferred_work": image_work,
                 "preferred_hold": {"ended_wall": 10**400, "boot_steady": 10**400},
                 "preferred_reservation": {"modality": "image", "owner": "tok",
                                           "started_wall": 10**400,
                                           "started_steady": 10**400,
                                           "budget_s": 10**400}}):
    write_flags(bad_row)
    image_policy._pressure_is_high = lambda: True
    image_policy._tick()
    assert image_released == ["memory-pressure"], bad_row
    image_released.clear()
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "auto"}), encoding="utf-8")

# --- Image finish decision: keep_resident follows the same bounded exemption ----------------
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "auto"}), encoding="utf-8")
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=10)})
assert image_policy.keep_resident(image_policy._MANAGER) is True, \
    "auto-immediate keeps the just-finished model for the bounded hold"
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=1000)})
assert image_policy.keep_resident(image_policy._MANAGER) is False
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "immediate"}), encoding="utf-8")
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=10)})
assert image_policy.keep_resident(image_policy._MANAGER) is False, \
    "stored immediate still unloads at finish"
(root / "image" / "memory_policy.json").write_text(
    json.dumps({"mode": "auto"}), encoding="utf-8")


class LargeImageManager(FakeImageManager):
    total_memory_bytes = 32 * 1024**3


assert image_policy.keep_resident(LargeImageManager()) is True, \
    "balanced/performance behavior is unchanged"

# Repeated idle ticks are reads only: a worker poll never rewrites the Hub's flags row.
(root / "chat" / "memory_policy.json").write_text(
    json.dumps({"mode": "balanced"}), encoding="utf-8")
write_flags({"preferred_work": chat_work, "preferred_hold": hold(ago=10)})
before = (flags_path.read_bytes(), flags_path.stat().st_mtime_ns)
policy.tick(); policy.tick(); policy.tick()
assert (flags_path.read_bytes(), flags_path.stat().st_mtime_ns) == before, \
    "idle ticks must not rewrite the flags row"
write_flags({"preferred_work": image_work, "preferred_hold": hold(ago=10)})
before = (flags_path.read_bytes(), flags_path.stat().st_mtime_ns)
image_policy._MANAGER = FakeImageManager()
image_policy._pressure_is_high = lambda: False
image_policy._tick(); image_policy._tick()
assert (flags_path.read_bytes(), flags_path.stat().st_mtime_ns) == before, \
    "the image idle loop must not rewrite the flags row"

print("preferred hold residency: worker reader and idle guards proved")
'''


class PreferredHoldResidencyTests(unittest.TestCase):
    def test_worker_reader_and_idle_guards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-preferred-residency-") as state:
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("STUDIOHUB_")}
            env.update(
                STUDIOHUB_DATA_DIR=state,
                PYTHONPATH=str(ROOT),
            )
            runtime = os.environ.get("STUDIOHUB_TEST_RUNTIME", sys.executable)
            result = subprocess.run(
                [runtime, "-c", PROOF], cwd=ROOT, env=env,
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("preferred hold residency: worker reader and idle guards proved",
                      result.stdout)


if __name__ == "__main__":
    unittest.main()
