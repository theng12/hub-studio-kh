"""Fixture seams for native/render-page.sh, applied to its throwaway copy of the sources only.

Nothing here is in the shipped app. With STUDIOHUB_RENDER_FIXTURES=1 the render harness can fill the
Audio/Image/Music history with long-prompt jobs, make the pages read as connected, and serve a
generated PNG for every image fetch (and STUDIOHUB_RENDER_IMAGE_MODAL=<job id> opens that
image's large view), so a layout is checked against the text that broke it.
"""
import pathlib
import sys

api, views = (pathlib.Path(a) for a in sys.argv[1:3])

text = api.read_text()
for name in ("audioJobs", "imageJobs", "musicJobs"):
    needle = f"@Published private(set) var {name}: [NativeJob]"
    assert needle in text, f"{name} moved; update render_fixture_patch.py"
    text = text.replace(needle, f"@Published var {name}: [NativeJob]", 1)
for name in ("controllerIdentity", "fleetOverview", "fleetNodes", "controllerReachable"):
    needle = f"@Published private(set) var {name}:"
    assert needle in text, f"{name} moved; update render_fixture_patch.py"
    text = text.replace(needle, f"@Published var {name}:", 1)
needle = "func imageData(for job: NativeJob) async throws -> Data {\n"
assert needle in text, "imageData moved; update render_fixture_patch.py"
text = text.replace(
    needle,
    needle + '        if let p = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_IMAGE"], '
             'let d = FileManager.default.contents(atPath: p) { return d }\n', 1)
api.write_text(text)

text = views.read_text()
needle = "private var connected: Bool { studio.readyURL != nil && !studio.stopping && workspace.isReachable }"
assert needle in text, "connected moved; update render_fixture_patch.py"
text = text.replace(
    needle,
    'private var connected: Bool { ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_FIXTURES"] == "1" '
    '|| (studio.readyURL != nil && !studio.stopping && workspace.isReachable) }', 1)
needle = "@State private var imageModalID: String?"
assert needle in text, "imageModalID moved; update render_fixture_patch.py"
text = text.replace(
    needle, '@State private var imageModalID: String? = ProcessInfo.processInfo.environment["STUDIOHUB_RENDER_IMAGE_MODAL"]', 1)
views.write_text(text)
