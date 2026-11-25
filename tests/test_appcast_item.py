"""`packaging/appcast.sh`: the Sparkle item names the DMG's own bytes and version.

The failure this covers happened to Story on 2026-11-24 (row 20261125-4): the item tool read the
version out of the checkout instead of the file it had just signed, and the feed served 1.4.419's
name over 1.4.420's bytes. The script mounts the DMG, reads the bundle inside it, and refuses a
bundle that disagrees with VERSION; the test proves both sides of that.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"


def make_dmg(directory: Path, version: str) -> Path:
    app = directory / 'src/Hub Studio KH.app/Contents'
    app.mkdir(parents=True)
    (app / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.studiohub.standalone',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version}))
    dmg = directory / f'Hub Studio KH-{version}.dmg'
    subprocess.run(['hdiutil', 'create', '-quiet', '-fs', 'HFS+', '-srcfolder',
                    str(directory / 'src'), '-volname', 'Hub Studio KH', str(dmg)], check=True)
    return dmg


class AppcastItemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        root = self.tmp / 'repo'
        (root / 'packaging').mkdir(parents=True)
        (root / 'vendor/khsparkle').mkdir(parents=True)
        shutil.copy2(ROOT / 'packaging/appcast.sh', root / 'packaging/appcast.sh')
        shutil.copy2(ROOT / 'vendor/khsparkle/sign_update', root / 'vendor/khsparkle/sign_update')
        (root / 'VERSION').write_text('0.6.261\n')
        # A fixture key, not a credential: sign_update needs 96 base64-decoded bytes and this
        # item's signature proves the plumbing, not a feed anybody trusts.
        self.key = self.tmp / 'key'
        self.key.write_bytes(base64.b64encode(os.urandom(96)))
        self.root = root

    def run_appcast(self, dmg: Path) -> subprocess.CompletedProcess:
        env = {**os.environ, 'HUB_SPARKLE_PRIVATE': str(self.key)}
        return subprocess.run(['bash', str(self.root / 'packaging/appcast.sh'), str(dmg)],
                              capture_output=True, text=True, env=env)

    def items(self) -> list:
        path = self.root / 'docs/appcast.xml'
        return ET.parse(path).getroot().find('channel').findall('item')

    def test_the_item_names_the_dmgs_own_version_bytes_and_signature(self):
        dmg = make_dmg(self.tmp, '0.6.261')
        result = self.run_appcast(dmg)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        [item] = self.items()
        self.assertEqual(item.findtext(f'{{{SPARKLE}}}shortVersionString'), '0.6.261')
        self.assertEqual(item.findtext(f'{{{SPARKLE}}}minimumSystemVersion'), '14.0')
        enclosure = item.find('enclosure')
        self.assertEqual(enclosure.get('length'), str(dmg.stat().st_size))
        self.assertEqual(enclosure.get('url'), 'https://github.com/theng12/hub-studio-kh/releases/'
                         'download/0.6.261/Hub.Studio.KH-0.6.261.dmg')
        signature = enclosure.get(f'{{{SPARKLE}}}edSignature')
        self.assertRegex(signature, r'^[A-Za-z0-9+/]{86}==$')
        # The same version again is refused: one version = one item, forever.
        again = self.run_appcast(dmg)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('already carries an item', again.stderr)
        self.assertEqual(len(self.items()), 1)

    def test_a_dmg_whose_bundle_disagrees_with_version_is_refused(self):
        dmg = make_dmg(self.tmp, '0.6.999')
        result = self.run_appcast(dmg)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('says 0.6.999 but VERSION says 0.6.261', result.stderr)
        self.assertFalse((self.root / 'docs/appcast.xml').exists(),
                         'a refused item must leave no feed behind')


if __name__ == '__main__':
    unittest.main()
