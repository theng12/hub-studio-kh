"""Serialized Hub gates with retained empty data/model roots before imports.

The per-release gate runner, now a repo script so every release does not copy one under
`.build/`. It is the accepted hub-offline-gate-201/202 runner: isolation, selectors,
retained empty data/model roots, fixture scoping, the ProgressResult class and the
GATE RESULT line are byte-identical. Only the root, the evidence dir and the Python
interpreters are parameterised.

The `unittest` stage runs in the launching interpreter, so launch it with the gate
Python (`.build/hub-gate/bin/python`); the other stages pick their interpreter from
`--root/.build` (`HUB_GATE_PYTHON` / `HUB_VOICE_PYTHON` override).

Usage:
  .build/hub-gate/bin/python scripts/hub_offline_gate.py unittest \
      --evidence .build/gate-evidence-<version>
  .build/hub-gate/bin/python scripts/hub_offline_gate.py regressions \
      --evidence .build/gate-evidence-<version>
  ... kind one of: unittest, regressions, voice, native, pytest

It refuses an evidence directory that already exists, so a failed run is never
mistaken for this run's evidence.
"""
from pathlib import Path
import argparse
import glob
import faulthandler
import os
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def gate_python(root: Path) -> str:
    return os.environ.get('HUB_GATE_PYTHON') or str(root / '.build' / 'hub-gate' / 'bin' / 'python')


def voice_python(root: Path) -> str:
    return os.environ.get('HUB_VOICE_PYTHON') or str(root / '.build' / 'voice-runtime' / 'bin' / 'python3')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('unittest', 'regressions', 'voice', 'native', 'pytest'))
    parser.add_argument('selectors', nargs='*',
                        help='unittest/pytest selectors (test id prefixes)')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT,
                        help='checkout to gate (default: this script\'s repo root)')
    parser.add_argument('--evidence', type=Path, required=True,
                        help='evidence directory; must not exist yet')
    args = parser.parse_args()

    root, evidence = args.root.resolve(), args.evidence.resolve()
    if evidence.exists():
        raise SystemExit(f'refusing to reuse existing evidence dir: {evidence}')

    faulthandler.enable()
    faulthandler.dump_traceback_later(180, repeat=True)
    print(f"Owned runner PID: {os.getpid()}", flush=True)
    scratch = evidence / 'hub-gate-state' / (args.kind + '-' + uuid.uuid4().hex)
    data, models = scratch / 'data', scratch / 'models'
    data.mkdir(parents=True)
    models.mkdir()
    workers = scratch / 'private-workers.json'
    workers.write_text('[]\n')
    os.environ.update({
        'STUDIOHUB_DATA_DIR': str(data),
        'STUDIOHUB_MODELS_DIR': str(models),
        'STUDIOHUB_WORKERS_FILE': str(workers),
        'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
    })
    os.chdir(root)
    sys.path.insert(0, str(root))
    print(f'GATE {args.kind}: retained data={data}; empty models={models}', flush=True)
    if args.kind == 'unittest':
        real_glob = glob.glob
        owner_models = str(Path.home() / 'StudioHubModels')
        suppressed = []
        def isolated_glob(pattern, *args, **kwargs):
            if str(pattern).startswith(owner_models + '/'):
                suppressed.append(str(pattern))
                return []
            return real_glob(pattern, *args, **kwargs)
        with patch.object(glob, 'glob', side_effect=isolated_glob):
            suite = unittest.defaultTestLoader.discover('tests', pattern='test_*.py')
        print('Owner model lookup isolated during discovery:', suppressed, flush=True)
        selectors = tuple(args.selectors)
        if selectors:
            def selected_cases(candidate):
                for case in candidate:
                    if isinstance(case, unittest.TestSuite):
                        yield from selected_cases(case)
                    elif any(case.id().startswith(selector) for selector in selectors):
                        yield case
            suite = unittest.TestSuite(selected_cases(suite))
            if suite.countTestCases() == 0:
                raise ValueError(f'no tests matched selectors: {selectors}')
            print(f'FOCUSED UNITTEST: selectors={selectors}; cases={suite.countTestCases()}', flush=True)
        fixture_classes = {
            ("test_chat_hub_wiring", "ChatCompletionJobTests"),
            ("test_chat_hub_wiring", "ChatHubForwardingTests"),
            ("test_chat_hub_wiring", "PeerLeaseDisconnectTests"),
            ("test_standalone_connections", "FleetCancelTests"),
            ("test_standalone_connections", "FleetDispatchTests"),
            ("test_standalone_connections", "FleetNodeDeathTests"),
            ("test_standalone_connections", "FleetPinTests"),
            ("test_standalone_connections", "FleetSubtitleDispatchTests"),
        }
        found = set()
        def scope_fixture(cls):
            original_run = cls.run
            def run(case, result=None):
                with patch.dict(os.environ, {"STUDIOHUB_MODELS_DIR": ""}):
                    return original_run(case, result)
            cls.run = run
        def inspect_suite(selected):
            for case in selected:
                if isinstance(case, unittest.TestSuite):
                    inspect_suite(case)
                    continue
                cls = type(case)
                key = (cls.__module__, cls.__name__)
                if key in fixture_classes and key not in found:
                    scope_fixture(cls)
                    found.add(key)
        inspect_suite(suite)
        expected_fixture_classes = fixture_classes if not selectors else {
            key for key in fixture_classes
            if any(f'{key[0]}.{key[1]}.'.startswith(selector)
                   for selector in selectors)
        }
        assert found == expected_fixture_classes, (
            "fixture class moved", expected_fixture_classes - found, found - expected_fixture_classes)
        for module, name in sorted(found):
            print(f'FIXTURE ISOLATION: {module}.{name}: fake worker readiness, no configured model library', flush=True)
        class ProgressResult(unittest.TextTestResult):
            def startTest(self, test):
                self.stream.writeln(f"START: {test.id()}")
                self.stream.flush()
                super().startTest(test)
            def addError(self, test, error):
                self.stream.writeln(f'\nERROR NOW: {test.id()}')
                self.stream.flush()
                super().addError(test, error)
            def addFailure(self, test, error):
                self.stream.writeln(f'\nFAILURE NOW: {test.id()}')
                self.stream.flush()
                super().addFailure(test, error)
        result = unittest.TextTestRunner(verbosity=1, resultclass=ProgressResult).run(suite)
        for test, reason in result.skipped:
            print('SKIP:', test.id(), reason, flush=True)
        print(f'GATE RESULT: run={result.testsRun} skipped={len(result.skipped)} failures={len(result.failures)} errors={len(result.errors)}', flush=True)
        sys.exit(0 if result.wasSuccessful() else 1)
    commands = {
        'regressions': [gate_python(root), '-m', 'pytest', '-p', 'no:cacheprovider', '-q', 'tests/hub_regression/cases'],
        'voice': [voice_python(root), '-m', 'pytest', '-p', 'no:cacheprovider', '-q', 'components/voice/app/tests'],
        'native': ['bash', 'native/build.sh'],
        'pytest': [gate_python(root), '-m', 'pytest', '-p', 'no:cacheprovider', '-q', *args.selectors],
    }
    result = subprocess.run(commands[args.kind], cwd=root, env=os.environ.copy())
    print('GATE EXIT:', result.returncode, flush=True)
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
