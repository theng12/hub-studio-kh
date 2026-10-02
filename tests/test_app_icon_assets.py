"""The committed icon files are the Wave Frame mark at the sizes the app and macOS need."""
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', f'{path.name} is not a PNG'
    return struct.unpack('>II', data[16:24])


class AppIconAssetsTests(unittest.TestCase):
    def test_master_and_menubar_sizes(self):
        self.assertEqual(png_size(ASSETS / 'app-icon-1024.png'), (1024, 1024))
        self.assertEqual(png_size(ASSETS / 'menubar-icon.png'), (18, 18))
        self.assertEqual(png_size(ASSETS / 'menubar-icon@2x.png'), (36, 36))

    @unittest.skipUnless(sys.platform == 'darwin', 'iconutil is macOS only')
    def test_icns_carries_every_size(self):
        self.assertEqual((ASSETS / 'app-icon.icns').read_bytes()[:4], b'icns')
        with tempfile.TemporaryDirectory() as temp:
            iconset = Path(temp) / 'x.iconset'
            subprocess.run(['iconutil', '-c', 'iconset', str(ASSETS / 'app-icon.icns'), '-o', str(iconset)],
                           check=True, capture_output=True)
            sizes = {p.name: png_size(p)[0] for p in iconset.iterdir()}
        # 16-512 at @1x and @2x; icon_512x512@2x is the 1024 master.
        want = {'icon_16x16.png': 16, 'icon_16x16@2x.png': 32, 'icon_32x32.png': 32, 'icon_32x32@2x.png': 64,
                'icon_128x128.png': 128, 'icon_128x128@2x.png': 256, 'icon_256x256.png': 256,
                'icon_256x256@2x.png': 512, 'icon_512x512.png': 512, 'icon_512x512@2x.png': 1024}
        self.assertEqual(sizes, want)

    def test_svg_sources_are_the_generator_inputs(self):
        self.assertTrue((ASSETS / 'logo/app-icon.svg').is_file())
        self.assertTrue((ASSETS / 'logo/menubar-icon.svg').is_file())

    def test_menubar_mark_is_a_template_image_with_a_bundled_resource(self):
        shell = (ROOT / 'native/StudioHub.swift').read_text()
        self.assertIn('image.isTemplate = true', shell)
        self.assertIn('Self.menuBarMark()', shell)
        stage = (ROOT / 'packaging/stage_voice.py').read_text()
        self.assertIn("'menubar-icon.png', 'menubar-icon@2x.png'", stage)


if __name__ == '__main__':
    unittest.main()
