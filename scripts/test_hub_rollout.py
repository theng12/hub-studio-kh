"""The fan-out rollout, proved against a Gateway that answers from a script — no socket, no fleet.

The owner, 2026-10-26: *"it shouldn't block the rest. we can just keep updating or installing ...
we can just fan it out."* The paced driver this replaces treated a busy or silent Mac as a reason
to halt every other site. The rules that stay are the ones that protect work already running:
nothing is published into a site whose own `running_jobs` is non-empty or that has non-terminal
jobs routed to it by the Gateway.

What these tests pin, one per sentence of the brief:

  * a busy site is re-polled **on its own** and never holds up, or is published to by, another;
  * `queue_depth: None` with nothing running is idle, and is reported as *queue unknown*;
  * an unreachable site is *deferred: unreachable* while the rest of the fleet carries on;
  * a member that never reports the new version is a **warning**, and its site still verifies;
  * a rerun never reposts: the receipt and the controller's own held release both stop it;
  * the owner's generation pause is read and recorded, and no code path here can write it.

The artifact acceptance is exercised with a fake `hdiutil`: the mount is read-only, the mountpoint
is private, only that mountpoint is detached, and no call is ever made against `/Volumes`.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import hub_rollout as hub  # noqa: E402

VERSION = '0.6.999'
BUILD = 'abc1234'
TARGET = {'version': VERSION, 'build': BUILD,
          'sha256': 'a' * 64, 'bytes': 1234}
WINDOW = 0.2
POLL = 0.01


class FakeSite:
    """One site's answer: reachable or not, busy or not, and what its members report."""

    def __init__(self, site_id: str, members, *, label=None, reachable=True, running=(),
                 queue=0, open_jobs=0, publish='ok', silent=(), offline=(), delay=0.0,
                 studios_down=(), studios=(4, 4), studio_labels=None):
        self.id = site_id
        self.label = label or site_id
        self.members = list(members)
        self.reachable = reachable
        self.running = list(running)
        self.queue = queue
        self.open_jobs = open_jobs
        self.publish_mode = publish
        self.silent = set(silent)
        self.offline = set(offline)
        self.delay = delay
        # A Mac whose Hub answers with the new release but whose studios are not all up: the
        # 1300/1400 shape. `studios` is the normal (up, total) count every member reports.
        self.studios_down = set(studios_down)
        self.studios = studios
        self.studio_labels = studio_labels or {}
        self.release = None
        self.published_at = None

    def member_rows(self) -> list[dict]:
        settled = (self.published_at is not None
                   and time.monotonic() - self.published_at >= self.delay)
        rows = []
        for name in self.members:
            current = settled and name not in self.silent
            if name in self.studios_down:
                up, total = 0, self.studios[1]
            else:
                up, total = self.studios
            rows.append({'machine': name, 'location_id': self.id,
                         'label': self.studio_labels.get(name, name),
                         'reachable': name not in self.offline,
                         'hub_version': TARGET['version'] if current else '0.6.998',
                         'health': 'up',
                         'startup': {'services_running': {'up': up, 'total': total,
                                                          'studios': {}}}})
        return rows


