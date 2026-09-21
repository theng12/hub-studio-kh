"""Behavioral checks for the portable native build entry points."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeBuildScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-native-build-")
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        shutil.copytree(ROOT / "native", self.project / "native")
        shutil.copytree(ROOT / "tests" / "native", self.project / "tests" / "native")
        self.log = self.root / "xcrun.log"
        self.runs = self.root / "runs.log"
        tool = self.root / "bin" / "xcrun"
        tool.parent.mkdir()
        tool.write_text(textwrap.dedent("""\
            #!/bin/sh
            set -eu
            printf '%s\\n' "$*" >> "$FAKE_XCRUN_LOG"
            if [ "$1" = "--sdk" ]; then
              if [ "$2" = "${FAKE_REJECT_SDK:-}" ]; then
                echo "SDK $2 unavailable" >&2
                exit 70
              fi
              if [ "$3" = "--show-sdk-path" ]; then
                printf '/fake-sdk/%s\\n' "$2"
                exit 0
              fi
              shift 3
            fi
            [ "$1" = "swiftc" ]
            output=""
            while [ "$#" -gt 0 ]; do
              if [ "$1" = "-o" ]; then output="$2"; shift 2; continue; fi
              shift
            done
            mkdir -p "$(dirname "$output")"
            cat > "$output" <<'EOF'
            #!/bin/sh
            printf '%s\\n' "$(basename "$0")" >> "$FAKE_RUN_LOG"
            case "$(basename "$0")" in
              native-lifecycle-tests) exit "${FAKE_LIFECYCLE_EXIT:-0}" ;;
            esac
            EOF
            chmod +x "$output"
        """))
        tool.chmod(0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, script, *, sdk=None, reject=None, lifecycle_exit=None):
        environment = {
            key: value for key, value in os.environ.items()
            if key != "STUDIOHUB_MACOS_SDK" and not key.startswith("FAKE_")
        } | {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "FAKE_XCRUN_LOG": str(self.log),
            "FAKE_RUN_LOG": str(self.runs),
        }
        if sdk is not None:
            environment["STUDIOHUB_MACOS_SDK"] = sdk
        if reject is not None:
            environment["FAKE_REJECT_SDK"] = reject
        if lifecycle_exit is not None:
            environment["FAKE_LIFECYCLE_EXIT"] = str(lifecycle_exit)
        return subprocess.run(["bash", str(self.project / "native" / script)], cwd=self.project,
                              env=environment, capture_output=True, text=True)

    def log_lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_build_uses_pinned_default_and_runs_lifecycle_gate(self):
        result = self.run_script("build.sh")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("--sdk macosx26.5 --show-sdk-path", self.log_lines()[0])
        lifecycle_compile = next(line for line in self.log_lines()
                                 if "lifecycle_main.swift" in line)
        self.assertIn("-D LIFECYCLE_TESTING", lifecycle_compile)
        self.assertIn("-parse-as-library", lifecycle_compile)
        self.assertEqual(
            ["native-boundary-tests", "native-api-tests", "native-lifecycle-tests"],
            self.runs.read_text().splitlines()[:3],
        )

    def test_build_uses_explicit_sdk_selector(self):
        result = self.run_script("build.sh", sdk="macosx99.0")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("--sdk macosx99.0 --show-sdk-path", self.log_lines()[0])

    def test_render_uses_explicit_sdk_selector(self):
        result = self.run_script("render-page.sh", sdk="macosx99.0")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("--sdk macosx99.0 --show-sdk-path", self.log_lines()[0])

    def test_render_uses_pinned_default_sdk_selector(self):
        result = self.run_script("render-page.sh")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("--sdk macosx26.5 --show-sdk-path", self.log_lines()[0])

    def test_unavailable_explicit_sdk_stops_before_build_compilation(self):
        result = self.run_script("build.sh", sdk="macosx-missing", reject="macosx-missing")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("macosx-missing", result.stderr)
        self.assertIn("STUDIOHUB_MACOS_SDK", result.stderr)
        self.assertEqual(["--sdk macosx-missing --show-sdk-path"], self.log_lines())
        self.assertFalse(self.runs.exists())

    def test_unavailable_explicit_sdk_stops_render_before_compilation(self):
        result = self.run_script("render-page.sh", sdk="macosx-missing", reject="macosx-missing")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("macosx-missing", result.stderr)
        self.assertIn("STUDIOHUB_MACOS_SDK", result.stderr)
        self.assertEqual(["--sdk macosx-missing --show-sdk-path"], self.log_lines())

    def test_empty_explicit_sdk_is_rejected_without_xcrun(self):
        result = self.run_script("build.sh", sdk="")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Requested macOS SDK is empty", result.stderr)
        self.assertIn("STUDIOHUB_MACOS_SDK", result.stderr)
        self.assertEqual([], self.log_lines())

    def test_lifecycle_failure_stops_build(self):
        result = self.run_script("build.sh", lifecycle_exit=23)

        self.assertEqual(23, result.returncode)
        self.assertEqual(
            ["native-boundary-tests", "native-api-tests", "native-lifecycle-tests"],
            self.runs.read_text().splitlines(),
        )


if __name__ == "__main__":
    unittest.main()
