#!/usr/bin/env python3
"""Fan a Hub app release out to every site at once, and never wait on one of them.

The owner, 2026-10-26: *"it shouldn't block the rest. we can just keep updating or installing ...
we can just fan it out."* That supersedes the paced, one-site-at-a-time driver
(`rollout-hub-0202.py`), whose rule was that a busy or silent Mac halted the whole rollout.

What is unchanged is the one thing that protects running work:

  * **No publish into a site that is working.** A site is publishable only when it is reachable,
    its own `running_jobs` is empty, and this Gateway has no non-terminal job routed to it
    (`provider_id = studio-hub-<controller>`). A queue depth of `None` with nothing running is
    idle, and is recorded as *queue unknown* rather than silently read as zero.
    **`--allow-busy` is the explicit exception, and it is off by default.** Publishing only
    stages the release on the controller; it starts no install. Every Mac - the controller
    included - installs from its own update tick only after its own work finishes, so the offer
    cannot interrupt a running job. The exception still requires a reachable controller and a
    readable Gateway gate, and the row says it was published while busy.
  * **The artifact is proved, not quoted.** Size and SHA-256 are recomputed from the bytes on
    disk, `hdiutil verify` must accept the image, and the image is mounted **read-only at a
    private temp mountpoint** (never under `/Volumes`, so a mounted kit SSD at
    `/Volumes/KH-STUDIO-*` is never touched); only that mountpoint is detached.

What changed:

  * **Every site runs its own independent gate.** A busy site is re-polled on its own until a
    bounded window (default 2 h) ends; an unreachable one is reported *deferred: unreachable* and
    retried inside the same window. Neither holds up any other site, and neither halts the run.
  * **Verification is per site and per member.** A site is *verified* when its controller holds
    this release (`version`, `build`, and `sha256`/`bytes` when published). Members are counted as
    they report; a member that is offline or still silent when the window ends is a **warning** in
    the receipt, never a failure of the rollout. Each phase of a site — the gate, the wait for the
    controller to hold the release, the member count — gets its own bounded window, so a slow gate
    cannot eat the time a member needs to report.
  * **The sites never wait on each other; the Gateway does sign one push at a time.** This tool
    gives every site its own worker, so no site's gate, transfer or member count is queued behind
    another's *wait*. The Gateway's own `release_publisher._push_lock` still admits one signed push
    at a time (`gateway/release_publisher.py`), so two POSTs that arrive together are pushed one
    after the other there; that is the Gateway's property, not a serialisation this tool adds, and
    it is recorded in the receipt's `concurrency` line rather than hidden.
  * **One accepted POST per site, and reruns never republish.** Each accepted publication writes a
    receipt beside `--evidence` (opened `x`, so it cannot be overwritten). A rerun that finds a
    site already holding the release, or a receipt for this same artifact, skips the POST.
    `--evidence` itself refuses to overwrite; reuse is a refusal, not a silent append. A POST
    the Gateway answers `LOCATION_UNREACHABLE` accepted nothing, so that one is retried inside the
    site's window; an **accepted** POST is never repeated.
  * **This Mac's fleet generation stays paused.** The owner's pause (`local_paused_at` in the
    Gateway's roster) is read before the first POST, after each site that verifies, and at the
    end, and written nowhere: this tool has no resume path and touches no fleet setting. The
    receipt records every read and the file's source path.

Every outcome lands in one receipt: one row per site, and a table a person can read in one pass.

usage: hub_rollout.py --artifact DMG --version V --build B --evidence PATH [--only-sites A,B,C]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA = 'studiohub.hub-rollout-fanout.v1'
SITE_SCHEMA = 'studiohub.hub-rollout-fanout.site.v1'

GATEWAY_URL = os.environ.get('KH_GATEWAY_URL', 'http://127.0.0.1:8787')
GATEWAY_ENV = Path(os.path.expanduser('~/Library/Application Support/gateway-kh/.env'))
GATEWAY_DB = Path(os.path.expanduser(
    '~/Library/Application Support/gateway-kh/data/gateway.sqlite3'))
GATEWAY_ROSTER = Path(os.path.expanduser(
    '~/Library/Application Support/gateway-kh/data/fleet.json'))

#: A job in one of these states is history, not work: the same three the Gateway publishes as
#: terminal and the paced driver read.
TERMINAL_STATES = ('succeeded', 'failed', 'cancelled')
#: Bounded once per wait: how long a busy or unreachable site is re-polled before it is reported,
#: and how long a published site is watched for its controller and members. The owner's window:
#: long enough that a site merely mid-install is not called a failure.
DEFAULT_WINDOW_S = 2 * 60 * 60
POLL_S = 60
#: How long one site's POST may take. Measured 2026-10-27: a 1.42 GB full push took 36 min on a
#: ~1 MB/s link, which is past the 30 min this used to be — the tool then reported the site as
#: *deferred: unreadable* while the Gateway went on to finish the push and the controller held it.
#: It is the site's own window because the Gateway signs one push at a time: sites queued behind
#: another's transfer are waiting, not failing.
#: shortcut: a `--full` run over many slow sites serialises behind the Gateway's push lock, so
#: the last site's wait could still exceed this; raise --publish-timeout for such a run.
PUBLISH_TIMEOUT_S = 2 * 60 * 60

_print_lock = threading.Lock()


def note(message: str) -> None:
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with _print_lock:
        print(f'{stamp} {message}', flush=True)


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _as_int(value: object) -> int | None:
    """A number the fleet actually said, or None. `True` is not a count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    return None


