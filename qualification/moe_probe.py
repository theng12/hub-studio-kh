#!/usr/bin/env python3
r"""Standalone, disposable MLX-LM qualification probe; no Studio service wiring.

Run with Studio Hub's packaged interpreter:

  /Applications/Studio\ Hub.app/Contents/Resources/runtimes/voice/bin/python3 \
    probe.py --model-dir /path/to/local/model --output-dir /path/to/new/results --suite full

The public process is a pressure watchdog. It launches a private worker and stops it if
macOS reports critical memory pressure or swap grows by more than 1 GiB from the run baseline.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any

GIB = 1024 ** 3
CACHE_LIMIT = 512_000_000
CACHE_LIMIT_SOURCE = (
    "configured: components/voice/app/backend/mlx_memory.py CACHE_LIMIT_MB=512; "
    "not measured RAM, throughput, or an admission floor"
)
SWAP_ABORT = GIB
WORKING_ROOM = 4 * GIB
PROMPTS = Path(__file__).with_name("moe-prompts.json")
HERE = Path(__file__).resolve().parent
DEFAULT_HUB_SOURCE = (HERE.parent if (HERE.parent / "components" / "host_memory.py").is_file()
                      else HERE / "moe-support")
REPORT = "report.json"
THINK_MARKERS = ("<think", "</think", "thinking process:")


class _Usage(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_uint8 * 16), ("user", ctypes.c_uint64),
        ("system", ctypes.c_uint64), ("idle_wakeups", ctypes.c_uint64),
        ("interrupt_wakeups", ctypes.c_uint64), ("pageins", ctypes.c_uint64),
        ("wired", ctypes.c_uint64), ("resident", ctypes.c_uint64),
        ("footprint", ctypes.c_uint64), ("pad", ctypes.c_uint64 * 40),
    ]


class _SwapUsage(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_uint64), ("avail", ctypes.c_uint64),
        ("used", ctypes.c_uint64), ("page_size", ctypes.c_uint32),
        ("encrypted", ctypes.c_int),
    ]


_LIBC = ctypes.CDLL(None)
_LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib") if sys.platform == "darwin" else None


def _sysctl(name: str, value: ctypes._SimpleCData | ctypes.Structure) -> bool:
    if sys.platform != "darwin":
        return False
    size = ctypes.c_size_t(ctypes.sizeof(value))
    return _LIBC.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) == 0


def sysctl_int(name: str) -> int | None:
    value = ctypes.c_uint64()
    return int(value.value) if _sysctl(name, value) else None


def swap_used() -> int | None:
    value = _SwapUsage()
    return int(value.used) if _sysctl("vm.swapusage", value) else None


def pressure_level() -> int | None:
    value = ctypes.c_int()
    return int(value.value) if _sysctl("kern.memorystatus_vm_pressure_level", value) else None


def footprint(pid: int | None = None) -> int | None:
    if _LIBPROC is None:
        return None
    usage = _Usage()
    if _LIBPROC.proc_pid_rusage(pid or os.getpid(), 4, ctypes.byref(usage)) != 0:
        return None
    return int(usage.footprint)


def available_memory(hub_source: Path = DEFAULT_HUB_SOURCE) -> int | None:
    """Use Studio Hub's one headroom calculation; it remains a warning, never qualification."""
    try:
        import psutil
        source = hub_source / "components" / "host_memory.py"
        spec = importlib.util.spec_from_file_location("studio_hub_host_memory", source)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return int(module.available_bytes(psutil.virtual_memory()))
    except (OSError, ImportError, AttributeError, ValueError):
        return None


def health(baseline_swap: int | None) -> dict[str, Any]:
    used = swap_used()
    level = pressure_level()
    return {
        "swap_used_bytes": used,
        "swap_delta_bytes": None if used is None or baseline_swap is None else used - baseline_swap,
        "pressure_level": level,
        "pressure_name": {1: "normal", 2: "warning", 4: "critical"}.get(level, "unknown"),
    }


def abort_reason(sample: dict[str, Any]) -> str | None:
    if sample["pressure_level"] == 4:
        return "kernel memory pressure became critical"
    delta = sample["swap_delta_bytes"]
    if delta is not None and delta > SWAP_ABORT:
        return f"swap grew by {delta} bytes, above the 1 GiB run limit"
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 ** 2), b""):
            digest.update(block)
    return digest.hexdigest()


