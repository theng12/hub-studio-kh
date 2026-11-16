"""Fixture seams for native/render-page.sh, applied to its throwaway copy of the sources only.

Nothing here is in the shipped app. With STUDIOHUB_RENDER_FIXTURES=1 the render harness can fill the
Audio/Image/Music history with long-prompt jobs, make the pages read as connected, and serve a
generated PNG for every image fetch (and STUDIOHUB_RENDER_IMAGE_MODAL=<job id> opens that
image's large view), so a layout is checked against the text that broke it.
"""
import os
import pathlib
import sys

api, views = (pathlib.Path(a) for a in sys.argv[1:3])
notes = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None
shell = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None

# Connections shows this Mac's two tokens (masked) only while the services run, so a render can give it a
# fixture Hub token; the Fleet token is read from a fixture file the harness writes.
if shell is not None:
    text = shell.read_text()
    needle = "@Published private(set) var connectionToken: String?"
    assert needle in text, "connectionToken moved; update render_fixture_patch.py"
    shell.write_text(text.replace(needle, "@Published var connectionToken: String?", 1))

text = api.read_text()
for name in ("audioJobs", "imageJobs", "musicJobs"):
    needle = f"@Published private(set) var {name}: [NativeJob]"
    assert needle in text, f"{name} moved; update render_fixture_patch.py"
    text = text.replace(needle, f"@Published var {name}: [NativeJob]", 1)
# The rest of what the Models, Voices and Connections pages read, so they render with data.
for decl, kind in (("models", "[NativeModel]"), ("voices", "[NativeVoice]"), ("hubVersion", "NativeVersion?"),
                   ("fleetReadiness", "[String: NativeMachineReadiness]"), ("workstation", "NativeWorkstation?"),
                   ("workPolicy", "NativeWorkPolicy?"),
                   # The Render page's list, seeded by render_main's own fixture (2026-10-26).
                   ("renderHistory", "NativeRenderHistory?"),
                   # A download in flight, so the Models page's downloading chip is rendered.
                   ("downloads", "[NativeDownload]")):
    needle = f"@Published private(set) var {decl}: {kind}"
    assert needle in text, f"{decl} moved; update render_fixture_patch.py"
    text = text.replace(needle, f"@Published var {decl}: {kind}", 1)
# Settings > Local storage: the Trash Folder row and the Empty All Trash button (0.6.243).
for name in ("controllerIdentity", "fleetOverview", "fleetNodes", "controllerReachable",
             "catalogReadError", "fleetReadError", "trashFolder"):
    needle = f"@Published private(set) var {name}:"
    assert needle in text, f"{name} moved; update render_fixture_patch.py"
    text = text.replace(needle, f"@Published var {name}:", 1)
needle = "func imageData(for job: NativeJob) async throws -> Data {\n"
assert needle in text, "imageData moved; update render_fixture_patch.py"
text = text.replace(
    needle,
    needle + '        if let message = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_IMAGE_ERROR"], '
             '!message.isEmpty { throw NativeAPIError.message(message) }\n'
             '        if let p = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_IMAGE"], '
             'let d = FileManager.default.contents(atPath: p) { return d }\n', 1)
if "func refreshRenderAuthority() async {" in text:
    needle = "    func refreshRenderAuthority() async {\n"
    grant = '{"ok":true,"render_authority":{"accept_unqualified":true,"effective":true,"grant_version":"v2","granted_encodes":[{"container":"mp4","requested_codec":"h264","requested_effort":"hardware","crf":23,"quality_cpu_preset":"medium","bitrate_kbps":8000}],"migration_provenance":{"authorization_id":"fixture-owner-direction","authorized_at":1700000000}}}'
    text = text.replace(needle, needle + '''        if ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_FIXTURES"] == "1" {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            renderAuthority = try? decoder.decode(NativeRenderAuthorityResponse.self, from: Data(#"''' + grant + '''"#.utf8)).renderAuthority
            return
        }
''', 1)
api.write_text(text)

text = views.read_text()
needle = "private var connected: Bool { studio.readyURL != nil && !studio.stopping && workspace.isReachable }"
assert needle in text, "connected moved; update render_fixture_patch.py"
text = text.replace(
    needle,
    'private var connected: Bool { (ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_FIXTURES"] == "1" '
    '&& ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_SERVICES"] != "off") '
    '|| (studio.readyURL != nil && !studio.stopping && workspace.isReachable) }', 1)
needle = "@State private var selectedModel: String?"
assert needle in text, "selectedModel moved; update render_fixture_patch.py"
text = text.replace(
    needle, '@State private var selectedModel: String? = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_MODEL"]', 1)
needle = "@State private var imageModalID: String?"
assert needle in text, "imageModalID moved; update render_fixture_patch.py"
text = text.replace(
    needle, '@State private var imageModalID: String? = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_IMAGE_MODAL"]', 1)
# Observe the real node-address button's layout only in the throwaway render sources.
needle = 'CopyValueButton(value: address)\n                        .accessibilityLabel("Copy node join address")'
if os.environ.get("STUDIOHUB_RENDER_CONNECTION_COPY") == "1":
    assert needle in text, "Node join Copy button moved; update render_fixture_patch.py"
    text = text.replace(needle, '''CopyValueButton(value: address, onCopy: { ConnectionCopyRenderProbe.didCopy = true })
                        .accessibilityLabel("Copy node join address")
                        .background(GeometryReader { geometry in
                            Color.clear
                                .onAppear { ConnectionCopyRenderProbe.frame = geometry.frame(in: .named("ConnectionCopyRender")) }
                                .onChange(of: geometry.frame(in: .named("ConnectionCopyRender"))) { _, frame in
                                    ConnectionCopyRenderProbe.frame = frame
                                }
                        })''', 1)
views.write_text(text)

# What's New reads the notes shipped in the app bundle, which a render has none of. STUDIOHUB_RENDER_NOTES
# points it at a markdown file (release-notes.md) so the page can be looked at with real content.
if notes is not None:
    text = notes.read_text()
    needle = "    static func text() -> String {\n"
    assert needle in text, "ReleaseNotes.text moved; update render_fixture_patch.py"
    text = text.replace(
        needle,
        needle + '        if let p = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_NOTES"], '
                 'let t = try? String(contentsOfFile: p, encoding: .utf8) { return t }\n', 1)
    notes.write_text(text)