# ---------------------------------------------------------------------------
# Artifact acceptance — the accept-artifact rig, generalised to "whatever artifact was named".
# ---------------------------------------------------------------------------

def accept_artifact(artifact: Path, version: str, build: str, run=subprocess.run) -> dict:
    """Prove the artifact before a byte of it is offered to any site.

    The mounted bundle is the thing that is checked: the DMG's own `Info.plist` and
    `release-manifest.json` must both name `--version`, and the manifest's implementation commit
    must start with `--build` when it states one. The mount is read-only, at a private temp
    directory, and only that directory is detached — a kit SSD mounted at `/Volumes/KH-STUDIO-*`
    is outside this code's reach by construction, and a mountpoint that lands under `/Volumes` is
    refused outright.
    """
    artifact = Path(artifact)
    if not artifact.is_file():
        raise SystemExit(f'no artifact at {artifact}')
    if artifact.suffix != '.dmg':
        raise SystemExit(f'artifact is not a .dmg: {artifact.name}')
    if version not in artifact.name:
        raise SystemExit(f'the artifact name does not carry {version}: {artifact.name}')

    size = artifact.stat().st_size
    digest = sha256_of(artifact)

    verify = run(['hdiutil', 'verify', str(artifact)], capture_output=True, text=True, timeout=1800)
    text = (verify.stdout or '') + (verify.stderr or '')
    if verify.returncode != 0 or 'is VALID' not in text:
        raise SystemExit(f'hdiutil verify did not accept the image (exit {verify.returncode})')

    mount = Path(tempfile.mkdtemp(prefix='hub-rollout-accept-'))
    if str(mount).startswith('/Volumes'):
        raise SystemExit(f'refusing to mount under /Volumes: {mount}')
    try:
        run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount),
             str(artifact)], check=True, capture_output=True, text=True, timeout=600)
    except Exception as exc:  # noqa: BLE001 - a mount we made is a mount we clean up
        try:
            mount.rmdir()
        except OSError:
            pass
        raise SystemExit(f'hdiutil attach failed: {exc}') from exc
    try:
        app = next((p for p in mount.iterdir() if p.suffix == '.app'), None)
        if app is None:
            raise SystemExit('the mounted image carries no .app bundle')
        info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
        if info.get('CFBundleShortVersionString') != version:
            raise SystemExit(f'the mounted app reports {info.get("CFBundleShortVersionString")!r}, '
                             f'not {version}')
        manifest_path = app / 'Contents' / 'Resources' / 'release-manifest.json'
        manifest = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('version') != version:
                raise SystemExit(f'the mounted manifest reports {manifest.get("version")!r}, '
                                 f'not {version}')
            commit = str(manifest.get('implementation_commit') or '')
            if build and commit and not commit.startswith(build):
                raise SystemExit(f'the manifest was built from {commit[:12]}, not {build}')
    finally:
        # Only this mountpoint, and only after our own attach returned.
        run(['hdiutil', 'detach', str(mount)], check=False, capture_output=True, timeout=300)
        try:
            mount.rmdir()
        except OSError:
            pass

    return {
        'artifact': str(artifact.resolve()), 'name': artifact.name,
        'sha256': digest, 'bytes': size, 'mtime_ns': artifact.stat().st_mtime_ns,
        'hdiutil_verify_exit': verify.returncode,
        'app_version': version, 'build': build,
        'manifest_version': manifest.get('version') if manifest else None,
        'mount': 'read-only, private temp mountpoint, detached by this run only',
        'accepted_at': utc_now(),
    }


