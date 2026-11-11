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
// .../Contents/Resources/helper/Hub Studio KH Helper.app/Contents/MacOS/Hub Studio KH Helper
var resources = executable
for _ in 0..<4 { resources.deleteLastPathComponent() }

let python = resources.appendingPathComponent("runtimes/voice/bin/python3")
let worker = resources.appendingPathComponent("hub_worker.py")
guard FileManager.default.isExecutableFile(atPath: python.path),
      FileManager.default.fileExists(atPath: worker.path) else {
    FileHandle.standardError.write(Data("Hub Studio KH Helper: the bundled runtime or hub_worker.py is missing\n".utf8))
    exit(127)
}

var arguments = Array(CommandLine.arguments.dropFirst())
if let index = arguments.firstIndex(of: "--parent-pid"), index + 1 < arguments.count {
    arguments[index + 1] = String(getpid())
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
