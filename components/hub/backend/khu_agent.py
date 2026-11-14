"""Per-user bootstrap. KH Updater remains the authority for app updates."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import httpx
from . import ed25519

log = logging.getLogger("studiohub.khu_agent")
KIT_FILES = {"VERSION", "bin/khu", "bin/khu-stub", "install-agent.sh",
             "com.khupdater.agent.plist", "agent-config.example.json", "publisher.json"}
# The shared icon names the background bundles the agent installs. A kit built before the
# icon existed still installs; when present it is pinned by the manifest like every other byte.
KIT_OPTIONAL = {"AppIcon.icns"}
STATUS = {"state": "pending", "tokenRejected": False,
          "detail": "KH Updater has not been checked yet."}


def preserve(path: Path, root: Path) -> None:
    bucket = root / "_trash" / secrets.token_hex(12)
    bucket.mkdir(parents=True, mode=0o700)
    shutil.move(str(path), str(bucket / path.name))


def private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("unsafe config path")
    fd, name = tempfile.mkstemp(prefix=".khu-", dir=path.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        preserve(path, path.parent)
    os.replace(name, path)


def version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,9}){0,5}", value):
        raise ValueError("invalid agent version")
    values = tuple(map(int, value.split(".")))
    return values + (0,) * (6 - len(values))


def publisher_config(raw: dict, *, allow_test: bool = False) -> dict:
    if set(raw) != {"feedURL", "enrolURL", "publicKey", "testAuthority"}:
        raise ValueError("invalid publisher configuration")
    if type(raw["testAuthority"]) is not bool or (raw["testAuthority"] and not allow_test):
        raise ValueError("test authority cannot enrol a real Hub")
    if len(base64.b64decode(raw["publicKey"], validate=True)) != 32:
        raise ValueError("invalid public key")
    import ipaddress
    addresses = []
    # The publisher serves the feed and enrolment on ONE port (8790), not one each.
    for key, path in (("feedURL", "/release.json"), ("enrolURL", "/enrol")):
        parsed = urlsplit(raw[key])
        address = ipaddress.ip_address(parsed.hostname or "")
        # In test mode, accept loopback OR Tailscale; otherwise require Tailscale range and port 8790.
        if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path != path
                or (not allow_test and (address not in ipaddress.ip_network("100.64.0.0/10")
                                        or parsed.port != 8790))
                or (allow_test and not (address.is_loopback
                                        or address in ipaddress.ip_network("100.64.0.0/10")))):
            raise ValueError("publisher must use its configured Tailscale address on port 8790")
        addresses.append(address)
    if addresses[0] != addresses[1]:
        raise ValueError("publisher origins disagree")
    return raw


def verify_kit(kit: Path, *, allow_test: bool = False) -> dict:
    if kit.is_symlink() or not kit.is_dir():
        raise ValueError("agent kit missing")
    manifest = json.loads((kit / "MANIFEST.json").read_text())
    files = manifest["files"]
    actual = {p.relative_to(kit).as_posix() for p in kit.rglob("*") if not p.is_dir()}
    if (not KIT_FILES <= set(files) or not set(files) <= KIT_FILES | KIT_OPTIONAL
            or actual != set(files) | {"MANIFEST.json"}):
        raise ValueError("unexpected agent kit contents")
    for name, pin in files.items():
        path = kit / name
        # Only check for symlinks within the kit itself; parent directories (like /var/folders
        # on macOS) are often symlinked and that is not a kit defect.
        if path.is_symlink():
            raise ValueError("agent kit contains an alias")
        data = path.read_bytes()
        if len(data) != pin["bytes"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
            raise ValueError("agent kit checksum mismatch")
    version((kit / "VERSION").read_text().strip())
    publisher_config(json.loads((kit / "publisher.json").read_text()), allow_test=allow_test)
    return manifest


def app_config(home: Path, kit: Path) -> dict:
    apps = {"updater": {"bundlePath": "", "recipe": "updater", "automatic": False}}
    known = {"local.studiohub.standalone": ("hub", "hub"),
             "kh.gateway.menubar": ("gateway", "gateway"),
             "com.storystudio.kh": ("story", "story"),
             "com.storystudio.desktop": ("story", "story")}
    # ponytail: discover standard per-user/system Applications folders; add an explicit path
    # source if a future installation supports bundles outside those folders.
    roots = [home / "Applications", Path("/Applications")]
    if home != Path.home():
        roots = [home / "Applications"]  # isolated homes must never inventory the owner's apps
    for root in roots:
        for bundle in sorted(root.glob("*.app")):
            try:
                info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
                identity = info["CFBundleIdentifier"]
                entry = known.get(identity)
                if entry:
                    app, recipe = entry
                    apps.setdefault(app, {"bundlePath": str(bundle.resolve()), "recipe": recipe,
                                          "automatic": False, "bundleID": identity})
            except (OSError, ValueError, KeyError):
                continue
    return apps


class Bootstrap:
    def __init__(self, kit: Path, home: Path, *, allow_test: bool = False,
                 run=subprocess.run):
        self.kit, self.home, self.allow_test, self.run = kit, home, allow_test, run
        self.root = home / "Library/KHUpdater"
        self.base = self.root / "base"
        self.token_rejected = False

    def install(self) -> str:
        verify_kit(self.kit, allow_test=self.allow_test)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.base.is_symlink():
            raise ValueError("unsafe install path")
        with (self.root / "bootstrap.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.base.mkdir(exist_ok=True, mode=0o700)
            # Same lock used by the stub's rollback and the updater's signed self-update.
            with (self.base / "switch.lock").open("a+") as switch:
                fcntl.flock(switch, fcntl.LOCK_EX)
                wanted = (self.kit / "VERSION").read_text().strip()
                current = self.base / "current"
                if current.is_symlink():
                    installed = current.resolve(strict=True)
                    expected_versions = (self.base / "versions").resolve()
                    if installed.parent.parent != expected_versions or installed.name != "khu":
                        raise ValueError("unsafe current agent")
                    comparison = version(installed.parent.name)
                    wanted_v = version(wanted)
                    if comparison > wanted_v:
                        return "newer"
                    if comparison == wanted_v:
                        return "current"
                elif current.exists():
                    raise ValueError("current agent must be a version link")
                target = self.base / "versions" / wanted
                target.parent.mkdir(exist_ok=True, mode=0o700)
                if target.exists():
                    if (target / "khu").read_bytes() != (self.kit / "bin/khu").read_bytes():
                        raise ValueError("version already has different bytes")
                else:
                    stage = Path(tempfile.mkdtemp(prefix=".version-", dir=target.parent))
                    shutil.copyfile(self.kit / "bin/khu", stage / "khu")
                    (stage / "khu").chmod(0o755)
                    stage.rename(target)
                binaries = self.root / "bin"
                binaries.mkdir(exist_ok=True, mode=0o700)
                stub = binaries / "khu-stub"
                # The running supervisor is immutable. Agent upgrades only move version links.
                if not stub.exists():
                    shutil.copyfile(self.kit / "bin/khu-stub", stub)
                    stub.chmod(0o755)
                if current.is_symlink():
                    self._point("previous", os.readlink(current))
                self._point("current", f"versions/{wanted}/khu")
                return "installed"

    def _point(self, name: str, target: str) -> None:
        link = self.base / name
        fresh = self.base / (".link-" + secrets.token_hex(12))
        fresh.symlink_to(target)
        if link.is_symlink():
            bucket = self.base / "_trash" / secrets.token_hex(12)
            bucket.mkdir(parents=True, mode=0o700)
            (bucket / name).symlink_to(os.readlink(link))
        elif link.exists():
            raise ValueError("unsafe version link")
        os.replace(fresh, link)

    async def _token_rejected(self, publisher: dict, saved: dict, machine: str) -> bool:
        """True only when the publisher answers 401 for the stored token.

        The probe is `GET /commands` with NO `capabilities` parameter: it must not change the
        record it is asking about. A `POST /status` probe would refresh the publisher's
        `lastSeen` and make a Mac whose agent died look alive on the owner's page, and it would
        also rewrite what that Mac last reported.

        Anything else — 200, 404, a server error, a timeout, an unreachable host — is not
        proof of staleness, so a working config is never dropped on an unreachable publisher.
        """
        parsed = urlsplit(publisher["feedURL"])
        origin = f"{parsed.scheme}://{parsed.netloc}"
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
                response = await client.get(f"{origin}/commands",
                                            params={"machine": machine},
                                            headers={"authorization": f"Bearer {saved['token']}"})
        except httpx.HTTPError:
            return False
        return response.status_code == 401

    async def enrol(self, machine: str) -> str:
        config_file = self.base / "config.json"
        publisher = publisher_config(json.loads((self.kit / "publisher.json").read_text()),
                                     allow_test=self.allow_test)
        re_enrolling = False
        if config_file.exists():
            saved = json.loads(config_file.read_text())
            if any(saved[k] != publisher[k] for k in ("feedURL", "publicKey")) or saved["machine"] != machine:
                raise ValueError("installed agent has another authority or identity")
            if not await self._token_rejected(publisher, saved, machine):
                return "current"
            # The publisher dropped this Mac. Keep the dead config as evidence and enrol again
            # through the window-gated flow below; nothing here trusts the old token.
            preserve(config_file, self.root)
            self.token_rejected = True
            re_enrolling = True
        request_file = self.root / "enrol-request.json"
        if not request_file.exists():
            private_json(request_file, {"machine": machine, "nonce": secrets.token_hex(32)})
        request = json.loads(request_file.read_text())
        if request["machine"] != machine:
            raise ValueError("enrolment identity changed")
        # No credential travels with this request. The Hub's fleet token is this group's
        # shared secret and the publisher neither needs nor can verify it; enrolment is
        # authorised on the publisher by an owner-opened time window instead, and the
        # signed reply below is what this Mac actually trusts.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
            response = await client.post(publisher["enrolURL"], json=request)
        if response.status_code != 200 or len(response.content) > 16384:
            raise ValueError("publisher has not authorised this Mac")
        signed = response.json()
        raw = base64.b64decode(signed["reply"], validate=True)
        if not ed25519.verify(base64.b64decode(publisher["publicKey"]), raw,
                              base64.b64decode(signed["signature"], validate=True)):
            raise ValueError("publisher signature refused")
        payload = json.loads(raw)
        if (payload["machine"] != machine or payload["nonce"] != request["nonce"]
                or payload["feedURL"] != publisher["feedURL"]
                or payload["publicKey"] != publisher["publicKey"]
                or abs(time.time() - payload["issuedAt"]) > 300
                or not isinstance(payload["token"], str) or len(payload["token"]) < 32):
            raise ValueError("publisher reply refused")
        private_json(config_file, {**{k: publisher[k] for k in ("feedURL", "publicKey")},
                                  "machine": machine, "token": payload["token"],
                                  "stateDir": str(self.root / "state"), "stubBase": str(self.base),
                                  "apps": app_config(self.home, self.kit),
                                  "intervalMinutes": 5, "once": False})
        # This Mac holds a token the publisher has just accepted: a later failure (a launchd
        # problem, say) must not keep reporting a token the publisher no longer knows.
        self.token_rejected = False
        return "re-enrolled" if re_enrolling else "enrolled"

    def activate(self) -> None:
        if not (self.base / "config.json").is_file():
            raise ValueError("agent not enrolled")
        plist = self.home / "Library/LaunchAgents/com.khupdater.agent.plist"
        job = plistlib.loads((self.kit / "com.khupdater.agent.plist").read_bytes())
        job["ProgramArguments"] = [str(self.root / "bin/khu-stub"), "--base", str(self.base)]
        logfile = self.home / "Library/Logs/khupdater-agent.log"
        logfile.parent.mkdir(parents=True, exist_ok=True)
        job["StandardOutPath"] = job["StandardErrorPath"] = str(logfile)
        plist.parent.mkdir(parents=True, exist_ok=True)
        content = plistlib.dumps(job)
        if plist.exists() and plist.read_bytes() != content:
            raise ValueError("existing launch job differs; repair it explicitly")
        if not plist.exists():
            with plist.open("xb") as stream:
                stream.write(content)
            plist.chmod(0o600)
        domain = f"gui/{os.getuid()}"
        probe = self.run(["/bin/launchctl", "print", domain + "/com.khupdater.agent"],
                         capture_output=True, timeout=10)
        if probe.returncode == 0:
            return
        result = self.run(["/bin/launchctl", "bootstrap", domain, str(plist)],
                          capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError("launchd did not start KH Updater")


async def check_once(bootstrap: Bootstrap, machine: str) -> dict:
    try:
        state = await asyncio.to_thread(bootstrap.install)
        enrolment = await bootstrap.enrol(machine)
        await asyncio.to_thread(bootstrap.activate)
        if enrolment == "re-enrolled":
            answer = {"state": state, "tokenRejected": True,
                      "detail": "The publisher had dropped this Mac's token; KH Updater enrolled again."}
        else:
            answer = {"state": state, "tokenRejected": False,
                      "detail": "KH Updater enrolled; launchd job is loaded."}
    except Exception:
        # Never format an exception: HTTP and subprocess errors may carry a credential.
        log.warning("KH Updater setup incomplete; waiting for a verified kit and publisher enrolment.")
        rejected = bootstrap.token_rejected
        detail = "KH Updater setup incomplete. Check the kit, publisher authorisation and launchd job."
        if rejected:
            detail = ("The publisher no longer recognises this Mac; it enrols again when the owner "
                      "opens an enrolment window.")
        answer = {"state": "waiting", "tokenRejected": rejected, "detail": detail}
    STATUS.update(answer)
    return answer


async def loop() -> None:
    from . import control_plane
    kit = Path(__file__).resolve().parents[3] / "khu-agent"
    bootstrap = Bootstrap(kit, Path.home())
    while True:
        await check_once(bootstrap, control_plane.load_settings()["controller_id"])
        await asyncio.sleep(300)
