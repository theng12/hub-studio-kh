import AppKit
import SwiftUI

// The masked-secret pieces in native/Design.swift: a hidden secret is never in what the view draws.
// Run from the lifecycle gate (tests/native/lifecycle_main.swift), which native/build.sh runs.
enum SecretChecks {
    @MainActor static func run() {
        var failures = 0
        func check(_ ok: Bool, _ what: String) { if !ok { failures += 1; print("FAIL: \(what)") } }
        let secret = "hub-test-secret-0123456789abcdef"

        // The pure rule every secret row uses.
        check(SecretMask.text(secret, revealed: false) == SecretMask.dots, "hidden shows only the dots")
        check(!SecretMask.text(secret, revealed: false).contains("hub-"), "hidden leaks no prefix")
        check(!SecretMask.text(secret, revealed: false).contains("cdef"), "hidden leaks no suffix")
        check(SecretMask.text("x", revealed: false).count == SecretMask.text(secret, revealed: false).count,
              "hidden says nothing about length")
        check(SecretMask.text(secret, revealed: true) == secret, "revealed shows the value")
        check(SecretMask.text(nil, revealed: true) == "Unavailable", "no value reads as Unavailable")

        // The real view, drawn offscreen. Two different secrets must draw the *same pixels* while masked: if
        // any part of the value reached the screen, the two images would differ.
        NSApplication.shared.setActivationPolicy(.prohibited)
        func draw(_ value: String?) -> Data? {
            let hosting = NSHostingView(rootView: SecretValue(value: value, copyTitle: "Copy Token").padding())
            hosting.frame = NSRect(x: 0, y: 0, width: 520, height: 60)
            let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = hosting
            window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            window.orderFront(nil)
            for _ in 0..<6 {
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                hosting.layoutSubtreeIfNeeded()
                window.displayIfNeeded()
            }
            guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { return nil }
            hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
            window.orderOut(nil)
            return bitmap.representation(using: .png, properties: [:])
        }
        let first = draw(secret)
        let second = draw("another-value-entirely-ZZZZZZZZZZZZZZZZZZZZ")
        let unavailable = draw(nil)
        check(first != nil && second != nil && unavailable != nil, "the view draws offscreen")
        check(first == second, "two different secrets draw identical pixels while masked")
        check(first != unavailable, "the drawing is live (a missing value draws differently)")

        if failures > 0 { exit(1) }
        print("Native secret masking checks passed")
    }
}