def same_file_as_accepted(artifact: Path, accepted: dict) -> bool:
    """Cheap re-check before a POST: the bytes offered must be the bytes accepted."""
    stat = Path(artifact).stat()
    return stat.st_size == accepted['bytes'] and stat.st_mtime_ns == accepted['mtime_ns']


# ---------------------------------------------------------------------------
# The Gateway — one endpoint, the same one the paced driver used.
# ---------------------------------------------------------------------------

class Gateway:
    """The paired Gateway's admin API plus the read-only bits of its own state."""

    def __init__(self, base: str = GATEWAY_URL, env: Path = GATEWAY_ENV,
                 db: Path = GATEWAY_DB, roster: Path = GATEWAY_ROSTER) -> None:
        self.base = base.rstrip('/')
        self.env = Path(env)
        self.db = Path(db)
        self.roster = Path(roster)

    def _key(self) -> str:
        for line in self.env.read_text().splitlines():
            if line.startswith('GATEWAY_API_KEY='):
                return line.split('=', 1)[1].strip().strip('"\'')
        raise RuntimeError(f'no GATEWAY_API_KEY in {self.env}')

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: int = 120) -> tuple[int, object]:
        key = self._key()
        request = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + key, 'X-API-Key': key,
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                return answer.status, json.loads(answer.read() or b'null')
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()[:500].decode(errors='replace')
        except (urllib.error.URLError, TimeoutError) as exc:
            return 0, {'error': f'{type(exc).__name__}: {exc}'}

    def fleet(self, force: bool = True) -> dict:
        status, reply = self.call('GET', '/admin/api/fleet' + ('?force=true' if force else ''))
        if status != 200 or not isinstance(reply, dict):
            raise RuntimeError(f'the fleet read failed: http {status}')
        return reply

    def open_jobs(self, controller_id: str) -> int:
        """Non-terminal jobs this Gateway routed to that site's provider."""
        provider = 'studio-hub-' + controller_id
        query = (f"select count(*) from jobs where state not in "
                 f"({','.join(repr(s) for s in TERMINAL_STATES)}) "
                 f"and provider_id like '{provider}%'")
        done = subprocess.run(['sqlite3', '-readonly', f'file:{self.db}?mode=ro', query],
                              capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError('gateway jobs read failed: '
                               + (done.stderr or '').strip()[:200])
        return int(done.stdout.strip() or 0)

    def publish(self, artifact: Path, version: str, build: str,
                controller_ids: list[str], force_full: bool = False,
                timeout: int = PUBLISH_TIMEOUT_S) -> tuple[int, object]:
        """One POST. `controller_ids` names exactly the site(s) this call offers the file to.

        `force_full` asks the Gateway for the whole image even where a controller advertises the
        delta capability. A Hub older than the release can take the capability marker and still
        refuse the stage (0.6.201 refuses a delta over `MAX_DELTA_FRACTION` 0.5), then asks for a
        disk image a files-only release never had. The field is sent only when asked for, so an
        ordinary publish's body is unchanged.
        """
        payload = {'artifact_path': str(artifact), 'version': version, 'build': build,
                   'controller_ids': controller_ids}
        if force_full:
            payload['force_full'] = True
        return self.call('POST', '/admin/api/release/publish', payload,
                         timeout=timeout)

    def pause(self) -> object:
        """This Mac's owner-set generation pause, **read only**.

        There is no writer here on purpose: the owner paused fleet generation on 2026-10-26 and
        the rollout records the value; it does not own it.
        """
        return json.loads(self.roster.read_text()).get('local_paused_at')


# ---------------------------------------------------------------------------
# What the fleet said about one site.
# ---------------------------------------------------------------------------

def site_read(location: dict, gateway_open_jobs: int | None,
              gateway_error: str | None = None) -> dict:
    capacity = location.get('capacity') if isinstance(location.get('capacity'), dict) else {}
    release = location.get('release') if isinstance(location.get('release'), dict) else {}
    running = [job.get('id') for job in (location.get('running_jobs') or []) if isinstance(job, dict)]
    return {
        'reachable': location.get('reachable'),
        'running_job_ids': running,
        'queue_depth': _as_int(capacity.get('queue_depth')),
        'gateway_open_jobs': gateway_open_jobs,
        'gateway_error': gateway_error,
        'controller_release': {key: release.get(key)
                               for key in ('version', 'build', 'sha256', 'bytes')},
    }


def is_idle(read: dict) -> bool:
    """Publishable: reachable, nothing running there, and nothing routed there.

    `queue_depth is None` is *unknown*, not zero, and is accepted only with nothing running —
    and then recorded as *queue unknown* rather than disguised as a zero (owner, 2026-10-26).
    """
    return (read['reachable'] is True
            and not read['running_job_ids']
            and read['gateway_error'] is None
            and read['gateway_open_jobs'] == 0
            and read['queue_depth'] in (None, 0))


def queue_unknown(read: dict) -> bool:
    return read['queue_depth'] is None

def is_publishable_while_busy(read: dict) -> bool:
    """Reachable and no failed Gateway read: the only site checks a busy publish needs.

    Publishing stages the release on the controller; it never starts an install. Each Mac -
    the controller included - installs from its own update tick only when its own work is
    done, so a site with work running is not interrupted by the offer itself. An unreachable
    controller, or an unreadable Gateway gate, is still not publishable: those are unknowns,
    not busy.
    """
    return read['reachable'] is True and read['gateway_error'] is None




def holds_release(read: dict, version: str, build: str, sha256: str, size: int) -> bool:
    held = read['controller_release']
    if held.get('version') != version or held.get('build') != build:
        return False
    # Older Hubs publish no digest; when they do, it must be this file's.
    if held.get('sha256') is not None and held.get('sha256') != sha256:
        return False
    if held.get('bytes') is not None and held.get('bytes') != size:
        return False
    return True


def member_is_current(member: dict, version: str) -> bool:
    return (member.get('reachable') is True and member.get('hub_version') == version
            and str(member.get('health') or '').lower() in ('up', 'ready', ''))


# ---------------------------------------------------------------------------
# The rollout.
# ---------------------------------------------------------------------------

class Rollout:
    """One bounded, independent worker per site. Nothing here waits on another site's outcome."""

    def __init__(self, gateway, accepted: dict, artifact: Path, version: str, build: str,
                 receipts: Path, *, window_s: float = DEFAULT_WINDOW_S, poll_s: float = POLL_S,
                 now=time.monotonic, sleep=time.sleep, force_full: bool = False,
                 allow_busy: bool = False,
                 publish_timeout_s: float = PUBLISH_TIMEOUT_S) -> None:
        self.gateway = gateway
        self.accepted = accepted
        self.artifact = Path(artifact)
        self.version = version
        self.build = build
        self.receipts = Path(receipts)
        self.force_full = force_full
        self.allow_busy = allow_busy
        self.window_s = window_s
        self.poll_s = poll_s
        self.publish_timeout_s = publish_timeout_s
        self.now = now
        self.sleep = sleep
        self.pause_reads: list[dict] = []

    # -- receipts ---------------------------------------------------------
    def receipt_path(self, site_id: str) -> Path:
        return self.receipts / f'{site_id}-{self.version}.json'

    def read_receipt(self, site_id: str) -> dict | None:
        path = self.receipt_path(site_id)
        if not path.is_file():
            return None
        receipt = json.loads(path.read_text())
        if (receipt.get('sha256') != self.accepted['sha256']
                or receipt.get('version') != self.version
                or receipt.get('build') != self.build):
            raise SystemExit(
                f'{path} is a publication receipt for a different artifact '
                f'({receipt.get("version")}/{receipt.get("build")}/{str(receipt.get("sha256"))[:12]}); '
                'it is refused rather than reused. Move it out (to _trash/) before publishing '
                'a different build.')
        return receipt

    def write_receipt(self, site_id: str, outcome: dict) -> None:
        path = self.receipt_path(site_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'schema': SITE_SCHEMA, 'site': site_id, 'version': self.version,
                  'build': self.build, 'artifact': str(self.artifact),
                  'sha256': self.accepted['sha256'], 'bytes': self.accepted['bytes'],
                  'published_at': utc_now(), **outcome}
        # 'x': a receipt is written once. Never overwritten, never appended.
        with path.open('x') as handle:
            handle.write(json.dumps(record, indent=2) + '\n')

    # -- pause ------------------------------------------------------------
    def read_pause(self, where: str) -> object:
        try:
            value = self.gateway.pause()
        except Exception as exc:  # noqa: BLE001 - an unreadable pause is recorded, not fatal
            value = f'unreadable: {type(exc).__name__}: {exc}'
        self.pause_reads.append({'at': utc_now(), 'where': where, 'value': value})
        return value

    # -- reads ------------------------------------------------------------
    def _location(self, site_id: str, force: bool) -> dict:
        fleet = self.gateway.fleet(force=force)
        for location in fleet.get('locations') or []:
            if location.get('id') == site_id:
                return location
        raise RuntimeError('the fleet read does not carry this site')

    def read_site(self, site_id: str, force: bool = True) -> dict:
        location = self._location(site_id, force)
        try:
            open_jobs, error = self.gateway.open_jobs(site_id), None
        except Exception as exc:  # noqa: BLE001 - an unreadable gate is not a zero
            open_jobs, error = None, f'{type(exc).__name__}: {exc}'
        return site_read(location, open_jobs, error)

    def members_of(self, site_id: str, force: bool = False) -> list[dict]:
        fleet = self.gateway.fleet(force=force)
        return [m for m in fleet.get('machines') or [] if m.get('location_id') == site_id]

    def _try_read(self, site_id: str) -> dict | None:
        """A read that failed is this site's wait, not this run's end."""
        try:
            return self.read_site(site_id)
        except Exception as exc:  # noqa: BLE001 - recorded on the row, not raised
            note(f'{site_id}: the fleet read failed ({type(exc).__name__}: {exc}); retrying')
            return None

    def _try_members(self, site_id: str) -> list[dict]:
        try:
            return self.members_of(site_id)
        except Exception:  # noqa: BLE001 - an unreadable roster is a poll with no members
            return []

    # -- per-site ---------------------------------------------------------
    def run_site(self, site: dict) -> dict:
        """One site's whole life. A failure here is that site's report, never the run's end."""
        started = self.now()
        try:
            row = self._run_site(site)
        except SystemExit:
            raise  # a refusal is the operator's to see (a receipt for another artifact, say)
        except Exception as exc:  # noqa: BLE001 - one site's fault is one site's row
            row = {'site': site['id'], 'label': site.get('label') or site['id'],
                   'state': 'deferred: unreadable', 'published_at': None, 'post_count': 0,
                   'members_updated': 0, 'members_total': 0,
                   'warnings': [f'{type(exc).__name__}: {exc}'], 'attempts': []}
            note(f'{site["id"]}: deferred: unreadable ({type(exc).__name__}: {exc})')
        row['elapsed_s'] = round(self.now() - started, 1)
        return row

    def _run_site(self, site: dict) -> dict:
        site_id, label = site['id'], site.get('label') or site['id']
        row = {'site': site_id, 'label': label, 'state': 'pending', 'published_at': None,
               'post_count': 0, 'members_updated': 0, 'members_total': 0, 'warnings': [],
               'attempts': []}
        started = self.now()

        first = self._try_read(site_id)

        if first is not None and holds_release(first, self.version, self.build,
                                               self.accepted['sha256'],
                                               self.accepted['bytes']):
            row['published_at'] = 'already held before this run'
            note(f'{label}: already holds {self.version}; not republishing')
            return self._verify(site_id, label, row)

        gate_deadline = started + self.window_s
        read = first
        while True:
            if read is None:
                row['state'] = 'deferred: unreadable'
                if self.now() >= gate_deadline:
                    row['warnings'].append('the fleet never answered for this site inside the '
                                           'window; never published')
                    return row
                self.sleep(self.poll_s)
                read = self._try_read(site_id)
                continue
            row['gate'] = read
            if read['reachable'] is not True:
                row['state'] = 'deferred: unreachable'
                if self.now() >= gate_deadline:
                    row['warnings'].append('unreachable for the whole window; never published')
                    note(f'{label}: deferred: unreachable (window ended)')
                    return row
                note(f'{label}: deferred: unreachable; retrying in {self.poll_s:g}s')
                self.sleep(self.poll_s)
                read = self._try_read(site_id)
                continue
            busy_ok = self.allow_busy and is_publishable_while_busy(read)
            if not is_idle(read) and not busy_ok:
                why = (f"gateway jobs {read['gateway_open_jobs']}, "
                       f"running {read['running_job_ids'] or 'none'}, "
                       f"queue {read['queue_depth']}")
                row['state'] = 'busy'
                if self.now() >= gate_deadline:
                    row['warnings'].append(f'still busy when the window ended ({why}); '
                                           'never published into running work')
                    note(f'{label}: busy at the window end; not published')
                    return row
                note(f'{label}: busy ({why}); re-polling on its own')
                self.sleep(self.poll_s)
                read = self._try_read(site_id)
                continue
            if busy_ok and not is_idle(read):
                row['warnings'].append(
                    'published while the site was busy (--allow-busy): the offer only stages '
                    'the release, and each Mac installs when its own work finishes')
            if queue_unknown(read):
                row['warnings'].append('queue unknown (the site published no queue depth); '
                                       'nothing was running, so it was published to')
            if not same_file_as_accepted(self.artifact, self.accepted):
                raise SystemExit(f'{self.artifact} changed since it was accepted; refusing to '
                                 'offer different bytes to {label}')
            published = self._publish(site_id, label, row)
            if published in ('refused', 'failed'):
                return row
            if published == 'unreachable':
                if self.now() >= gate_deadline:
                    row['state'] = 'deferred: unreachable'
                    row['warnings'].append('the controller could not be reached for the POST; '
                                           'nothing was accepted')
                    note(f'{label}: deferred: unreachable (POST refused, window ended)')
                    return row
                self.sleep(self.poll_s)
                read = self._try_read(site_id)
                continue
            return self._verify(site_id, label, row)

    def _publish(self, site_id: str, label: str, row: dict) -> str:
        receipt = self.read_receipt(site_id)
        if receipt is not None:
            row['published_at'] = receipt.get('published_at')
            row['warnings'].append('an accepted publication receipt already existed; '
                                   'the POST was not repeated')
            note(f'{label}: receipt from an earlier run; not reposting')
            return 'reused'
        row['post_count'] += 1
        status, reply = self.gateway.publish(self.artifact, self.version, self.build, [site_id],
                                             force_full=self.force_full,
                                             timeout=int(self.publish_timeout_s))
        locations = reply.get('locations') if isinstance(reply, dict) else None
        outcome = (locations or {}).get(site_id) if isinstance(locations, dict) else None
        if not isinstance(outcome, dict):
            # The POST did not return an outcome directly. Check if the controller actually
            # stored the release (e.g. Gateway finished the push but the client timed out
            # or the connection dropped).
            read = self._try_read(site_id)
            if read is not None and holds_release(read, self.version, self.build,
                                                  self.accepted['sha256'],
                                                  self.accepted['bytes']):
                row['published_at'] = utc_now()
                self.write_receipt(site_id, {'outcome': 'accepted', 'code': 'RECOVERED_HELD',
                                             'release': read['controller_release'], 'gate': row.get('gate')})
                note(f'{label}: publish returned {status}, but controller already holds {self.version}')
                self.read_pause(f'after the POST to {label}')
                return 'accepted'

            row['attempts'].append({'at': utc_now(), 'http': status,
                                    'reply': str(reply)[:300]})
            row['state'] = 'publish failed'
            err_detail = (reply or {}).get('error') if isinstance(reply, dict) else None
            # A timed-out POST does not say the Gateway stopped: it keeps pushing after the
            # caller leaves. A rerun after that finishes finds the release held and verifies.
            warning = (f'the Gateway publish timed out or dropped ({err_detail}); '
                       f'it may still be pushing, so rerun after it settles'
                       if err_detail else f'the Gateway answered http {status} without an outcome')
            row['warnings'].append(warning)
            note(f'{label}: {warning}')
            return 'failed'
        release = outcome.get('release') if isinstance(outcome.get('release'), dict) else {}
        row['attempts'].append({'at': utc_now(), 'http': status, 'ok': outcome.get('ok'),
                                'code': outcome.get('code'),
                                'release': {k: release.get(k)
                                            for k in ('version', 'build', 'sha256', 'bytes')}})
        if outcome.get('ok') is True:
            expected = {'version': self.version, 'build': self.build,
                        'sha256': self.accepted['sha256'], 'bytes': self.accepted['bytes']}
            drifted = [key for key in expected
                       if release.get(key) is not None and release.get(key) != expected[key]]
            if drifted:
                row['warnings'].append(
                    'the controller read back a different release ('
                    + ', '.join(f'{key}={release.get(key)!r} not {expected[key]!r}'
                                for key in drifted) + '); verification decides')
                note(f'{label}: published, but the release read back has drifted')
            row['published_at'] = utc_now()
            self.write_receipt(site_id, {'outcome': 'accepted', 'code': outcome.get('code'),
                                         'release': release, 'gate': row.get('gate')})
            note(f'{label}: publication accepted')
            self.read_pause(f'after the POST to {label}')
            return 'accepted'
        if outcome.get('code') == 'LOCATION_UNREACHABLE' and not outcome.get('release'):
            note(f'{label}: the controller could not be reached; nothing accepted')
            return 'unreachable'
        row['state'] = f'refused: {outcome.get("code")}'
        row['warnings'].append(f'the Gateway refused the publication: '
                              f'{outcome.get("code")} {outcome.get("detail") or ""}'.strip())
        note(f'{label}: refused: {outcome.get("code")}')
        return 'refused'

    def _verify(self, site_id: str, label: str, row: dict) -> dict:
        """The controller must hold the release; members are counted as they report.

        Each phase gets its own bounded window: waiting for the controller to report the release,
        and then waiting for its members. Neither the long gate nor a slow member eats the other's
        time, and neither can hold up another site.
        """
        deadline = self.now() + self.window_s
        while True:
            read = self._try_read(site_id)
            if read is not None:
                row['controller_release'] = read['controller_release']
            if read is not None and holds_release(read, self.version, self.build,
                             self.accepted['sha256'], self.accepted['bytes']):
                break
            if self.now() >= deadline:
                row['state'] = 'published, not verified'
                row['warnings'].append('the controller did not report holding this release '
                                       'within the window')
                note(f'{label}: published, but the controller has not reported {self.version}')
                return row
            self.sleep(self.poll_s)

        deadline = self.now() + self.window_s
        # Every member the site lists counts towards the total, reachable or not: a Mac that is
        # off is a warning against the site's own roster, never quietly left out of the count.
        required: set[str] = {m['machine'] for m in self._try_members(site_id)
                              if m.get('machine')}
        seen: set[str] = set()
        while True:
            members = self._try_members(site_id)
            for member in members:
                if member.get('reachable') is True and member.get('machine'):
                    required.add(member['machine'])
                if member_is_current(member, self.version) and member.get('machine'):
                    seen.add(member['machine'])
            pending = sorted(required - seen)
            if not pending:
                break
            if self.now() >= deadline:
                offline = sorted(m for m in pending
                                 if not any(x.get('machine') == m and x.get('reachable') is True
                                            for x in members))
                silent = sorted(set(pending) - set(offline))
                if offline:
                    row['warnings'].append('still offline at the window end: '
                                           + ', '.join(offline))
                if silent:
                    row['warnings'].append('reached but silent about the new version: '
                                           + ', '.join(silent))
                note(f'{label}: {len(required) - len(pending)}/{len(required)} members on '
                     f'{self.version}; warning only')
                break
            note(f'{label}: {len(required) - len(pending)}/{len(required)} members on '
                 f'{self.version}; pending {", ".join(pending)}')
            self.sleep(self.poll_s)

        row['members_total'] = len(required)
        row['members_updated'] = len(seen)
        row['state'] = 'verified'
        note(f'{label}: verified ({len(seen)}/{len(required)} members on {self.version})')
        self.read_pause(f'after {label} verified')
        return row

    def run(self, sites: list[dict]) -> list[dict]:
        self.read_pause('before the first POST')
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(sites))) as pool:
            rows = list(pool.map(self.run_site, sites))
        self.read_pause('final readback')
        return rows


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

