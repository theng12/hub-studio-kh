// What the app itself reads back from a real controller and a real node.
//
// The Hub contract is covered by `tests/test_fleet_pool_visibility.py`, which drives two live Hub
// processes. This is the other half: it proves the *app* decodes what those Hubs answer, through
// the same `NativeWorkspaceStore` the Connections screen uses - so a field the Hub sends and the
// app silently drops fails here rather than in front of the operator.
//
// It is a manual check, not part of `native/build.sh`, because it needs two joined Hubs to talk
// to. Build and run it with the harness:
//
//   ./tests/native/pool-probe.sh
//
// The output is the app's own view: this Mac's role and credential origin, then one line per
// machine in the pool with what the app decoded for it.
import Foundation

@main
struct PoolProbe {
    @MainActor
    static func main() async throws {
        guard let address = ProcessInfo.processInfo.environment["PROBE_URL"],
              let token = ProcessInfo.processInfo.environment["PROBE_TOKEN"],
              let url = URL(string: address) else {
            print("no PROBE_URL/PROBE_TOKEN")
            return
        }
        let scratch = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("pool-probe-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
        let store = NativeWorkspaceStore()
        await store.connect(baseURL: url, dataDirectory: scratch, token: token,
                            allowedHost: "127.0.0.1")
        try? await Task.sleep(for: .seconds(3))
        await store.refreshConnection()
        await store.refreshFleetNodes()
        print("version:", store.hubVersion?.version ?? "nil")
        print("role:", store.controllerIdentity?.settings.role ?? "nil")
        print("origin:", store.controllerIdentity?.settings.fleetCredentialOrigin ?? "nil")
        if let overview = store.fleetOverview {
            print("overview role:", overview.role ?? "nil",
                  "| member:", overview.isMember,
                  "| roster readable:", overview.rosterReadable.map(String.init) ?? "nil")
            print("controller:", overview.controller?.label ?? "nil",
                  "|", overview.controller?.state ?? "-",
                  "|", overview.controller?.address ?? "-")
            for row in overview.machines {
                print("  row:", row.machine, "|", row.label, "|", row.role ?? "-",
                      "| reachable:", row.reachable, "|", row.address ?? "-")
            }
        } else {
            print("overview: nil")
        }
        print("own roster:", store.fleetNodes.map { "\($0.role), \($0.nodes.count) node(s)" } ?? "nil")
        if let error = store.error { print("error:", error) }
    }
}
