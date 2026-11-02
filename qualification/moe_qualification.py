#!/usr/bin/env python3
"""Portable offline preflight and live launcher for one pinned MoE qualification."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
from typing import Any


GIB = 1024 ** 3
DEFAULT_MAX_FOOTPRINT_GIB = 26.0
LOCK_PATH = Path.home() / "Library/Application Support/Studio Hub/moe-llm-qualification.lock"


class QualificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo_id: str
    revision: str
    model_type: str
    display_name: str


MODELS = {
    row.name: row for row in (
        ModelSpec("nemotron-lightning-4bit", "mlx-community/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-4bit",
                  "55ac8c89261109b36c04371cd3f479a4594208c8", "nemotron_h",
                  "NVIDIA Nemotron 3.5 Lightning 30B-A3B MLX 4-bit"),
        ModelSpec("qwen36-35b-4bit", "mlx-community/Qwen3.6-35B-A3B-4bit",
                  "38740b847e4cb78f352aba30aa41c76e08e6eb46", "qwen3_5_moe",
                  "Qwen3.6 35B-A3B MLX 4-bit"),
        ModelSpec("gemma4-26b-a4b-qat-4bit", "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
                  "0e3cbab38ce568cf6e23543010d08d03b731910c", "gemma4",
                  "Gemma 4 26B-A4B QAT MLX 4-bit"),
    )
}


def resolve_model_dir(root: Path, spec: ModelSpec) -> Path:
    ssd = root / "model-library" / spec.name / spec.revision / "model"
    local = root / "models" / spec.name
    for candidate in (ssd, local):
        if candidate.is_dir():
            return candidate
    raise QualificationError(f"exact pinned model is missing: {ssd} (local-study fallback: {local})")


def check_model(model_dir: Path, spec: ModelSpec) -> dict[str, Any]:
    config_path = model_dir / "config.json"
    index_path = model_dir / "model.safetensors.index.json"
    if not config_path.is_file():
        raise QualificationError(f"model config is missing: {config_path}")
    if not index_path.is_file():
        raise QualificationError(f"weight index is missing: {index_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"model metadata cannot be read: {exc}") from exc
    if config.get("model_type") != spec.model_type:
        raise QualificationError(
            f"model_type mismatch in {config_path}: expected {spec.model_type}, got {config.get('model_type')}")
    quant = config.get("quantization") or config.get("quantization_config") or {}
    if quant.get("bits") != 4:
        raise QualificationError(f"expected the selected MLX 4-bit checkpoint in {config_path}")
    expected = {Path(name).name for name in index.get("weight_map", {}).values()
                if str(name).endswith(".safetensors")}
    actual = {path.name for path in model_dir.glob("*.safetensors") if path.is_file() and path.stat().st_size > 0}
    if not expected or expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise QualificationError(f"weight shard set does not match {index_path}; missing={missing}, extra={extra}")
    return {"config": str(config_path), "weight_index": str(index_path), "weight_shards": len(actual)}


def check_runtime(runtime_python: Path, spec: ModelSpec) -> dict[str, Any]:
    if not runtime_python.is_file() or not os.access(runtime_python, os.X_OK):
        raise QualificationError(f"Studio Hub voice runtime Python is missing or not executable: {runtime_python}")
    runtime = runtime_python.parent.parent
    sites = sorted((runtime / "lib").glob("python*/site-packages"))
    if not sites:
        raise QualificationError(f"Studio Hub voice runtime site-packages is missing under: {runtime / 'lib'}")
    packages = {str(dist.metadata.get("Name", "")).casefold(): dist.version
                for dist in importlib.metadata.distributions(path=[str(sites[0])])}
    required: dict[str, str] = {}
    for name in ("mlx", "mlx-lm", "transformers"):
        version = packages.get(name)
        if not version:
            raise QualificationError(f"runtime package metadata is missing for {name}: {sites[0]}")
        required[name] = version
    implementation = sites[0] / "mlx_lm" / "models" / f"{spec.model_type}.py"
    if not implementation.is_file():
        raise QualificationError(f"runtime model implementation source is missing: {implementation}")
    return {"runtime_python": str(runtime_python), "runtime_packages": required,
            "model_implementation": str(implementation)}


def check_helpers(technical: Path) -> dict[str, str]:
    repo_host_memory = technical.parent / "components" / "host_memory.py"
    host_memory = (repo_host_memory if repo_host_memory.is_file()
                   else technical / "moe-support" / "components" / "host_memory.py")
    paths = {
        "probe": technical / "moe_probe.py",
        "prompts": technical / "moe-prompts.json",
        "selfcheck": technical / "moe_probe_selfcheck.py",
        "host_memory": host_memory,
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise QualificationError("qualification helper is missing: " + ", ".join(missing))
    try:
        json.loads(paths["prompts"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"prompt file cannot be read: {exc}") from exc
    return {name: str(path) for name, path in paths.items()}


def physical_ram_bytes() -> int | None:
    try:
        value = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True,
                               text=True, check=False).stdout.strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


def static_check(spec: ModelSpec, kit_root: Path, technical: Path,
                 runtime_python: Path) -> dict[str, Any]:
    model_dir = resolve_model_dir(kit_root, spec)
    result = {"model": spec.name, "repo_id": spec.repo_id, "revision": spec.revision,
              "model_dir": str(model_dir), "physical_ram_bytes": physical_ram_bytes()}
    result.update(check_model(model_dir, spec))
    result.update(check_runtime(runtime_python, spec))
    result["helpers"] = check_helpers(technical)
    return result


def acquire_lock(path: Path = LOCK_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise QualificationError("another Studio Hub LLM qualification is already running on this Mac") from exc
    return handle


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_owned_process_group(process, *, killpg=os.killpg, group_exists=_group_exists,
                             timeout: float = 10.0) -> None:
    try:
        killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return
    if group_exists(process.pid):
        try:
            killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _value(value: Any, suffix: str = "") -> str:
    return "unknown" if value is None else f"{value}{suffix}"


def _bytes(value: Any) -> str:
    return "unknown" if not isinstance(value, (int, float)) else f"{value / GIB:.2f} GiB"


def summary_text(report: dict[str, Any], spec: ModelSpec) -> str:
    cases = report.get("cases") or []
    mechanical = report.get("generation_mechanical_pass")
    review = report.get("subjective_writing_review")
    environment = report.get("environment") or {}
    tier = environment.get("physical_ram_tier_gib")
    cold_load = report.get("cold_load") or {}
    initial_health = (report.get("preflight") or {}).get("health") or {}
    final_health = report.get("run_health_final") or {}
    prompt_values = [row.get("prompt_tokens_measured") for row in cases]
    completion_values = [row.get("completion_tokens") for row in cases]
    prompt_total = (sum(prompt_values) if prompt_values and
                    all(isinstance(value, int) for value in prompt_values) else None)
    completion_total = (sum(completion_values) if completion_values and
                        all(isinstance(value, int) for value in completion_values) else None)
    peaks = [value for row in cases for value in (row.get("mlx_peak_bytes"),)
             if isinstance(value, (int, float))]
    cold_peak = (report.get("cold_load") or {}).get("mlx_peak_bytes")
    if isinstance(cold_peak, (int, float)):
        peaks.append(cold_peak)
    footprints = [report.get("parent_observed_peak_phys_footprint_bytes"),
                  cold_load.get("kernel_phys_footprint_peak_bytes")]
    footprints += [row.get("kernel_phys_footprint_peak_bytes") for row in cases]
    footprints = [value for value in footprints if isinstance(value, (int, float))]
    lines = [
        "Studio Hub LLM Qualification Summary", "",
        f"Model: {spec.display_name}", f"Repository: {spec.repo_id}", f"Revision: {spec.revision}",
        f"Status: {report.get('status', 'unknown')}",
        f"Mechanical checks: {'PASS' if mechanical is True else 'FAIL' if mechanical is False else 'UNKNOWN'}",
        f"Subjective writing quality: {'PENDING' if review in (None, 'not_performed') else str(review).upper()}",
        f"Physical tier only: {_value(tier, ' GiB')}",
        "Tier scope: this physical host only; no lower-memory tier is inferred.", "",
        f"Total measured prompt tokens: {_value(prompt_total)}",
        f"Total completion tokens: {_value(completion_total)}",
        f"First load in fresh process: {_value(cold_load.get('seconds'), ' seconds')}",
        f"Available memory at start: {_bytes(environment.get('available_memory_bytes_at_start'))}",
        f"Initial memory pressure: {_value(initial_health.get('pressure_name'))}",
        f"Final memory pressure: {_value(final_health.get('pressure_name'))}",
        f"Final swap growth: {_bytes(final_health.get('swap_delta_bytes'))}",
        f"Peak MLX memory: {_bytes(max(peaks) if peaks else None)}",
        f"Largest observed process footprint: {_bytes(max(footprints) if footprints else None)}",
    ]
    capacity = report.get("scene_batch_capacity")
    if isinstance(capacity, dict):
        lines += [
            f"Largest passing tested scene batch: {_value(capacity.get('largest_passing_tested_batch_size'))}",
            "Failed scene batch sizes: " + (", ".join(str(n) for n in capacity.get("failed_batch_sizes", [])) or "none"),
            "Batch scope: screening results; repeated production reliability is not established.",
        ]
    stop_rule = report.get("stop_rule")
    if isinstance(stop_rule, dict):
        lines += [
            "",
            "Long-Context Stop Rule Evaluation:",
            f"  Suite: {report.get('suite', 'long-context')}",
            f"  Peak footprint stop threshold: {stop_rule.get('max_footprint_gib', DEFAULT_MAX_FOOTPRINT_GIB):.2f} GiB",
            f"  Last safe context size: {stop_rule.get('last_safe_size', 'unknown')}",
            f"  Stop reason: {stop_rule.get('stop_reason', 'none')}",
            f"  Stopped early: {'YES' if stop_rule.get('stopped_early') else 'NO'}",
        ]
    if report.get("error"):
        lines.append(f"Error: {report['error']}")
    if report.get("inconclusive_reason"):
        lines.append(f"Inconclusive reason: {report['inconclusive_reason']}")
    for row in cases:
        lines += ["", f"Case: {row.get('name', 'unknown')}",
                  f"  Mechanical: {'PASS' if row.get('mechanical_pass') is True else 'FAIL' if row.get('mechanical_pass') is False else 'UNKNOWN'}",
                  f"  Answer complete: {'YES' if row.get('answer_complete') is True else 'NO' if row.get('answer_complete') is False else 'UNKNOWN'}",
                  f"  Finish reason: {_value(row.get('finish_reason'))}",
                  f"  Elapsed: {_value(row.get('elapsed_seconds'), ' seconds')}",
                  f"  Prefill time: {_value(row.get('prefill_seconds'), ' seconds')}",
                  f"  Generate time: {_value(row.get('generate_seconds'), ' seconds')}",
                  f"  Prompt tokens: {_value(row.get('prompt_tokens_measured'))}",
                  f"  Completion tokens: {_value(row.get('completion_tokens'))}",
                  f"  Prompt tokens/s: {_value(row.get('prompt_tps'))}",
                  f"  Decode tokens/s: {_value(row.get('decode_tps'))}",
                  f"  Peak MLX memory: {_bytes(row.get('mlx_peak_bytes'))}",
                  f"  Peak process footprint: {_bytes(row.get('kernel_phys_footprint_peak_bytes'))}",
                  f"  Memory pressure: {_value(row.get('memory_pressure'))}",
                  f"  Swap delta: {_bytes(row.get('swap_delta_bytes'))}",
                  f"  Free memory at start: {_bytes(row.get('free_memory_at_start_bytes'))}"]
        validation = row.get("validation") or {}
        if "sections_count" in validation:
            needle_status = "recalled" if validation.get("needle_found") else "MISSING"
            lines.append(f"  Sections: {validation.get('sections_count')}/{validation.get('expected_sections')}; needle '{validation.get('needle')}': {needle_status}")
        for chapter in validation.get("chapters", []):
            number = chapter.get("number")
            label = f"{number:02d}" if isinstance(number, int) else "unknown"
            verdict = "PASS" if chapter.get("pass") is True else "FAIL" if chapter.get("pass") is False else "UNKNOWN"
            lines.append(f"  Chapter {label}: {_value(chapter.get('word_count'))} words ({verdict})")
        lengths = validation.get("length_stats")
        if isinstance(lengths, dict):
            lines += [
                f"  Scene prompts: {_value(validation.get('count'))}; characters min/mean/max: "
                f"{_value(lengths.get('min_chars'))} / {_value(lengths.get('mean_chars'))} / {_value(lengths.get('max_chars'))}",
                f"  Character cap: {_value(lengths.get('cap'))}; over cap: {_value(lengths.get('over_cap_count'))}",
            ]
        for error in validation.get("errors", []):
            lines.append(f"  Validation: {error}")
    return "\n".join(lines) + "\n"


def _result_dir(output_root: Path, spec: ModelSpec) -> Path:
    host = re.sub(r"[^A-Za-z0-9._-]+", "-", platform.node()).strip("-") or "unknown-host"
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S-%f")
    return output_root / f"{spec.name}-{host}-{stamp}"


def run_live(args: argparse.Namespace, spec: ModelSpec) -> int:
    lock = acquire_lock()
    result_dir = _result_dir(args.output_root, spec)
    try:
        result_dir.mkdir(parents=True)
    except Exception:
        lock.close()
        raise
    log_path, summary_path = result_dir / "run.log", result_dir / "SUMMARY.txt"
    report: dict[str, Any] = {"status": "launcher_failure", "suite": args.suite, "cases": []}
    child = None
    launcher_override: dict[str, Any] | None = None
    try:
        check = static_check(spec, args.kit_root, args.technical_root, args.runtime_python)
        model_dir = Path(check["model_dir"])
        print(f"Model: {spec.display_name}\nExact revision: {spec.revision}")
        print(f"Physical RAM: {_bytes(check['physical_ram_bytes'])}")
        if args.suite == "long-context":
            print("Suite: long-context (evaluating 48K and 64K input tokens)")
            print(f"Peak footprint stop threshold: {args.max_footprint_gb:.2f} GiB (override with --max-footprint-gb)")
            print("Stop rule: stop before next size if swap grows or peak footprint exceeds threshold")
        print("WARNING: this run measures only this physical Mac; writing quality review remains required.")
        print("It never enables the model or infers support for a smaller memory tier.")
        hub_source = Path(check["helpers"]["host_memory"]).parents[1]
        command = [str(args.runtime_python), "-B", str(args.technical_root / "moe_probe.py"),
                   "--model-dir", str(model_dir), "--output-dir", str(result_dir),
                   "--suite", args.suite, "--hub-source", str(hub_source),
                   "--max-footprint-gb", str(args.max_footprint_gb)]
        with log_path.open("w", encoding="utf-8") as log:
            log.write(json.dumps(check, indent=2) + "\n")
            log.flush()
            env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       PYTHONDONTWRITEBYTECODE="1")
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, start_new_session=True, env=env)
            assert child.stdout is not None
            for line in child.stdout:
                print(line, end="")
                log.write(line)
                log.flush()
            status = child.wait()
    except (KeyboardInterrupt, SystemExit):
        launcher_override = {
            "status": "cancelled",
            "error": "qualification cancelled; only the owned probe process group was stopped",
        }
        status = 130
    except Exception as exc:
        launcher_override = {"status": "launcher_failure", "error": f"{type(exc).__name__}: {exc}"}
        status = 3
    finally:
        try:
            if child is not None:
                stop_owned_process_group(child)
        finally:
            lock.close()
    report_path = result_dir / "report.json"
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report = {"status": "launcher_failure", "error": f"report unreadable: {exc}", "cases": []}
    elif not launcher_override:
        report["error"] = f"probe exited with status {status} without writing report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if status == 0:
            status = 3
    if launcher_override:
        report.update(launcher_override)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(launcher_override["error"] + "\n")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(summary_text(report, spec), encoding="utf-8")
    print(f"Results: {result_dir}\nSummary: {summary_path}")
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(MODELS), required=True)
    parser.add_argument("--kit-root", type=Path, required=True)
    parser.add_argument("--technical-root", type=Path, required=True)
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--output-root", type=Path,
                        default=Path.home() / "Desktop" / "StudioHub LLM Qualification")
    parser.add_argument("--suite", choices=("smoke", "full", "long-context"), default="full")
    parser.add_argument("--max-footprint-gb", type=float, default=DEFAULT_MAX_FOOTPRINT_GIB,
                        help=f"Peak physical footprint limit in GiB before stopping subsequent sizes (default: {DEFAULT_MAX_FOOTPRINT_GIB:.1f})")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    for name in ("kit_root", "technical_root", "runtime_python", "output_root"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = MODELS[args.model]
    if args.check:
        try:
            check = static_check(spec, args.kit_root, args.technical_root, args.runtime_python)
        except QualificationError as exc:
            print(f"CHECK FAILED: {exc}", file=sys.stderr)
            return 3
        print(json.dumps(check, indent=2))
        print("CHECK PASSED: static files only; MLX/model/tokenizer were not imported or loaded.")
        return 0
    def interrupt(_signum, _frame) -> None:
        raise KeyboardInterrupt

    old_term = signal.signal(signal.SIGTERM, interrupt)
    old_hup = signal.signal(signal.SIGHUP, interrupt)
    try:
        try:
            return run_live(args, spec)
        except QualificationError as exc:
            print(f"NOT MEASURED: {exc}", file=sys.stderr)
            return 3
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGHUP, old_hup)


if __name__ == "__main__":
    raise SystemExit(main())
