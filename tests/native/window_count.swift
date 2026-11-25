// Prints how many on-screen windows a process owns, so "the update opened nothing in front of
// anyone" can be measured instead of assumed. Read-only: it asks the window server and never
// touches, activates or moves a window.
//
//   swiftc -O tests/native/window_count.swift -o /tmp/window_count
//   /tmp/window_count <pid>
import CoreGraphics
import Foundation

guard let argument = CommandLine.arguments.dropFirst().first, let pid = Int(argument) else {
    FileHandle.standardError.write(Data("usage: window_count <pid>\n".utf8))
    exit(2)
}
let windows = CGWindowListCopyWindowInfo(
    [.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
let owned = windows.filter { window in
    guard let owner = window[kCGWindowOwnerPID as String] as? NSNumber else { return false }
    // A window with no area is not on anyone's screen.
    guard let bounds = window[kCGWindowBounds as String] as? [String: Any],
          let height = (bounds["Height"] as? NSNumber)?.doubleValue,
          let width = (bounds["Width"] as? NSNumber)?.doubleValue else { return false }
    return owner.intValue == pid && height >= 1 && width >= 1
}
print(owned.count)