def render_table(rows: list[dict]) -> str:
    header = ('site', 'label', 'publish time', 'state', 'members', 'warnings')
    body = []
    for row in rows:
        members = (f'{row.get("members_updated", 0)}/{row.get("members_total", 0)}'
                   if row.get('members_total') else '-')
        body.append((str(row.get('site')), str(row.get('label') or ''),
                     str(row.get('published_at') or '-'), str(row.get('state')), members,
                     '; '.join(row.get('warnings') or []) or '-'))
    widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header) - 1)]
    lines = ['  '.join(header[i].ljust(widths[i]) for i in range(len(widths))) + '  '
             + header[-1]]
    for row in body:
        lines.append('  '.join(row[i].ljust(widths[i]) for i in range(len(widths))) + '  '
                     + row[-1])
    return '\n'.join(lines)


def choose_sites(locations: list[dict], only: str | None) -> list[dict]:
    chosen = [{'id': loc.get('id'), 'label': loc.get('label')}
              for loc in locations if loc.get('id')]
    if not only:
        return chosen
    wanted = [part.strip() for part in only.split(',') if part.strip()]
    known = {str(s['id']) for s in chosen} | {str(s['label']) for s in chosen}
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise SystemExit(f'unknown site(s) {", ".join(unknown)}; the Gateway names '
                         + ', '.join(sorted(known)))
    out = [s for s in chosen if str(s['id']) in wanted or str(s['label']) in wanted]
    if not out:
        raise SystemExit('--only-sites matched no site')
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--artifact', required=True, help='the .dmg to offer every site')
    parser.add_argument('--version', required=True, help='the release version it carries')
    parser.add_argument('--build', required=True, help='the build identity it carries')
    parser.add_argument('--evidence', required=True,
                        help='receipt JSON to write; refused when it already exists')
    parser.add_argument('--only-sites', default=None,
                        help='comma-separated site ids or labels; default: every site')
    parser.add_argument('--full', action='store_true',
                        help='send the whole image to every site, even one that advertises the '
                             'delta capability (a Hub older than the release can refuse a delta '
                             'over half the image and then ask for a disk image a files-only '
                             'release never had)')
    parser.add_argument('--allow-busy', action='store_true',
                        help='publish to a reachable site even while it has work running; the '
                             'offer only stages the release, and each Mac installs when its own '
                             'work finishes. Default: wait for a quiet site')
    parser.add_argument('--publish-timeout', type=int, default=int(PUBLISH_TIMEOUT_S),
                        help=f'max seconds to wait for Gateway POST /admin/api/release/publish '
                             f'(default: {int(PUBLISH_TIMEOUT_S)}s / 2h)')
    args = parser.parse_args(argv)

    evidence = Path(args.evidence)
    if evidence.exists():
        raise SystemExit(f'{evidence} already exists; a receipt is never reused or overwritten')

    artifact = Path(args.artifact)
    accepted = accept_artifact(artifact, args.version, args.build)
    note(f'artifact accepted: {accepted["name"]} {accepted["bytes"]} bytes '
         f'sha256 {accepted["sha256"]}')

    gateway = Gateway()
    rollout = Rollout(gateway, accepted, artifact, args.version, args.build,
                      receipts=evidence.parent / 'hub-rollout-site-receipts',
                      force_full=args.full, allow_busy=args.allow_busy,
                      publish_timeout_s=args.publish_timeout)
    try:
        fleet = gateway.fleet(force=True)
    except Exception as exc:  # noqa: BLE001 - no roster is no publish
        raise SystemExit(f'cannot read the fleet: {exc}') from exc
    sites = choose_sites(fleet.get('locations') or [], args.only_sites)
    note('sites: ' + ', '.join(f'{s["id"]} ({s["label"]})' for s in sites))

    rows = rollout.run(sites)
    pause_reads = rollout.pause_reads
    pause_baseline = next((r['value'] for r in pause_reads
                           if r['where'] == 'before the first POST'), None)
    pause_final = pause_reads[-1]['value'] if pause_reads else None

    try:
        readback_fleet = gateway.fleet(force=True)
    except Exception as exc:  # noqa: BLE001 - the report still lands
        readback_fleet = {'error': f'{type(exc).__name__}: {exc}'}

    receipt = {
        'schema': SCHEMA, 'tool': 'scripts/hub_rollout.py',
        'generated_at': utc_now(), 'version': args.version, 'build': args.build,
        'artifact': accepted,
        # Which shape was offered: a full push is the only one an older Hub can take when its
        # delta refusal (MAX_DELTA_FRACTION) covers this release.
        'force_full': args.full,
        'allow_busy': args.allow_busy,
        'publish_timeout_s': args.publish_timeout,
        'window_s': DEFAULT_WINDOW_S, 'poll_s': POLL_S,
        'sites': rows,
        'pause': {
            'value_baseline': pause_baseline, 'value_final': pause_final,
            'changed_during_run': pause_baseline != pause_final,
            'written_by_tool': False,
            'source': str(GATEWAY_ROSTER) + ' local_paused_at (read only; no writer exists)',
            'reads': pause_reads,
        },
        'fleet_after': {
            'locations': [{'id': loc.get('id'), 'reachable': loc.get('reachable'),
                           'held_release': (loc.get('release') or {}).get('version'),
                           'paused_at': loc.get('paused_at')}
                          for loc in readback_fleet.get('locations') or []],
            'error': readback_fleet.get('error'),
        },
        'concurrency': (f'{len(sites)} site workers, one bounded window per phase each, '
                        'no shared wait; the Gateway signs one push at a time '
                        '(release_publisher._push_lock)'),
        'table': render_table(rows),
    }
    evidence.parent.mkdir(parents=True, exist_ok=True)
    with evidence.open('x') as handle:
        handle.write(json.dumps(receipt, indent=2) + '\n')

    print(render_table(rows))
    pause_note = ('unchanged' if pause_baseline == pause_final
                  else 'CHANGED during the run by something other than this tool')
    note(f'pause readback: {pause_final!r} ({pause_note}); never written')
    note(f'receipt: {evidence}')
    bad = [row for row in rows if row.get('state') != 'verified']
    if bad:
        note('sites not verified: ' + ', '.join(f'{r["site"]} ({r["state"]})' for r in bad))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