class FakeGateway:
    """`fleet`, `open_jobs`, `publish` and `pause`, answering from `FakeSite`s."""

    def __init__(self, sites, pause='2026-10-26T22:41:00+00:00', pause_reader=None,
                 identity=None):
        self.sites = {site.id: site for site in sites}
        self.posts: list[tuple[str, float]] = []
        self.full_flags: list[bool] = []
        self.timeouts: list[int | None] = []
        self.pause_value = pause
        self.pause_reader = pause_reader
        self.pause_reads = 0
        self.identity = identity

    # -- reads ---------------------------------------------------------
    def fleet(self, force: bool = True) -> dict:
        locations, machines = [], []
        for site in self.sites.values():
            locations.append({
                'id': site.id, 'label': site.label, 'reachable': site.reachable,
                'running_jobs': [{'id': job} for job in site.running],
                'capacity': {'queue_depth': site.queue}, 'release': site.release,
            })
            machines.extend(site.member_rows())
        return {'locations': locations, 'machines': machines, 'summary': {}}

    def open_jobs(self, controller_id: str) -> int:
        return self.sites[controller_id].open_jobs

    def pause(self):
        self.pause_reads += 1
        if self.pause_reader is not None:
            return self.pause_reader()
        return self.pause_value

    # -- the one write -------------------------------------------------
    def publish(self, artifact, version, build, controller_ids, force_full=False, timeout=None):
        site_id = controller_ids[0]
        self.posts.append((site_id, time.monotonic()))
        self.full_flags.append(force_full)
        self.timeouts.append(timeout)
        site = self.sites[site_id]
        if site.publish_mode == 'unreachable':
            return 200, {'locations': {site_id: {'ok': False, 'code': 'LOCATION_UNREACHABLE',
                                                 'release': None}}}
        if site.publish_mode == 'drift':
            return 200, {'locations': {site_id: {
                'ok': True, 'code': None,
                'release': {**self.identity, 'build': 'notthis'}}}}
        if site.publish_mode.startswith('refuse:'):
            code = site.publish_mode.split(':', 1)[1]
            return 200, {'locations': {site_id: {'ok': False, 'code': code,
                                                 'release': None}}}
        site.published_at = time.monotonic()
        site.release = dict(self.identity)
        return 200, {'locations': {site_id: {'ok': True, 'code': None,
                                             'release': site.release}}}


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix='hub-rollout-test-'))
        self.artifact = self.tmp / f'StudioHub-{VERSION}-{BUILD}.dmg'
        self.artifact.write_bytes(b'not a real dmg, but it is the bytes that were offered')
        self.accepted = {
            'artifact': str(self.artifact), 'name': self.artifact.name,
            'sha256': hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
            'bytes': self.artifact.stat().st_size,
            'mtime_ns': self.artifact.stat().st_mtime_ns,
            'hdiutil_verify_exit': 0, 'app_version': VERSION, 'build': BUILD,
        }
        self.identity = {'version': VERSION, 'build': BUILD,
                         'sha256': self.accepted['sha256'], 'bytes': self.accepted['bytes']}
        self.receipts = self.tmp / 'receipts'

    def gateway(self, *sites, **kwargs) -> 'FakeGateway':
        return FakeGateway(list(sites), identity=self.identity, **kwargs)

    def rollout(self, gateway, full: bool = False) -> hub.Rollout:
        return hub.Rollout(gateway, self.accepted, self.artifact, VERSION, BUILD,
                           receipts=self.receipts, window_s=WINDOW, poll_s=POLL,
                           force_full=full)

    @staticmethod
    def by_site(rows):
        return {row['site']: row for row in rows}


class IdleRuleTests(unittest.TestCase):
    def read(self, **kwargs):
        value = {'reachable': True, 'running_job_ids': [], 'queue_depth': 0,
                 'gateway_open_jobs': 0, 'gateway_error': None, 'controller_release': {}}
        value.update(kwargs)
        return value

    def test_idle_when_reachable_and_nothing_running_or_routed(self):
        self.assertTrue(hub.is_idle(self.read()))

    def test_unreachable_is_never_idle(self):
        self.assertFalse(hub.is_idle(self.read(reachable=False)))

    def test_in_flight_batch_is_never_idle(self):
        self.assertFalse(hub.is_idle(self.read(running_job_ids=['batch-1'])))

    def test_open_gateway_job_is_never_idle(self):
        self.assertFalse(hub.is_idle(self.read(gateway_open_jobs=2)))

    def test_a_declared_queue_of_work_is_never_idle(self):
        self.assertFalse(hub.is_idle(self.read(queue_depth=3)))

    def test_unknown_queue_with_nothing_running_is_idle_and_says_unknown(self):
        read = self.read(queue_depth=None)
        self.assertTrue(hub.is_idle(read))
        self.assertTrue(hub.queue_unknown(read))

    def test_an_unreadable_gateway_count_is_not_a_zero(self):
        self.assertFalse(hub.is_idle(self.read(gateway_open_jobs=None,
                                               gateway_error='RuntimeError: no db')))


