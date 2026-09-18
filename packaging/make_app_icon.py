#!/usr/bin/env python3
"""Draw Studio Hub's app icon and assemble the macOS .icns.

Build tool only; run when the mark changes, and commit the resulting `assets/app-icon.icns` so a
staged bundle carries a real icon instead of the blank page macOS shows for a bundle with none.

    .build/voice-runtime/bin/python3 packaging/make_app_icon.py

Why the mark is drawn rather than shipped as a bitmap: a single source of truth for every size. The
1024px master is rendered once and every smaller size is a proper downscale of it, so the small
icons in the Dock and the menu bar are the same drawing rather than separately hand-tuned files that
drift apart.

The mark: a broadcast source in the middle of a waveform, inside the rounded square macOS expects.
It is the app's own vocabulary - a Hub with Studios around it, carrying sound and image - and it
stays legible at 16px because there are only three shapes in it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICNS = ASSETS / "app-icon.icns"
MASTER = ASSETS / "app-icon-1024.png"

# The sizes macOS asks for. 1024@2x is the master; every entry is (pixels, name in the iconset).
SIZES = (
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
)

# The drawing, in a 1024x1024 space. Kept here as data so the Swift below is only plumbing.
CANVAS = 1024
# macOS leaves a margin around the icon's artwork; roughly 824 of 1024 is the visible tile.
TILE_INSET = 100
TILE = CANVAS - TILE_INSET * 2
CORNER = TILE * 0.2237          # the system's own corner radius for an app tile this size
DRAW = r'''
import AppKit
import CoreGraphics
import Foundation

let canvas = CGFloat(CANVAS)
let inset = CGFloat(TILE_INSET)
let tile = CGRect(x: inset, y: inset, width: CGFloat(TILE), height: CGFloat(TILE))
let corner = CGFloat(CORNER)

guard let context = CGContext(data: nil, width: Int(canvas), height: Int(canvas),
                              bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpace(name: CGColorSpace.sRGB)!,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    FileHandle.standardError.write(Data("could not create a drawing context\n".utf8)); exit(1)
}
context.setAllowsAntialiasing(true)
context.interpolationQuality = .high

// ── the tile ────────────────────────────────────────────────────────────────────────────────
// A deep indigo-to-violet gradient: the app is used in a dark room, and a saturated tile reads at
// every size where a pale one washes out against a light Dock.
let tilePath = CGPath(roundedRect: tile, cornerWidth: corner, cornerHeight: corner, transform: nil)
context.saveGState()
context.addPath(tilePath)
context.clip()
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let gradient = CGGradient(colorsSpace: space, colors: [
    CGColor(red: 0.153, green: 0.176, blue: 0.431, alpha: 1),   // #272D6E
    CGColor(red: 0.435, green: 0.239, blue: 0.757, alpha: 1),   // #6F3DC1
] as CFArray, locations: [0, 1])!
context.drawLinearGradient(gradient, start: CGPoint(x: tile.minX, y: tile.maxY),
                           end: CGPoint(x: tile.maxX, y: tile.minY), options: [])
// A soft highlight from the top-left, so the tile has a light source rather than being flat.
let sheen = CGGradient(colorsSpace: space, colors: [
    CGColor(red: 1, green: 1, blue: 1, alpha: 0.20),
    CGColor(red: 1, green: 1, blue: 1, alpha: 0.0),
] as CFArray, locations: [0, 1])!
context.drawRadialGradient(sheen, startCenter: CGPoint(x: tile.minX + tile.width * 0.28,
                                                       y: tile.maxY - tile.height * 0.18),
                           startRadius: 0,
                           endCenter: CGPoint(x: tile.midX, y: tile.midY),
                           endRadius: tile.width * 0.78, options: [])
context.restoreGState()

// ── the broadcast source ────────────────────────────────────────────────────────────────────
// Two rings around a filled centre: a Hub with its Studios, as the app describes itself.
//
// Stacked above the waveform rather than behind it. Overlapping them merged the rings, the bars and
// the dot into one pale shape that read as an indistinct smudge at menu-bar size; separating them
// keeps three countable elements at 16px, which is the whole job of an icon this small.
let centre = CGPoint(x: canvas / 2, y: canvas * 0.640)
context.setLineCap(.round)
let ringWidth = canvas * 0.028
context.setLineWidth(ringWidth)
context.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.95))
context.addArc(center: centre, radius: canvas * 0.168, startAngle: 0, endAngle: .pi * 2, clockwise: false)
context.strokePath()
context.setStrokeColor(CGColor(red: 0.55, green: 0.93, blue: 1.0, alpha: 1))
context.addArc(center: centre, radius: canvas * 0.104, startAngle: 0, endAngle: .pi * 2, clockwise: false)
context.strokePath()
context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
context.fillEllipse(in: CGRect(x: centre.x - canvas * 0.040, y: centre.y - canvas * 0.040,
                               width: canvas * 0.080, height: canvas * 0.080))

// ── the sound ───────────────────────────────────────────────────────────────────────────────
// A waveform below the source, symmetric around its own centre line, tapering to the edges. Wide
// and low, so it reads as sound rather than competing with the rings for the middle of the tile.
// Heights are capped so the tallest bar stays clear of the outer ring: the ring's lower edge sits
// at 0.640 - 0.168 = 0.472 of the canvas, and the highest bar reaches 0.235 + 0.105 = 0.340. The gap
// between them is what keeps the two elements countable instead of a single stack.
let bars: [(CGFloat, CGFloat)] = [   // (half-height, x offset from the icon centre) as fractions
    (0.023, -0.196), (0.046, -0.147), (0.070, -0.098), (0.090, -0.049),
    (0.105, 0.000), (0.090, 0.049), (0.070, 0.098), (0.046, 0.147), (0.023, 0.196),
]
let waveCentre = CGPoint(x: canvas / 2, y: canvas * 0.235)
context.setLineCap(.round)
for (half, dx) in bars {
    let x = waveCentre.x + canvas * dx
    context.setStrokeColor(CGColor(red: 0.60, green: 0.94, blue: 1.0, alpha: 1))
    context.setLineWidth(canvas * 0.034)
    context.move(to: CGPoint(x: x, y: waveCentre.y - canvas * half))
    context.addLine(to: CGPoint(x: x, y: waveCentre.y + canvas * half))
    context.strokePath()
}

guard let image = context.makeImage() else {
    FileHandle.standardError.write(Data("could not render the icon\n".utf8)); exit(1)
}
let rep = NSBitmapImageRep(cgImage: image)
guard let png = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write(Data("could not encode the icon\n".utf8)); exit(1)
}
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
'''


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("the icon is drawn with AppKit; run this on macOS")
    ASSETS.mkdir(parents=True, exist_ok=True)
    swift = ASSETS / ".make_app_icon.swift"
    swift.write_text(
        f"let CANVAS = {CANVAS}.0\nlet TILE_INSET = {TILE_INSET}.0\nlet TILE = {TILE}.0\n"
        f"let CORNER = {CORNER:.4f}\n{DRAW}")
    try:
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
        binary = ASSETS / ".make_app_icon"
        subprocess.run(["xcrun", "swiftc", "-sdk", sdk, "-O", str(swift), "-o", str(binary)], check=True)
        subprocess.run([str(binary), str(MASTER)], check=True)
    finally:
        for path in (swift, ASSETS / ".make_app_icon"):
            path.unlink(missing_ok=True)

    iconset = ASSETS / "app-icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    for pixels, name in SIZES:
        subprocess.run(["sips", "-z", str(pixels), str(pixels), str(MASTER),
                        "--out", str(iconset / name)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ICNS.unlink(missing_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True)
    shutil.rmtree(iconset, ignore_errors=True)
    print(json.dumps({"icns": str(ICNS.relative_to(ROOT)),
                      "bytes": ICNS.stat().st_size,
                      "sizes": [pixels for pixels, _ in SIZES]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
