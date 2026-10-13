"""Isolated lifecycle checks for the packaged model downloader."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_PROOF = r'''
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path
root = Path(tempfile.mkdtemp(prefix="hub-download-proof-")); data=root/"data"; library=root/"models"
data.mkdir(); library.mkdir(); os.environ["STUDIOHUB_DATA_DIR"]=str(data)
from components.hub import model_downloads
from components import model_packages
payload=b"x"; manifest={"schema_version":1,"package_id":"fixture","model_id":"fixture/model","revision":"a"*40,"engine":"voice-mlx","minimum_macos":"0.0","files":[{"path":"model.bin","size":1,"sha256":hashlib.sha256(payload).hexdigest(),"urls":["https://10.255.255.1/model"]}]}
# A persisted prior run receives a bounded owner-loss grace, then becomes
# retryable rather than forever blocking this package.
state={"runs":{"old":{"id":"old","package_id":"fixture","state":"running","started_at":0,"pid":999}}}
model_downloads._write(state)
assert model_downloads.reconcile()["runs"]["old"]["state"] == "recovery_pending"
state=model_downloads._read(); state["runs"]["old"]["recovery_pending_at"]=0; model_downloads._write(state)
assert model_downloads.reconcile()["runs"]["old"]["state"] == "interrupted"
# Exercise the actual isolated CLI with an already verified package and a
# non-empty sources map.  sources.map must not be mistaken for a manifest.
source=root/"source"; source.mkdir(); (source/"model.bin").write_bytes(payload)
model_packages.import_folder(manifest, source, library)
run=model_downloads.start(package_id="fixture", manifest=manifest, catalog={"fixture":manifest}, library=library, sources={})
for _ in range(80):
    completed=model_downloads.status(run["id"])
    if completed["state"] != "running": break
    time.sleep(.05)
assert completed["state"] == "done"
assert (data/"model-download-runs"/run["id"]/"sources.map").is_file()
# A different, incomplete package stays cancellable and is synchronously reaped.
cancel_manifest={**manifest,"package_id":"fixture-cancel","model_id":"fixture/cancel","revision":"b"*40}
run=model_downloads.start(package_id="fixture-cancel", manifest=cancel_manifest, catalog={"fixture-cancel":cancel_manifest}, library=library, sources=None)
# Listed whoever started it (the app, a fleet install), newest first, and the list is what the app
# adopts to show and cancel a run it did not start.
listed=model_downloads.list_runs()
assert listed[0]["id"] == run["id"] and listed[0]["state"] == "running" and "staging" not in listed[0], listed[0]
stage=Path(model_downloads._read()["runs"][run["id"]]["staging"]); stage.mkdir(parents=True, exist_ok=True)
(stage/"partial.part").write_bytes(b"x")
cancelled=model_downloads.cancel(run["id"])
assert cancelled["state"] == "cancelled" and run["id"] not in model_downloads._processes
# Cancel stops the downloader only: partial bytes stay in staging for the next try to resume.
assert (stage/"partial.part").read_bytes() == b"x"
assert next(r for r in model_downloads.list_runs() if r["id"] == run["id"])["state"] == "cancelled"
# A wrapper that exits on TERM must not leave its stubborn downloader orphaned.
pidfile=root/"owned-child.pid"
owned_script="import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(60)"
parent_script="import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); time.sleep(.2); Path(sys.argv[1]).write_text(str(child.pid)); child.wait()"
owner=subprocess.Popen([sys.executable,"-c",parent_script,str(pidfile),owned_script],start_new_session=True)
for _ in range(40):
    if pidfile.exists(): break
    time.sleep(.05)
assert pidfile.exists()
child_pid=int(pidfile.read_text())
model_downloads._stop_owned("synthetic",owner)
for _ in range(40):
    state=subprocess.run(["ps","-p",str(child_pid),"-o","stat="],capture_output=True,text=True).stdout.strip()
    if not state or state.startswith("Z"): break
    time.sleep(.05)
assert not state or state.startswith("Z"), "owned downloader survived cancellation"
# A fresh owned child is synchronously reaped at Hub shutdown.
shutdown_manifest={**manifest,"package_id":"fixture-shutdown","model_id":"fixture/shutdown","revision":"c"*40}
second=model_downloads.start(package_id="fixture-shutdown", manifest=shutdown_manifest, catalog={"fixture-shutdown":shutdown_manifest}, library=library, sources=None)
# A run that already finished (its child exited 0, nobody asked since) stays done through an update's stop.
other=root/"models2"; other.mkdir(); model_packages.import_folder(manifest, source, other)
finished=model_downloads.start(package_id="fixture", manifest=manifest, catalog={"fixture":manifest}, library=other, sources={})
model_downloads._processes[finished["id"]].wait(timeout=10)
model_downloads.shutdown()
assert model_downloads.status(second["id"])["state"] == "interrupted"
assert model_downloads.status(finished["id"])["state"] == "done", model_downloads.status(finished["id"])
# Kill the actual owner only after its watcher and uncooperative child are
# ready. The watcher must run its five-second TERM/KILL branch on owner loss.
watcherfile=root/"watcher.pid"; readyfile=root/"watcher-child.pid"
stubborn="import os,signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
owner_script="import os,subprocess,sys,time; from pathlib import Path; watcher=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(os.getpid()),sys.executable,'-c',sys.argv[3],sys.argv[4]],start_new_session=True); Path(sys.argv[1]).write_text(str(watcher.pid)); time.sleep(60)"
owner=subprocess.Popen([sys.executable,"-c",owner_script,str(watcherfile),model_downloads._PARENT_WATCH,stubborn,str(readyfile)])
watcher_pid=child_pid=None
def alive(pid):
    state=subprocess.run(["ps","-p",str(pid),"-o","stat="],capture_output=True,text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")
try:
    for _ in range(40):
        if watcherfile.exists() and readyfile.exists(): break
        time.sleep(.05)
    assert watcherfile.exists() and readyfile.exists()
    watcher_pid=int(watcherfile.read_text()); child_pid=int(readyfile.read_text())
    assert alive(watcher_pid) and alive(child_pid)
    started=time.time(); owner.kill(); owner.wait(timeout=3)
    while time.time()-started < 7 and (alive(watcher_pid) or alive(child_pid)):
        time.sleep(.05)
    assert not alive(watcher_pid) and not alive(child_pid)
    assert 5 <= time.time()-started < 7
finally:
    if owner.poll() is None: owner.kill(); owner.wait()
    for pid in (watcher_pid,child_pid):
        if pid and alive(pid): os.kill(pid,signal.SIGKILL)
'''


class ModelDownloadLifecycleTests(unittest.TestCase):
    def test_peer_only_does_not_relabel_or_duplicate_an_existing_internet_run(self) -> None:
        from components.hub import model_downloads

        with tempfile.TemporaryDirectory(prefix="hub-download-reuse-") as tmp, \
                mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": tmp}):
            existing = {"id": "accepted", "package_id": "fixture", "state": "running",
                        "source": "internet", "peer_only": False, "staging": str(Path(tmp) / "stage")}
            model_downloads._write({"runs": {"accepted": existing}})
            process = mock.Mock()
            process.poll.return_value = None
            model_downloads._processes["accepted"] = process
            self.addCleanup(model_downloads._processes.pop, "accepted", None)
            with self.assertRaisesRegex(ValueError, "already has an accepted download without a LAN-only policy"):
                model_downloads.start(
                    package_id="fixture", manifest={"files": []}, catalog={}, library=Path(tmp) / "models",
                    sources=None, peer="http://127.0.0.1/share/token/files", peer_only=True)
            saved = model_downloads._read()["runs"]
            self.assertEqual(list(saved), ["accepted"])
            self.assertEqual((saved["accepted"]["id"], saved["accepted"]["peer_only"]),
                             ("accepted", False))
            process.poll.assert_called()

    def test_crash_recovery_retry_and_owned_shutdown(self) -> None:
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run([sys.executable, "-B", "-c", _PROOF], text=True,
                                capture_output=True, env=env, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
