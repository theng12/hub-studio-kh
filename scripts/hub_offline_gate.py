"""Serialized Hub gates with retained empty data/model roots before imports.

The per-release gate runner, now a repo script so every release does not copy one under
`.build/`. It is the accepted hub-offline-gate-201/202 runner: isolation, selectors,
retained empty data/model roots, fixture scoping, the ProgressResult class and the
GATE RESULT line are byte-identical. Only the root, the evidence dir and the Python
interpreters are parameterised.

The `unittest` stage runs in the launching interpreter, so launch it with the gate
Python (`.build/hub-gate/bin/python`); the other stages pick their interpreter from
`--root/.build` (`HUB_GATE_PYTHON` / `HUB_VOICE_PYTHON` override).

Before any evidence directory is created, `preflight()` refuses a drifted gate
environment: a missing interpreter, the unittest stage running in a different
environment (compared by the selected interpreter's own `sys.prefix`, because
different venvs often resolve to the same base binary), the full-suite seeds
(soundfile, mlx.core, misaki.espeak, espeakng_loader and their native assets), a
gate closure that does not match `packaging/hub-gate/requirements.lock.txt`, or a
pytest stage that cannot import pytest through the `.build/test-deps` overlay the
runner now prepends itself. Every probe is bounded; a hung interpreter is a loud
preflight failure, not a silent wait.

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
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def gate_python(root: Path) -> str:
    return os.environ.get('HUB_GATE_PYTHON') or str(root / '.build' / 'hub-gate' / 'bin' / 'python')


def voice_python(root: Path) -> str:
    return os.environ.get('HUB_VOICE_PYTHON') or str(root / '.build' / 'voice-runtime' / 'bin' / 'python3')


# The gate environment's required closure: exact pins from the verified R7 reconstruction
# (_orchestra/hub-lead/next-batch-20261105/gate-r7-environment.json). More distributions
# may be installed; these must be present at these versions.
GATE_LOCK = Path('packaging') / 'hub-gate' / 'requirements.lock.txt'
# Modules the full unittest stage imports directly; the two path assertions cover the
# espeak native assets that a successful import alone does not prove. tokenizers is the
# Vision helper Florence2 imports when the shipped checkpoints are installed (R7 error:
# test_vision_worker.ShippedModelTests -> florence.py `from tokenizers import Tokenizer`).
GATE_SEEDS = ('soundfile', 'mlx.core', 'misaki.espeak', 'espeakng_loader', 'tokenizers')
GATE_SEED_CHECK = (
    'import soundfile, mlx.core, misaki.espeak, espeakng_loader, tokenizers\n'
    'from pathlib import Path\n'
    'assert Path(espeakng_loader.get_library_path()).is_file(), "espeak library missing"\n'
    'assert Path(espeakng_loader.get_data_path()).is_dir(), "espeak data missing"\n'
)
DISTRIBUTION_PROBE = (
    'import json\n'
    'from importlib import metadata\n'
    'print(json.dumps({d.metadata["Name"]: d.version for d in metadata.distributions()'
    ' if d.metadata["Name"]}))\n'
)
# A venv's own identity: two venvs can symlink the same base binary, so the
# resolved executable cannot tell them apart - sys.prefix can.
INTERPRETER_PROBE = (
    'import json, sys\n'
    'print(json.dumps({"prefix": sys.prefix, "executable": sys.executable}))\n'
)
PROBE_TIMEOUT_SECONDS = 60


def _preflight_fail(message: str):
    raise SystemExit('GATE PREFLIGHT: ' + message)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else 'no output'


def _run_probe(python, code: str, env=None, isolated: bool = True, label: str = 'gate probe'):
    command = [str(python)] + (['-I'] if isolated else []) + ['-B', '-c', code]
    try:
        return subprocess.run(command, capture_output=True, text=True, env=env,
                              timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _preflight_fail(
            f'{label} did not finish within {PROBE_TIMEOUT_SECONDS}s: {python}')


def test_deps_env(root: Path, env: dict) -> dict:
    """Prepend this checkout's pytest overlay; a caller's own PYTHONPATH is kept."""
    overlay = str(root / '.build' / 'test-deps')
    existing = env.get('PYTHONPATH')
    merged = dict(env)
    merged['PYTHONPATH'] = overlay + (os.pathsep + existing if existing else '')
    return merged


def check_interpreter(python, label: str) -> None:
    if not Path(python).is_file():
        _preflight_fail(f'{label} interpreter missing: {python}')


def refuse_different_environment(running_prefix: str, selected_prefix: str, selected) -> None:
    if Path(running_prefix).resolve() != Path(selected_prefix).resolve():
        _preflight_fail(
            f'unittest runs in a different environment; running prefix {running_prefix}, '
            f'selected gate prefix {selected_prefix} ({selected}); '
            'set HUB_GATE_PYTHON to choose another')


def check_gate_selection(root: Path) -> None:
    selected = gate_python(root)
    completed = _run_probe(selected, INTERPRETER_PROBE, label='interpreter identity')
    if completed.returncode:
        _preflight_fail(
            f'cannot read the selected gate interpreter identity from {selected}: '
            f'{_last_line(completed.stderr)}')
    try:
        identity = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _preflight_fail(f'{selected} returned unreadable interpreter identity')
    refuse_different_environment(sys.prefix, identity.get('prefix', ''), selected)


def check_gate_seeds(python) -> None:
    completed = _run_probe(python, GATE_SEED_CHECK, label='gate seed import')
    if completed.returncode:
        _preflight_fail(
            f'{python} lacks the full-suite seeds ({", ".join(GATE_SEEDS)}) or their native '
            f'assets: {_last_line(completed.stderr)}')


def _normalize_distribution(name: str) -> str:
    return name.lower().replace('_', '-').replace('.', '-')


def parse_lock_text(text: str) -> dict:
    required = {}
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        name, separator, version = line.partition('==')
        if not separator or not name.strip() or not version.strip():
            _preflight_fail(f'gate lock line is not name==version: {line}')
        required[_normalize_distribution(name)] = version.strip()
    return required


def compare_distributions(required: dict, installed: dict):
    normalized = {_normalize_distribution(name): version for name, version in installed.items()}
    missing = [f'{name}=={version}' for name, version in sorted(required.items())
               if name not in normalized]
    mismatched = [f'{name}: required {version}, installed {normalized[name]}'
                  for name, version in sorted(required.items())
                  if name in normalized and normalized[name] != version]
    return missing, mismatched


def check_gate_closure(python, lock_path: Path) -> None:
    if not lock_path.is_file():
        _preflight_fail(f'tracked gate closure missing: {lock_path}')
    required = parse_lock_text(lock_path.read_text(encoding='utf-8'))
    completed = _run_probe(python, DISTRIBUTION_PROBE, label='distribution probe')
    if completed.returncode:
        _preflight_fail(
            f'cannot read installed distributions from {python}: {_last_line(completed.stderr)}')
    try:
        installed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _preflight_fail(f'{python} returned unreadable distribution metadata')
    missing, mismatched = compare_distributions(required, installed)
    if missing or mismatched:
        details = []
        if missing:
            details.append('missing ' + ', '.join(missing))
        if mismatched:
            details.append('mismatched ' + ', '.join(mismatched))
        _preflight_fail(f'gate closure does not match {lock_path}: ' + '; '.join(details))


def check_pytest(python, env: dict) -> None:
    completed = _run_probe(python, 'import pytest', env=env, isolated=False,
                           label='pytest import')
    if completed.returncode:
        _preflight_fail(
            f'{python} cannot import pytest through the test-deps overlay '
            f'({env.get("PYTHONPATH", "")}): {_last_line(completed.stderr)}')


def preflight(root: Path, kind: str) -> None:
    """Refuse a drifted gate environment before any evidence directory is created."""
    if kind == 'native':
        return
    if kind == 'voice':
        python = voice_python(root)
        check_interpreter(python, 'voice')
        check_pytest(python, test_deps_env(root, os.environ.copy()))
        print(f'GATE PREFLIGHT: ok ({kind}, {python})', flush=True)
        return
    python = gate_python(root)
    check_interpreter(python, 'gate')
    if kind == 'unittest':
        check_gate_selection(root)
        check_gate_seeds(python)
    if kind in ('regressions', 'pytest'):
        check_pytest(python, test_deps_env(root, os.environ.copy()))
    check_gate_closure(python, root / GATE_LOCK)
    print(f'GATE PREFLIGHT: ok ({kind}, {python})', flush=True)


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
    preflight(root, args.kind)
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
        pending = [thread for thread in threading.enumerate()
                   if thread is not threading.main_thread() and not thread.daemon]
        if pending:
            print('THREADS AT SUITE END:', [(thread.name, thread.ident) for thread in pending],
                  flush=True)
            faulthandler.dump_traceback()
        def shutdown_timed_out():
            print('SHUTDOWN TIMEOUT: tests finished but the interpreter did not exit in 120s; gate failed',
                  file=sys.stderr, flush=True)
            print('THREADS AT SHUTDOWN:', [(thread.name, thread.ident, thread.daemon)
                                          for thread in threading.enumerate()],
                  file=sys.stderr, flush=True)
            faulthandler.dump_traceback()
            os._exit(1)
        watchdog = threading.Timer(120, shutdown_timed_out)
        watchdog.daemon = True
        watchdog.start()
        sys.exit(0 if result.wasSuccessful() else 1)
    commands = {
        'regressions': [gate_python(root), '-m', 'pytest', '-p', 'no:cacheprovider', '-q', 'tests/hub_regression/cases'],
        'voice': [voice_python(root), '-m', 'pytest', '-p', 'no:cacheprovider', '-q', 'components/voice/app/tests'],
        'native': ['bash', 'native/build.sh'],
        'pytest': [gate_python(root), '-m', 'pytest', '-p', 'no:cacheprovider', '-q', *args.selectors],
    }
    stage_env = os.environ.copy()
    if args.kind in ('regressions', 'voice', 'pytest'):
        stage_env = test_deps_env(root, stage_env)
    result = subprocess.run(commands[args.kind], cwd=root, env=stage_env)
    print('GATE EXIT:', result.returncode, flush=True)
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