def model_hashes(model_dir: Path) -> dict[str, Any]:
    weights = sorted(model_dir.glob("*.safetensors"))
    configs = [model_dir / name for name in (
        "config.json", "generation_config.json", "tokenizer_config.json",
        "tokenizer.json", "chat_template.jinja", "model.safetensors.index.json",
    ) if (model_dir / name).is_file()]
    weight_rows = [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in weights]
    config_rows = [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in configs]
    canonical = json.dumps(weight_rows, sort_keys=True, separators=(",", ":")).encode()
    return {
        "weights": weight_rows,
        "configs": config_rows,
        "weights_bytes": sum(row["bytes"] for row in weight_rows),
        "weights_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def resolve_model_dir(path: Path) -> Path:
    """Accept either a model folder or Studio Hub's package/revision/model folder."""
    if (path / "config.json").is_file():
        return path
    candidates = [config.parent for config in path.glob("*/model/config.json")]
    return candidates[0] if len(candidates) == 1 else path


def load_prompts(path: Path = PROMPTS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def has_think_spill(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in THINK_MARKERS)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE))


def validate_chapter(text: str, spec: dict[str, Any]) -> dict[str, Any]:
    words = word_count(text)
    missing = [fact for fact in spec["must_include"] if fact.casefold() not in text.casefold()]
    forbidden = [phrase for phrase in spec.get("must_not_include", []) if phrase.casefold() in text.casefold()]
    errors = []
    if not text.startswith("## Chapter 07: The Quiet Meridian"):
        errors.append("required heading is missing or not first")
    if not spec["minimum_words"] <= words <= spec["maximum_words"]:
        errors.append(f"word count {words} is outside {spec['minimum_words']}-{spec['maximum_words']}")
    if missing:
        errors.append("missing stable facts: " + ", ".join(missing))
    if forbidden:
        errors.append("contradicted constraints: " + ", ".join(forbidden))
    if has_think_spill(text):
        errors.append("thinking trace marker leaked into output")
    return {"pass": not errors, "word_count": words, "missing_facts": missing,
            "forbidden_phrases": forbidden, "errors": errors}


def validate_scene_json(text: str, expected_ids: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"pass": False, "count": 0, "ids": [], "errors": [f"strict JSON parse failed: {exc}"]}
    if not isinstance(data, dict) or set(data) != {"results"} or not isinstance(data.get("results"), list):
        return {"pass": False, "count": 0, "ids": [], "errors": ["expected exactly one top-level results array"]}
    rows = data["results"]
    ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"scene_id", "visual_prompt"}:
            errors.append(f"result {index} must contain exactly scene_id and visual_prompt")
            continue
        scene_id, prompt = row["scene_id"], row["visual_prompt"]
        if not isinstance(scene_id, str):
            errors.append(f"result {index} scene_id is not a string")
        else:
            ids.append(scene_id)
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"result {index} visual_prompt is empty")
    if len(rows) != len(expected_ids):
        errors.append(f"expected {len(expected_ids)} results, got {len(rows)}")
    if len(ids) != len(set(ids)):
        errors.append("scene_id values are not unique")
    if ids != expected_ids:
        errors.append("scene_id values or order do not exactly match the request")
    if has_think_spill(text):
        errors.append("thinking trace marker leaked into output")
    return {"pass": not errors, "count": len(rows), "ids": ids, "errors": errors}


