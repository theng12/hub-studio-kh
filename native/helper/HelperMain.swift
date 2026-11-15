// Hub Studio KH Helper — the process the operator sees in Activity Monitor instead of `python3`.
//
// It lives inside the app at Contents/Resources/helper/Hub Studio KH Helper.app and runs the
// bundled worker with the same interpreter the old `StudioHubService` shell shim used. Two things
// it must preserve:
//   * `hub_worker.py` refuses to run unless `--parent-pid` equals its own parent, so the value is
//     rewritten to this process's pid before the child starts.
//   * SIGTERM/SIGINT/SIGHUP are forwarded, because the Hub stops the service with a signal and
//     expects the worker's own shutdown path (readiness removal, child cleanup) to run.
//
// No UI and no AppKit: LSUIElement is declared in the bundle's Info.plist.

import Darwin
import Foundation

let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()

// The host app keeps `hub_worker.py` beside `runtimes/` in Contents/Resources. Find it by that
// payload, never by counting parent directories: 0.6.228 counted four, landed on
// Resources/helper, and shipped a helper that could not start on any Mac.
func locateResources(from executable: URL) -> URL? {
    var candidate = executable.deletingLastPathComponent()
    while candidate.path != "/" && !candidate.path.isEmpty {
        if FileManager.default.isExecutableFile(
                atPath: candidate.appendingPathComponent("runtimes/voice/bin/python3").path),
           FileManager.default.fileExists(atPath: candidate.appendingPathComponent("hub_worker.py").path) {
            return candidate
        }
        candidate.deleteLastPathComponent()
    }
    return nil
}

// Test seam: the native boundary tests stage this binary in a synthetic bundle and ask where it
// resolved, so the guard is proved against the real launch layout without starting a worker.
if CommandLine.arguments.contains("--print-resources") {
    guard let resources = locateResources(from: executable) else {
        FileHandle.standardError.write(Data("Hub Studio KH Helper: the bundled runtime or hub_worker.py is missing\n".utf8))
        exit(127)
    }
    print(resources.path)
    exit(0)
}

guard let resources = locateResources(from: executable) else {
    FileHandle.standardError.write(Data("Hub Studio KH Helper: the bundled runtime or hub_worker.py is missing\n".utf8))
    exit(127)
}

let python = resources.appendingPathComponent("runtimes/voice/bin/python3")
let worker = resources.appendingPathComponent("hub_worker.py")

var arguments = Array(CommandLine.arguments.dropFirst())
if let index = arguments.firstIndex(of: "--parent-pid"), index + 1 < arguments.count {
    arguments[index + 1] = String(getpid())
}
// The app waits for a ready record whose pid is the process it launched: this helper, not
// the python child. Keep the record honest about the owned process.
if let index = arguments.firstIndex(of: "--ready-pid"), index + 1 < arguments.count {
    arguments[index + 1] = String(getpid())
} else {
    arguments += ["--ready-pid", String(getpid())]
}

let child = Process()
child.executableURL = python
child.arguments = ["-I", "-B", worker.path] + arguments
// The same minimal environment the retired shell shim gave the worker.
child.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"]

var signalSources: [DispatchSourceSignal] = []
let signals: [Int32] = [SIGTERM, SIGINT, SIGHUP]
for signalNumber in signals {
    signal(signalNumber, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .global())
    source.setEventHandler {
        if child.isRunning { kill(child.processIdentifier, signalNumber) }
    }
    source.resume()
    // Held for the life of the process; a released source stops delivering.
    signalSources.append(source)
}

do {
    try child.run()
} catch {
    FileHandle.standardError.write(Data("Hub Studio KH Helper: could not start the worker: \(error)\n".utf8))
    exit(126)
}
child.waitUntilExit()
exit(child.terminationStatus)
