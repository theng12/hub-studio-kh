#!/usr/bin/env python3
"""Render Studio Hub's app icon (.icns) and menu-bar mark from the logo SVGs.

Build tool only; run when the mark changes, and commit what it writes, so a staged bundle carries a
real icon instead of the blank page macOS shows for a bundle with none:

    python3 packaging/make_app_icon.py

The mark is "Wave Frame" (chosen by the owner 2026-10-02 from six concepts): a picture frame holding
a wave with a rising note, on a dark indigo tile. The two SVGs in `assets/logo/` are the single
source of truth:

    assets/logo/app-icon.svg       the 1024 tile, with the margin and shadow macOS expects
    assets/logo/menubar-icon.svg   the one-colour 18 pt glyph used as a template image

Each iconset size is rendered from the SVG at its exact pixel size (AppKit's own SVG renderer, the
one an asset catalog uses) rather than downscaled from one bitmap, so 16 px is drawn at 16 px. The
menu-bar glyph is written at 1x and 2x (18 and 36 px, black plus alpha); the app tints it for light
and dark menu bars.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
APP_SVG = ASSETS / "logo" / "app-icon.svg"
MENU_SVG = ASSETS / "logo" / "menubar-icon.svg"
ICNS = ASSETS / "app-icon.icns"
MASTER = ASSETS / "app-icon-1024.png"
MENU_1X = ASSETS / "menubar-icon.png"
MENU_2X = ASSETS / "menubar-icon@2x.png"

# The sizes macOS asks for: (pixels, name in the iconset). 1024 is icon_512x512@2x.
SIZES = (
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
)

# Arguments: <svg> <pixels> <out.png>, repeated. Transparent, exact pixel size.
RASTER = r'''
import AppKit
let a = CommandLine.arguments
var i = 1
while i + 2 < a.count {
    guard let img = NSImage(contentsOf: URL(fileURLWithPath: a[i])), let n = Int(a[i + 1]) else {
        FileHandle.standardError.write(Data("cannot read \(a[i])\n".utf8)); exit(1)
    }
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: n, pixelsHigh: n, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: n, height: n)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    img.draw(in: NSRect(x: 0, y: 0, width: n, height: n))
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[i + 2]))
    i += 3
}
'''


def render(jobs) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = Path(tmp) / "raster.swift", Path(tmp) / "raster"
        src.write_text(RASTER)
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
        subprocess.run(["xcrun", "swiftc", "-sdk", sdk, "-O", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe), *[str(x) for job in jobs for x in job]], check=True)


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("the icon is rendered with AppKit; run this on macOS")
    iconset = ASSETS / "app-icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    render([(APP_SVG, pixels, iconset / name) for pixels, name in SIZES]
           + [(APP_SVG, 1024, MASTER), (MENU_SVG, 18, MENU_1X), (MENU_SVG, 36, MENU_2X)])
    ICNS.unlink(missing_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True)
    shutil.rmtree(iconset, ignore_errors=True)
    print(json.dumps({"icns": str(ICNS.relative_to(ROOT)), "bytes": ICNS.stat().st_size,
                      "sizes": [pixels for pixels, _ in SIZES],
                      "menubar": [str(p.relative_to(ROOT)) for p in (MENU_1X, MENU_2X)]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