def scene_messages(batch: dict[str, Any]) -> list[dict[str, str]]:
    expected = [{"scene_id": row[0], "narration": row[1]} for row in batch["scenes"]]
    system = (
        "You write visual scene descriptions for an AI image generator. Return only one valid JSON object, "
        "with no markdown fences, commentary, or text outside JSON. The object must be exactly "
        "{\"results\":[{\"scene_id\":\"...\",\"visual_prompt\":\"...\"}]}. Preserve every scene_id "
        "exactly and in order. Return exactly one nonempty visual_prompt per input. Each prompt is one concise "
        "English paragraph naming subject, action, setting, framing, lighting, and mood. Do not reveal hidden reasoning."
    )
    user = (
        f"Art style for every prompt: {batch['style']}\n"
        "Write all 12 prompts for these stable scene IDs and narration beats:\n"
        + json.dumps(expected, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def render_prompt(tokenizer: Any, messages: list[dict[str, str]]) -> str:
    apply = getattr(tokenizer, "apply_chat_template", None)
    if not callable(apply):
        raise RuntimeError("tokenizer has no apply_chat_template; qualification requires its native chat template")
    try:
        return apply(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError as exc:
        raise RuntimeError("chat template does not accept enable_thinking=False") from exc


def token_count(tokenizer: Any, prompt: str) -> int:
    encoded = tokenizer.encode(prompt)
    return len(encoded)


def padded_messages(tokenizer: Any, spec: dict[str, Any], target: int) -> tuple[list[dict[str, str]], int]:
    base = [dict(row) for row in spec["base_messages"]]
    paragraph = "\n" + spec["padding_paragraph"]

    def candidate(repeats: int) -> tuple[list[dict[str, str]], int]:
        messages = [dict(row) for row in base]
        messages[-1]["content"] += paragraph * repeats
        prompt = render_prompt(tokenizer, messages)
        return messages, token_count(tokenizer, prompt)

    one, one_tokens = candidate(1)
    zero, zero_tokens = candidate(0)
    per = max(1, one_tokens - zero_tokens)
    guess = max(1, math.ceil((target - zero_tokens) / per))
    low, high = 0, guess
    while candidate(high)[1] < target:
        low, high = high, high * 2
    while low + 1 < high:
        mid = (low + high) // 2
        if candidate(mid)[1] < target:
            low = mid
        else:
            high = mid
    options = [candidate(low), candidate(high)]
    return min(options, key=lambda item: abs(item[1] - target))


class FootprintSampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = footprint() or 0
        self.stopping = threading.Event()

    def run(self) -> None:
        while not self.stopping.wait(0.02):
            self.peak = max(self.peak, footprint() or 0)

    def reset(self) -> None:
        self.peak = footprint() or 0


def write_report(output_dir: Path, report: dict[str, Any]) -> None:
    target = output_dir / REPORT
    pending = output_dir / (REPORT + ".writing")
    pending.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(pending, target)


def environment_record(hub_source: Path = DEFAULT_HUB_SOURCE) -> dict[str, Any]:
    physical = sysctl_int("hw.memsize")
    return {
        "python": platform.python_version(),
        "executable": sys.executable,
        "libraries": {name: importlib.metadata.version(name) for name in ("mlx", "mlx-lm", "transformers")},
        "platform": platform.platform(),
        "chip": subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip(),
        "machine_model": subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True).stdout.strip(),
        "physical_ram_bytes": physical,
        "physical_ram_tier_gib": None if physical is None else round(physical / GIB),
        "available_memory_bytes_at_start": available_memory(hub_source),
        "available_memory_source": str(hub_source / "components" / "host_memory.py"),
    }


def generate_case(model: Any, tokenizer: Any, mx: Any, stream_generate: Any,
                  sampler_factory: Any, messages: list[dict[str, str]], max_tokens: int,
                  sampler: FootprintSampler, baseline_swap: int, output_path: Path) -> dict[str, Any]:
    prompt = render_prompt(tokenizer, messages)
    prompt_tokens_measured = token_count(tokenizer, prompt)
    mx.reset_peak_memory()
    sampler.reset()
    before = health(baseline_swap)
    started = time.monotonic()
    chunks: list[str] = []
    final = None
    generator = stream_generate(
        model, tokenizer, prompt=prompt, max_tokens=max_tokens,
        sampler=sampler_factory(temp=0.6, top_p=0.9),
    )
    try:
        for response in generator:
            final = response
            chunks.append(response.text)
            reason = abort_reason(health(baseline_swap))
            if reason:
                raise InterruptedError(reason)
    finally:
        generator.close()
    elapsed = time.monotonic() - started
    text = "".join(chunks)
    output_path.write_text(text, encoding="utf-8")
    after = health(baseline_swap)
    return {
        "output_file": output_path.name,
        "max_tokens": max_tokens,
        "prompt_tokens_measured": prompt_tokens_measured,
        "prompt_tokens_reported": getattr(final, "prompt_tokens", None),
        "completion_tokens": getattr(final, "generation_tokens", 0) if final else 0,
        "prompt_tps": getattr(final, "prompt_tps", None),
        "decode_tps": getattr(final, "generation_tps", None),
        "finish_reason": getattr(final, "finish_reason", None),
        "elapsed_seconds": round(elapsed, 3),
        "mlx_peak_bytes": int(mx.get_peak_memory()),
        "kernel_phys_footprint_peak_bytes": sampler.peak,
        "health_before": before,
        "health_after": after,
        "think_spill": has_think_spill(text),
    }


def run_worker(args: argparse.Namespace) -> int:
    output_dir = args.output_dir
    prompts = load_prompts()
    baseline_swap = int(os.environ.get("MOE_PROBE_BASELINE_SWAP", swap_used() or 0))
    report: dict[str, Any] = {
        "schema_version": 1, "status": "running", "suite": args.suite,
        "model_dir": str(args.model_dir), "started_at": time.time(),
        "environment": environment_record(args.hub_source), "cache_limit_bytes": CACHE_LIMIT,
        "cache_limit_source": CACHE_LIMIT_SOURCE,
        "prompts_sha256": sha256(PROMPTS),
        "swap_abort_delta_bytes": SWAP_ABORT, "subjective_writing_review": "not_performed",
        "cases": [],
    }
    write_report(output_dir, report)
    try:
        print("Verifying model files before load...", flush=True)
        report["model_files"] = model_hashes(args.model_dir)
        write_report(output_dir, report)
        print("Loading model in a fresh process...", flush=True)
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler

        mx.set_cache_limit(CACHE_LIMIT)
        mx.reset_peak_memory()
        sampler = FootprintSampler()
        sampler.start()
        sampler.reset()
        load_started = time.monotonic()
        model, tokenizer = load(str(args.model_dir), tokenizer_config={"trust_remote_code": False})
        report["cold_load"] = {
            "seconds": round(time.monotonic() - load_started, 3),
            "mlx_peak_bytes": int(mx.get_peak_memory()),
            "kernel_phys_footprint_peak_bytes": sampler.peak,
            "health_after": health(baseline_swap),
        }
        reason = abort_reason(report["cold_load"]["health_after"])
        if reason:
            raise InterruptedError(reason)
        write_report(output_dir, report)

        specs: list[tuple[str, list[dict[str, str]], int, str]] = [
            ("speed", prompts["speed"]["messages"], prompts["speed"]["max_tokens"], "benchmark"),
        ]
        if args.suite == "full":
            for target in (8192, 32768):
                messages, _ = padded_messages(tokenizer, prompts["padded"], target)
                specs.append((f"input-{target // 1024}k", messages, prompts["padded"]["max_tokens"], "padded_benchmark"))
        specs.append(("chapter", prompts["chapter"]["messages"], prompts["chapter"]["max_tokens"], "quality"))
        batches = prompts["scene_batches"] if args.suite == "full" else prompts["scene_batches"][:1]
        specs.extend((f"scenes-{batch['batch_id']}", scene_messages(batch), 2200, "quality") for batch in batches)

        for name, messages, budget, purpose in specs:
            print(f"running {name}", flush=True)
            mx.random.seed(7)
            row = {"name": name, "purpose": purpose}
            row.update(generate_case(model, tokenizer, mx, stream_generate, make_sampler,
                                     messages, budget, sampler, baseline_swap, output_dir / f"{name}.txt"))
            if name == "chapter":
                row["validation"] = validate_chapter((output_dir / f"{name}.txt").read_text(), prompts["chapter"])
            elif name.startswith("scenes-"):
                batch = next(item for item in batches if name == f"scenes-{item['batch_id']}")
                row["validation"] = validate_scene_json(
                    (output_dir / f"{name}.txt").read_text(), [scene[0] for scene in batch["scenes"]])
            elif purpose == "padded_benchmark":
                generated = (output_dir / f"{name}.txt").read_text().strip()
                row["validation"] = {"pass": generated == "HARBOR LANTERN", "actual": generated}
            else:
                row["validation"] = {"pass": not row["think_spill"] and row["completion_tokens"] > 0}
            row["generation_complete"] = row["finish_reason"] == "stop"
            row["mechanical_pass"] = bool(row["generation_complete"] and row["validation"]["pass"])
            report["cases"].append(row)
            write_report(output_dir, report)

        sampler.stopping.set()
        sampler.join(timeout=1)
        mechanical = all(row["mechanical_pass"] for row in report["cases"])
        report["generation_mechanical_pass"] = mechanical
        report["physical_tier_result"] = {
            "tier_gib": report["environment"]["physical_ram_tier_gib"],
            "suite": args.suite,
            "mechanically_qualified": bool(args.suite == "full" and mechanical),
            "scope": "this physical host only; no lower-memory tier is inferred",
        }
        report["status"] = "pass" if mechanical else "fail"
        report["finished_at"] = time.time()
        write_report(output_dir, report)
        return 0 if mechanical else 1
    except InterruptedError as exc:
        report.update(status="inconclusive", inconclusive_reason=str(exc), finished_at=time.time())
        write_report(output_dir, report)
        return 2
    except Exception as exc:
        report.update(status="model_or_probe_failure", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())
        write_report(output_dir, report)
        raise


def preflight(model_dir: Path, hub_source: Path = DEFAULT_HUB_SOURCE) -> tuple[dict[str, Any], str | None]:
    weights = sorted(model_dir.glob("*.safetensors"))
    total = sum(path.stat().st_size for path in weights)
    physical = sysctl_int("hw.memsize")
    current = available_memory(hub_source)
    sample = health(swap_used())
    reason = None
    if not model_dir.is_dir() or not (model_dir / "config.json").is_file() or not weights:
        reason = "model directory is incomplete (config.json and local safetensors are required)"
    elif sample["pressure_level"] == 4:
        reason = "kernel memory pressure is already critical"
    elif physical is not None and total + WORKING_ROOM > physical:
        reason = f"weights plus 4 GiB working room ({total + WORKING_ROOM} bytes) exceed physical RAM ({physical} bytes)"
    return {
        "weights_bytes": total, "working_room_bytes": WORKING_ROOM,
        "physical_ram_bytes": physical, "available_memory_bytes": current,
        "available_memory_is_warning_only": True, "health": sample,
    }, reason


def run_parent(args: argparse.Namespace) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if (args.output_dir / REPORT).exists():
        raise SystemExit(f"refusing to overwrite existing {(args.output_dir / REPORT)}")
    check, refusal = preflight(args.model_dir, args.hub_source)
    baseline = swap_used()
    if refusal:
        report = {
            "schema_version": 1, "status": "inconclusive", "suite": args.suite,
            "model_dir": str(args.model_dir), "preflight": check,
            "inconclusive_reason": refusal, "finished_at": time.time(),
        }
        write_report(args.output_dir, report)
        print(refusal, file=sys.stderr)
        return 2
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--model-dir", str(args.model_dir),
               "--output-dir", str(args.output_dir), "--suite", args.suite,
               "--hub-source", str(args.hub_source)]
    env = dict(os.environ, MOE_PROBE_BASELINE_SWAP=str(baseline or 0))
    child = subprocess.Popen(command, env=env)
    peak = 0
    reason = None
    while child.poll() is None:
        peak = max(peak, footprint(child.pid) or 0)
        sample = health(baseline)
        reason = abort_reason(sample)
        if reason:
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            break
        time.sleep(0.1)
    report_path = args.output_dir / REPORT
    report = json.loads(report_path.read_text()) if report_path.exists() else {
        "schema_version": 1, "suite": args.suite, "model_dir": str(args.model_dir), "cases": [],
    }
    report["preflight"] = check
    report["parent_observed_peak_phys_footprint_bytes"] = peak
    report["run_health_final"] = health(baseline)
    if reason:
        report.update(status="inconclusive", inconclusive_reason=reason, finished_at=time.time())
    write_report(args.output_dir, report)
    print(json.dumps({"status": report.get("status"), "report": str(report_path)}, indent=2))
    return 2 if reason else int(child.returncode or 0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--suite", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--hub-source", type=Path, default=DEFAULT_HUB_SOURCE,
                        help="Studio Hub checkout used only for components/host_memory.py")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.model_dir = resolve_model_dir(args.model_dir.expanduser().resolve())
    args.output_dir = args.output_dir.expanduser().resolve()
    args.hub_source = args.hub_source.expanduser().resolve()
    return args


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    return run_worker(args) if args.worker else run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