class AcceptArtifactTests(unittest.TestCase):
    """The read-only mount rig, driven by a fake `hdiutil`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub-rollout-accept-test-'))
        self.dmg = self.tmp / f'StudioHub-{VERSION}-{BUILD}.dmg'
        self.dmg.write_bytes(b'image bytes')

    def runner(self, version=VERSION, commit=BUILD + 'deadbeef', fail=False):
        calls = []
        self.mounts = {}

        def run(argv, **kwargs):
            calls.append(list(argv))
            self.assertNotIn('/Volumes', ' '.join(argv))
            if argv[0] != 'hdiutil':
                raise AssertionError(f'unextected call {argv}')
            if argv[1] == 'verify':
                return SimpleNamespace(returncode=0, stdout='hdiutil: verify: ... is VALID',
                                       stderr='')
            if argv[1] == 'attach':
                self.assertIn('-readonly', argv)
                self.assertIn('-nobrowse', argv)
                mount = Path(argv[argv.index('-mountpoint') + 1])
                app = mount / 'Studio Hub.app' / 'Contents'
                (app / 'Resources').mkdir(parents=True)
                (app / 'Info.plist').write_bytes(plistlib.dumps(
                    {'CFBundleShortVersionString': '0.0.1' if fail else version}))
                (app / 'Resources' / 'release-manifest.json').write_text(json.dumps(
                    {'version': version, 'implementation_commit': commit}))
                self.mounts['/dev/disk99'] = mount
                return SimpleNamespace(returncode=0, stderr='', stdout=plistlib.dumps({'system-entities': [
                    {'dev-entry': '/dev/disk99s1', 'mount-point': str(mount)},
                    {'dev-entry': '/dev/disk99'}]}))
            if argv[1] == 'detach':
                # A real detach unmounts, leaving the (empty) mountpoint behind.
                mount = self.mounts[argv[2]]
                for child in sorted(mount.rglob('*'), reverse=True):
                    child.rmdir() if child.is_dir() else child.unlink()
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            raise AssertionError(f'unexpected hdiutil verb {argv[1]}')

        return run, calls

    def test_accepts_the_named_version_and_records_the_bytes_it_saw(self):
        run, calls = self.runner()
        accepted = hub.accept_artifact(self.dmg, VERSION, BUILD, run=run)
        self.assertEqual(accepted['sha256'],
                         hashlib.sha256(self.dmg.read_bytes()).hexdigest())
        self.assertEqual(accepted['bytes'], self.dmg.stat().st_size)
        self.assertEqual(accepted['manifest_version'], VERSION)
        attach = next(call for call in calls if call[1] == 'attach')
        detach = next(call for call in calls if call[1] == 'detach')
        mount = attach[attach.index('-mountpoint') + 1]
        # A mount-point detach ejected a real SSD on 2026-10-30.
        self.assertEqual(detach, ['hdiutil', 'detach', '/dev/disk99'],
                         'detach must name the whole device this run attached, never a path')
        self.assertFalse(Path(mount).exists(), 'the private mountpoint is removed afterwards')

    def test_refuses_a_mounted_app_that_is_not_the_named_version_and_still_detaches(self):
        run, calls = self.runner(fail=True)
        with self.assertRaises(SystemExit):
            hub.accept_artifact(self.dmg, VERSION, BUILD, run=run)
        self.assertTrue(any(call[1] == 'detach' for call in calls),
                        'a refused mount is still detached')

    def test_refuses_a_manifest_built_from_another_commit(self):
        run, _ = self.runner(commit='ffffffff')
        with self.assertRaises(SystemExit):
            hub.accept_artifact(self.dmg, VERSION, BUILD, run=run)

    def test_refuses_an_image_hdiutil_will_not_verify(self):
        def run(argv, **kwargs):
            return SimpleNamespace(returncode=1, stdout='checksum failed', stderr='')
        with self.assertRaises(SystemExit):
            hub.accept_artifact(self.dmg, VERSION, BUILD, run=run)


class FanOutTests(Harness):
    def test_a_busy_site_is_repolled_on_its_own_and_never_holds_up_the_rest(self):
        busy = FakeSite('1100', ['1100'], running=['batch-1'])
        local = FakeSite('local', ['this-mac'])
        quiet = FakeSite('1200', ['1200'])
        gateway = self.gateway(local, busy, quiet)

        rows = self.by_site(self.rollout(gateway).run(
            [{'id': 'local', 'label': 'This Mac'}, {'id': '1100', 'label': '1100'},
             {'id': '1200', 'label': '1200'}]))
        self.assertEqual(rows['1100']['state'], 'busy')
        self.assertEqual(rows['1100']['post_count'], 0,
                         'nothing is published into a site with work in flight')
        self.assertNotIn('1100', [site for site, _ in gateway.posts])
        self.assertEqual(rows['local']['state'], 'verified')
        self.assertEqual(rows['1200']['state'], 'verified')
        self.assertEqual(len(gateway.posts), 2, 'one POST per idle site')
        self.assertLess(rows['1200']['elapsed_s'], rows['1100']['elapsed_s'],
                        'the idle site finished without waiting for the busy one')

    def test_allow_busy_publishes_and_says_each_mac_installs_when_its_work_finishes(self):
        busy = FakeSite('1100', ['1100'], running=['batch-1'])
        gateway = self.gateway(busy)
        rollout = hub.Rollout(gateway, self.accepted, self.artifact, VERSION, BUILD,
                              receipts=self.receipts, window_s=WINDOW, poll_s=POLL,
                              allow_busy=True)
        row = self.by_site(rollout.run([{'id': '1100'}]))['1100']
        self.assertEqual(row['state'], 'verified')
        self.assertEqual(row['post_count'], 1)
        self.assertTrue(any('--allow-busy' in warning for warning in row['warnings']), row)

    def test_allow_busy_still_refuses_an_unreachable_or_unreadable_site(self):
        gone = FakeSite('1400', ['1400'], reachable=False)
        gateway = self.gateway(gone)
        rollout = hub.Rollout(gateway, self.accepted, self.artifact, VERSION, BUILD,
                              receipts=self.receipts, window_s=WINDOW, poll_s=POLL,
                              allow_busy=True)
        row = self.by_site(rollout.run([{'id': '1400'}]))['1400']
        self.assertEqual(row['state'], 'deferred: unreachable')
        self.assertEqual(row['post_count'], 0)

    def test_allow_busy_is_off_by_default(self):
        busy = FakeSite('1100', ['1100'], running=['batch-1'])
        gateway = self.gateway(busy)
        row = self.by_site(self.rollout(gateway).run([{'id': '1100'}]))['1100']
        self.assertEqual(row['state'], 'busy')
        self.assertEqual(row['post_count'], 0)

    def test_unknown_queue_with_nothing_running_publishes_and_is_recorded(self):
        gateway = self.gateway(FakeSite('1300', ['1300'], queue=None))
        row = self.by_site(self.rollout(gateway).run([{'id': '1300'}]))['1300']
        self.assertEqual(row['state'], 'verified')
        self.assertEqual(row['post_count'], 1)
        self.assertTrue(any('queue unknown' in warning for warning in row['warnings']), row)

    def test_an_unreachable_site_is_deferred_while_the_others_verify(self):
        gone = FakeSite('1400', ['1400'], reachable=False)
        local = FakeSite('local', ['this-mac'])
        gateway = self.gateway(local, gone)
        rows = self.by_site(self.rollout(gateway).run([{'id': 'local'}, {'id': '1400'}]))
        self.assertEqual(rows['1400']['state'], 'deferred: unreachable')
        self.assertEqual(rows['1400']['post_count'], 0)
        self.assertEqual(rows['local']['state'], 'verified')

    def test_a_site_whose_post_is_refused_is_reported_not_retried_into(self):
        site = FakeSite('1500', ['1500'], publish='unreachable')
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1500'}]))['1500']
        self.assertEqual(row['state'], 'deferred: unreachable')
        self.assertGreater(row['post_count'], 1,
                           'an unreachable POST is retried inside the window')
        self.assertIsNone(site.published_at, 'nothing was ever accepted there')

    def test_a_site_the_fleet_cannot_answer_for_is_that_site_only(self):
        """The roster losing one site mid-run must not end the fan-out or the other site."""
        site = FakeSite('1900', ['1900'])
        other = FakeSite('local', ['this-mac'])
        gateway = self.gateway(site, other)
        healthy = gateway.fleet

        def fleet(force=True):
            answer = healthy(force=force)
            answer['locations'] = [row for row in answer['locations'] if row['id'] != '1900']
            return answer

        gateway.fleet = fleet
        rows = self.by_site(self.rollout(gateway).run([{'id': 'local'}, {'id': '1900'}]))
        self.assertEqual(rows['1900']['state'], 'deferred: unreadable')
        self.assertEqual(rows['1900']['post_count'], 0)
        self.assertEqual(rows['local']['state'], 'verified')

    def test_a_release_read_back_as_another_build_is_flagged(self):
        site = FakeSite('1850', ['1850'], publish='drift')
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1850'}]))['1850']
        self.assertTrue(any('different release' in warning and 'notthis' in warning
                            for warning in row['warnings']), row)
        self.assertEqual(row['state'], 'published, not verified',
                         'a site that never reports holding this release is not verified')

    def test_a_refusal_is_a_warning_not_a_downgrade(self):
        site = FakeSite('1600', ['1600'], publish='refuse:RELEASE_DOWNGRADE')
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1600'}]))['1600']
        self.assertEqual(row['state'], 'refused: RELEASE_DOWNGRADE')
        self.assertEqual(len(gateway.posts), 1)

    def test_a_silent_member_is_a_warning_and_the_site_still_verifies(self):
        site = FakeSite('1700', ['controller', 'silent-mac'], silent=['silent-mac'])
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1700'}]))['1700']
        self.assertEqual(row['state'], 'verified')
        self.assertEqual((row['members_updated'], row['members_total']), (1, 2))
        self.assertTrue(any('silent-mac' in warning for warning in row['warnings']), row)

    def test_an_offline_member_is_a_warning_named_as_offline(self):
        site = FakeSite('1800', ['controller', 'asleep'], offline=['asleep'])
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1800'}]))['1800']
        self.assertEqual(row['state'], 'verified')
        self.assertTrue(any('offline' in warning and 'asleep' in warning
                            for warning in row['warnings']), row)


class ServicesRunningTests(unittest.TestCase):
    """A Mac counts as updated only when the new version answers AND its studios are up.

    The 2026-10-31 shape: 1300's controller answered with the release and had all seven of its
    studios down for nineteen hours. Counting that as "updated" is what let a rollout say
    success while the fleet was down.
    """

    @staticmethod
    def member(*, version='0.6.999', reachable=True, health='up', up=4, total=4, startup=...):
        row = {'machine': 'mac-1', 'label': 'Control Mac', 'reachable': reachable,
               'hub_version': version, 'health': health}
        if startup is not ...:
            row['startup'] = startup
        elif up is not None:
            row['startup'] = {'services_running': {'up': up, 'total': total, 'studios': {}}}
        return row

    def test_all_studios_up_counts_as_updated(self):
        self.assertEqual(hub.member_services_up(self.member()), True)
        self.assertTrue(hub.member_is_current(self.member(), VERSION))

    def test_a_mac_with_a_studio_down_is_not_counted_as_updated(self):
        # The exact fleet read from 1300: the release is on the Mac, and it is not serving.
        row = self.member(up=0, total=7)
        self.assertEqual(hub.member_services_up(row), False)
        self.assertFalse(hub.member_is_current(row, VERSION),
                         'a Mac whose studios are down must not count as updated')

    def test_partly_up_is_not_up(self):
        self.assertEqual(hub.member_services_up(self.member(up=6, total=7)), False)
        self.assertFalse(hub.member_is_current(self.member(up=6, total=7), VERSION))

    def test_zero_of_zero_is_unknown_never_a_pass(self):
        # A zero that means two things is a defect: no studios reported is the same shape as
        # a Hub that never answered, and neither may read as "all up".
        self.assertIsNone(hub.member_services_up(self.member(up=0, total=0)))
        self.assertFalse(hub.member_is_current(self.member(up=0, total=0), VERSION))

    def test_a_mac_that_never_said_is_unknown_not_updated(self):
        for startup in (None, {}):
            row = self.member(startup=startup)
            self.assertIsNone(hub.member_services_up(row), f'startup={startup!r}')
            self.assertFalse(hub.member_is_current(row, VERSION), f'startup={startup!r}')

    def test_a_stale_startup_is_unknown_not_updated(self):
        # The controller repeating what a Mac said before it went quiet is not a fresh answer.
        row = self.member(startup={'stale': True,
                                   'services_running': {'up': 7, 'total': 7, 'studios': {}}})
        self.assertFalse(hub.member_is_current(row, VERSION))

    def test_the_other_half_of_the_rule_still_holds(self):
        # Studios up alone is not enough: the new version must answer too.
        self.assertFalse(hub.member_is_current(self.member(version='0.6.998'), VERSION))
        self.assertFalse(hub.member_is_current(self.member(reachable=False), VERSION))
        self.assertFalse(hub.member_is_current(self.member(health='down'), VERSION))

    def test_the_reason_names_the_mac_and_the_counts(self):
        reason = hub.studios_down_reason(self.member(up=0, total=7), VERSION)
        self.assertIsNotNone(reason)
        self.assertIn('Control Mac', reason)
        self.assertIn('0 of 7 studios up', reason)

    def test_the_reason_is_none_for_a_mac_that_is_fine(self):
        self.assertIsNone(hub.studios_down_reason(self.member(), VERSION))

    def test_the_reason_is_none_for_a_mac_that_never_got_the_release(self):
        # A Mac on the old version with its studios down is a different warning, already reported.
        self.assertIsNone(hub.studios_down_reason(self.member(version='0.6.998', up=0, total=7),
                                                  VERSION))
        self.assertIsNone(hub.studios_down_reason(self.member(up=0, total=7, reachable=False),
                                                  VERSION))

    def test_a_stale_startup_reports_that_the_mac_did_not_say(self):
        reason = hub.studios_down_reason(
            self.member(startup={'stale': True,
                                 'services_running': {'up': 7, 'total': 7, 'studios': {}}}),
            VERSION)
        self.assertIsNotNone(reason, 'an unknown must be reported, never read as up')


class StudiosDownRolloutTests(Harness):
    """The receipt: a site whose member answers with the release and is not serving."""

    def test_a_member_with_studios_down_is_named_and_the_site_is_not_verified(self):
        site = FakeSite('1900', ['controller', 'worker-1'],
                        studios_down=['worker-1'], studio_labels={'worker-1': '1301 Desk'})
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1900'}]))['1900']
        self.assertEqual(row['state'], 'updated, studios down')
        self.assertTrue(row['published_at'], 'the release must still have been published')
        self.assertEqual((row['members_updated'], row['members_total']), (1, 2))
        self.assertEqual(row['studios_down'], ['1301 Desk (0 of 4 studios up)'])
        self.assertTrue(any('1301 Desk' in warning for warning in row['warnings']), row)

    def test_the_site_does_not_block_the_others(self):
        # The owner's rule: one Mac that never got there is that Mac's report, never a stop.
        down = FakeSite('1910', ['controller', 'worker-1'], studios_down=['worker-1'])
        healthy = FakeSite('1920', ['controller'])
        gateway = self.gateway(down, healthy)
        rows = self.by_site(self.rollout(gateway).run([{'id': '1910'}, {'id': '1920'}]))
        self.assertEqual(rows['1910']['state'], 'updated, studios down')
        self.assertEqual(rows['1920']['state'], 'verified')
        self.assertEqual(len(gateway.posts), 2, 'both sites must have been published to')

    def test_the_receipt_records_the_macs_that_are_down(self):
        site = FakeSite('1930', ['controller', 'worker-1'],
                        studios_down=['worker-1'], studio_labels={'worker-1': '1301 Desk'})
        gateway = self.gateway(site)
        rows = self.rollout(gateway).run([{'id': '1930'}])
        receipt = {'schema': 1, 'sites': rows}
        self.assertEqual(receipt['sites'][0]['studios_down'],
                         ['1301 Desk (0 of 4 studios up)'])

    def test_a_site_is_verified_when_every_studio_is_up(self):
        site = FakeSite('1940', ['controller', 'worker-1'])
        gateway = self.gateway(site)
        row = self.by_site(self.rollout(gateway).run([{'id': '1940'}]))['1940']
        self.assertEqual(row['state'], 'verified')
        self.assertEqual(row['studios_down'], [])


class RerunTests(Harness):
    def test_a_rerun_never_republishes_and_says_so(self):
        site = FakeSite('local', ['this-mac'])
        other = FakeSite('1100', ['1100'])
        gateway = self.gateway(site, other)
        sites = [{'id': 'local'}, {'id': '1100'}]

        first = self.rollout(gateway).run(sites)
        self.assertEqual(len(gateway.posts), 2)
        posts_after_first = len(gateway.posts)

        second = self.rollout(gateway).run(sites)
        self.assertEqual(len(gateway.posts), posts_after_first,
                         'a verified site is never republished')
        for row in second:
            self.assertEqual(row['state'], 'verified')
            self.assertEqual(row['post_count'], 0)
            self.assertEqual(row['published_at'], 'already held before this run')
        self.assertEqual(len(first), 2)

    def test_a_receipt_for_another_artifact_is_refused_not_reused(self):
        site = FakeSite('local', ['this-mac'])
        gateway = self.gateway(site)
        self.receipts.mkdir(parents=True, exist_ok=True)
        (self.receipts / f'local-{VERSION}.json').write_text(json.dumps(
            {'schema': hub.SITE_SCHEMA, 'site': 'local', 'version': VERSION, 'build': 'other',
             'sha256': 'b' * 64, 'bytes': 1}))
        with self.assertRaises(SystemExit) as caught:
            self.rollout(gateway).run([{'id': 'local'}])
        self.assertIn('different artifact', str(caught.exception))
        self.assertEqual(gateway.posts, [], 'a refused receipt never becomes a POST')

    def test_an_accepted_publication_writes_one_receipt(self):
        site = FakeSite('local', ['this-mac'])
        gateway = self.gateway(site)
        self.rollout(gateway).run([{'id': 'local'}])
        receipts = list(self.receipts.glob('*.json'))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt['outcome'], 'accepted')
        self.assertEqual(receipt['sha256'], self.accepted['sha256'])


    def test_full_resends_the_image_to_a_site_that_already_holds_a_files_only_release(self):
        # 0.6.197 nodes cannot install from a controller holding only changed files (the
        # artifact route answers 404). A controller that already reports the release is exactly
        # the one that needs the whole image, so `--full` must not take the already-held skip.
        site = FakeSite('1503', ['1503'])
        site.release = dict(self.identity)
        gateway = FakeGateway([site], identity=self.identity)
        row = self.rollout(gateway, full=True).run([{'id': '1503'}])[0]
        self.assertEqual(gateway.full_flags, [True], 'the whole image was asked for')
        self.assertEqual(row['post_count'], 1)
        self.assertEqual(row['state'], 'verified')
        # Its receipt is kept beside the ordinary one, never over it.
        self.assertTrue((self.receipts / f'1503-{VERSION}-full.json').is_file())
        self.assertIs(json.loads((self.receipts / f'1503-{VERSION}-full.json').read_text())
                      ['force_full'], True)

    def test_a_second_full_run_does_not_repost(self):
        site = FakeSite('1503', ['1503'])
        site.release = dict(self.identity)
        gateway = FakeGateway([site], identity=self.identity)
        self.rollout(gateway, full=True).run([{'id': '1503'}])
        self.rollout(gateway, full=True).run([{'id': '1503'}])
        self.assertEqual(len(gateway.posts), 1)

class PauseTests(Harness):
    def test_the_pause_is_read_before_the_first_post_and_again_at_the_end(self):
        gateway = self.gateway(FakeSite('local', ['this-mac']))
        rollout = self.rollout(gateway)
        rollout.run([{'id': 'local'}])
        wheres = [read['where'] for read in rollout.pause_reads]
        self.assertEqual(wheres[0], 'before the first POST')
        self.assertEqual(wheres[-1], 'final readback')
        self.assertTrue(all(read['value'] == gateway.pause_value
                            for read in rollout.pause_reads))

    def test_no_code_path_writes_the_pause(self):
        roster = self.tmp / 'fleet.json'
        roster.write_text(json.dumps({'local_paused_at': '2026-10-26T22:41:00+00:00'}))
        before = roster.read_bytes()
        stat_before = roster.stat().st_mtime_ns
        reader = hub.Gateway(roster=roster)
        gateway = self.gateway(FakeSite('local', ['this-mac']), pause_reader=reader.pause)
        gateway.pause_value = reader.pause()
        rollout = self.rollout(gateway)
        rollout.run([{'id': 'local'}])
        self.assertEqual(roster.read_bytes(), before, 'the pause value is unchanged')
        self.assertEqual(roster.stat().st_mtime_ns, stat_before, 'the roster file was not rewritten')
        self.assertEqual(gateway.pause_value, '2026-10-26T22:41:00+00:00')
        # The tool's own surface has no writer at all.
        self.assertFalse([name for name in dir(hub.Gateway)
                          if name.startswith(('set_', 'write_', 'resume', 'unpause'))])


class MainTests(unittest.TestCase):
    """`main` end to end, with the Gateway faked and the artifact acceptance stubbed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hub-rollout-main-test-'))
        self.artifact = self.tmp / f'StudioHub-{VERSION}-{BUILD}.dmg'
        self.artifact.write_bytes(b'image bytes for main')
        sha = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.identity = {'version': VERSION, 'build': BUILD, 'sha256': sha,
                         'bytes': self.artifact.stat().st_size}
        self.accepted = {'artifact': str(self.artifact), 'name': self.artifact.name,
                         'sha256': sha, 'bytes': self.artifact.stat().st_size,
                         'mtime_ns': self.artifact.stat().st_mtime_ns,
                         'hdiutil_verify_exit': 0, 'app_version': VERSION, 'build': BUILD}
        self.evidence = self.tmp / 'rollout-evidence.json'
        self.originals = (hub.Gateway, hub.accept_artifact, hub.Rollout)
        hub.accept_artifact = lambda *args, **kwargs: dict(self.accepted)
        hub.Gateway = lambda *args, **kwargs: self.gateway

        class FastRollout(hub.Rollout):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault('window_s', WINDOW)
                kwargs.setdefault('poll_s', POLL)
                super().__init__(*args, **kwargs)

        hub.Rollout = FastRollout

    def tearDown(self):
        hub.Gateway, hub.accept_artifact, hub.Rollout = self.originals

    def run_main(self, *sites, only=None, full=False):
        self.gateway = FakeGateway(list(sites), identity=self.identity)
        argv = ['--artifact', str(self.artifact), '--version', VERSION,
                '--build', BUILD, '--evidence', str(self.evidence)]
        if only:
            argv += ['--only-sites', only]
        if full:
            argv += ['--full']
        return hub.main(argv), self.gateway

    def test_writes_the_receipt_and_a_table_and_exits_clean(self):
        code, _ = self.run_main(FakeSite('local', ['this-mac']),
                                FakeSite('1100', ['1100']))
        self.assertEqual(code, 0)
        receipt = json.loads(self.evidence.read_text())
        self.assertEqual(receipt['schema'], hub.SCHEMA)
        self.assertEqual(receipt['version'], VERSION)
        self.assertEqual(receipt['artifact']['sha256'], self.identity['sha256'])
        self.assertEqual([row['state'] for row in receipt['sites']], ['verified', 'verified'])
        self.assertIn('verified', receipt['table'])
        self.assertFalse(receipt['pause']['written_by_tool'])
        self.assertEqual(receipt['pause']['value_baseline'],
                         receipt['pause']['value_final'])

    def test_a_busy_site_is_reported_and_the_run_ends_non_zero(self):
        code, gateway = self.run_main(FakeSite('local', ['this-mac']),
                                      FakeSite('1100', ['1100'], running=['batch-1']))
        self.assertEqual(code, 1)
        receipt = json.loads(self.evidence.read_text())
        states = {row['site']: row['state'] for row in receipt['sites']}
        self.assertEqual(states, {'local': 'verified', '1100': 'busy'})
        self.assertEqual([site for site, _ in gateway.posts], ['local'])

    def test_only_sites_restricts_the_fan_out(self):
        code, gateway = self.run_main(FakeSite('local', ['this-mac'], label='This Mac'),
                                      FakeSite('1100', ['1100']), only='This Mac')
        self.assertEqual(code, 0)
        self.assertEqual([site for site, _ in gateway.posts], ['local'])

    def test_full_asks_the_gateway_for_the_whole_image_and_records_it(self):
        # A 0.6.201 controller advertises delta but refuses a delta over half the image
        # (`MAX_DELTA_FRACTION` 0.5); `--full` is the path that works for it.
        code, gateway = self.run_main(FakeSite('1100', ['1100']), full=True)
        self.assertEqual(code, 0)
        self.assertEqual(gateway.full_flags, [True])
        receipt = json.loads(self.evidence.read_text())
        self.assertIs(receipt['force_full'], True)

    def test_an_ordinary_run_sends_no_force_full(self):
        code, gateway = self.run_main(FakeSite('1100', ['1100']))
        self.assertEqual(code, 0)
        self.assertEqual(gateway.full_flags, [False])
        self.assertIs(json.loads(self.evidence.read_text())['force_full'], False)

    def test_publish_timeout_is_forwarded_and_recorded(self):
        self.gateway = FakeGateway([FakeSite('1100', ['1100'])], identity=self.identity)
        argv = ['--artifact', str(self.artifact), '--version', VERSION,
                '--build', BUILD, '--evidence', str(self.evidence),
                '--publish-timeout', '3600']
        code = hub.main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(self.gateway.timeouts, [3600])
        receipt = json.loads(self.evidence.read_text())
        self.assertEqual(receipt['publish_timeout_s'], 3600)

    def test_publish_recovers_if_controller_holds_release_after_timeout(self):
        # If the gateway publish call times out or fails with an error, but the controller
        # has stored the release, rollout recovers and verifies the site.
        site = FakeSite('1100', ['1100'])
        self.gateway = FakeGateway([site], identity=self.identity)
        # Simulate gateway publish timing out or returning error
        def fail_publish(*args, **kwargs):
            self.gateway.posts.append(('1100', time.monotonic()))
            # But the controller now holds the release
            site.release = dict(self.identity)
            return 0, {'error': 'TimeoutError: timed out'}
        self.gateway.publish = fail_publish

        argv = ['--artifact', str(self.artifact), '--version', VERSION,
                '--build', BUILD, '--evidence', str(self.evidence)]
        code = hub.main(argv)
        self.assertEqual(code, 0)
        receipt = json.loads(self.evidence.read_text())
        self.assertEqual(receipt['sites'][0]['state'], 'verified')
        site_receipt = json.loads((self.evidence.parent / f'hub-rollout-site-receipts/1100-{VERSION}.json').read_text())
        self.assertEqual(site_receipt['code'], 'RECOVERED_HELD')

    def test_a_timed_out_publish_says_it_may_still_be_pushing(self):
        # A timeout is not proof the Gateway stopped: it keeps pushing after the caller leaves,
        # so the row must warn against rerunning straight away.
        site = FakeSite('1100', ['1100'])
        self.gateway = FakeGateway([site], identity=self.identity)

        def fail_publish(*args, **kwargs):
            self.gateway.posts.append(('1100', time.monotonic()))
            return 0, {'error': 'TimeoutError: timed out'}
        self.gateway.publish = fail_publish

        code = hub.main(['--artifact', str(self.artifact), '--version', VERSION,
                         '--build', BUILD, '--evidence', str(self.evidence)])
        self.assertEqual(code, 1)
        row = json.loads(self.evidence.read_text())['sites'][0]
        self.assertEqual(row['state'], 'publish failed')
        self.assertIn('TimeoutError: timed out', row['warnings'][0])
        self.assertIn('may still be pushing', row['warnings'][0])

    def test_an_existing_evidence_file_is_refused_before_anything_is_touched(self):
        self.evidence.write_text('{}')
        self.gateway = FakeGateway([FakeSite('local', ['this-mac'])], identity=self.identity)
        with self.assertRaises(SystemExit):
            hub.main(['--artifact', str(self.artifact), '--version', VERSION,
                      '--build', BUILD, '--evidence', str(self.evidence)])
        self.assertEqual(self.gateway.posts, [])


class ReportingTests(unittest.TestCase):
    def test_the_table_names_every_site_and_its_warnings(self):
        table = hub.render_table([
            {'site': 'local', 'label': 'This Mac', 'published_at': '2026-10-26T23:00:00+00:00',
             'state': 'verified', 'members_updated': 1, 'members_total': 1, 'warnings': []},
            {'site': '1100', 'label': '1100', 'published_at': None, 'state': 'busy',
             'members_updated': 0, 'members_total': 2, 'warnings': ['still busy']},
        ])
        self.assertIn('This Mac', table)
        self.assertIn('verified', table)
        self.assertIn('1/1', table)
        self.assertIn('still busy', table)

    def test_only_sites_selects_by_id_or_label_and_refuses_an_unknown(self):
        locations = [{'id': 'local', 'label': 'This Mac'}, {'id': '1100', 'label': '1100'}]
        self.assertEqual([s['id'] for s in hub.choose_sites(locations, 'This Mac')], ['local'])
        self.assertEqual([s['id'] for s in hub.choose_sites(locations, None)], ['local', '1100'])
        with self.assertRaises(SystemExit):
            hub.choose_sites(locations, 'nosuchsite')


if __name__ == '__main__':
    unittest.main()
