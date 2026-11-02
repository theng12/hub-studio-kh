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
DEFAULT_MAX_FOOTPRINT_GIB = 26.0
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

DEFAULT_LONG_CONTEXT_SPEC: dict[str, Any] = {
    "max_tokens": 4096,
    "expected_sections": 16,
    "needle": "HARBOR LANTERN",
    "base_messages": [
        {
            "role": "system",
            "content": "You are a precise technical writing assistant evaluating long-context retrieval and sustained generation. Read the supplied reference material silently. Do not reveal hidden reasoning.",
        },
        {
            "role": "user",
            "content": (
                "Read the reference material below. Inside the reference material, locate the secret retrieval token: 'HARBOR LANTERN'.\n\n"
                "Then, write an exhaustive structured report containing exactly 16 numbered sections, from Section 01 to Section 16.\n"
                "Format each section heading on its own line exactly as:\n"
                "## Section XX: Title\n"
                "(where XX is 01, 02, ..., 16).\n\n"
                "Write a substantive, detailed analysis of 150 to 200 words for every section discussing harbor operations, observation logs, and meteorological records.\n"
                "In Section 16, conclude your report by explicitly stating the secret retrieval token in the format:\n"
                "'Secret Key: HARBOR LANTERN'\n\n"
                "Reference material follows."
            ),
        },
    ],
    "padding_paragraph": (
        "At dawn the harbor clerk checked the tide ledger, the brass clock, the north window, and the quiet line of boats. "
        "The entry was copied carefully so a later reader could compare ordinary conditions without guessing what had changed. "
        "Record: HARBOR LANTERN is preserved in the harbor archives. "
        "No other event in this repeated reference paragraph changes the requested answer."
    ),
}


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


def validate_long_script(text: str, spec: dict[str, Any]) -> dict[str, Any]:
    expected = [f"## Chapter {row['number']:02d}: {row['title']}" for row in spec["chapters"]]
    matches = list(re.finditer(r"(?m)^## Chapter (\d+): ([^\n]+)$", text))
    headings = [match.group(0) for match in matches]
    all_h2 = re.findall(r"(?m)^## [^\n]+$", text)
    errors: list[str] = []
    if headings != expected or all_h2 != expected or not text.startswith(expected[0]):
        errors.append("chapter headings must exactly match the requested three chapters in order")

    chapters: list[dict[str, Any]] = []
    minimum = spec["minimum_words_per_chapter"]
    maximum = spec["maximum_words_per_chapter"]
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        count = word_count(text[match.end():body_end].strip())
        chapter_errors = []
        if index >= len(expected) or match.group(0) != expected[index]:
            chapter_errors.append("unexpected chapter number, title, or order")
        if not minimum <= count <= maximum:
            chapter_errors.append(f"body word count {count} is outside {minimum}-{maximum}")
        chapters.append({
            "number": int(match.group(1)), "title": match.group(2), "word_count": count,
            "pass": not chapter_errors, "errors": chapter_errors,
        })
    if len(chapters) != len(expected):
        errors.append(f"expected {len(expected)} chapters, got {len(chapters)}")
    if any(not chapter["pass"] for chapter in chapters):
        errors.append("one or more chapters failed per-chapter validation")

    missing = [fact for fact in spec["must_include"] if fact.casefold() not in text.casefold()]
    forbidden = [phrase for phrase in spec.get("must_not_include", []) if phrase.casefold() in text.casefold()]
    if missing:
        errors.append("missing stable facts: " + ", ".join(missing))
    if forbidden:
        errors.append("contradicted constraints: " + ", ".join(forbidden))
    if "```" in text:
        errors.append("markdown fence leaked into output")
    if has_think_spill(text):
        errors.append("thinking trace marker leaked into output")
    return {"pass": not errors, "chapters": chapters, "missing_facts": missing,
            "forbidden_phrases": forbidden, "errors": errors}


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


JS_TRIM_CHARS = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def _js_trim(text: str) -> str:
    return text.strip(JS_TRIM_CHARS)


def _lead_with_style(description: str, style: str | None) -> str:
    desc = _js_trim(description)
    clean_style = _js_trim(style or "")
    if not clean_style:
        return desc

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    style_key = normalize(clean_style)[:40]
    if style_key and normalize(desc).startswith(style_key):
        return desc
    separator = "" if re.search(r"[.!?…,;:]$", clean_style) else "."
    return f"{clean_style}{separator} {desc}"


def validate_scene_json(text: str, expected_ids: list[str], max_chars: int | None = None,
                        style: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    lengths: list[dict[str, Any]] = []
    cap_breaches: list[dict[str, Any]] = []

    def length_stats() -> dict[str, Any]:
        values = [item["chars"] for item in lengths]
        return {
            "items": lengths,
            "count": len(lengths),
            "min_chars": min(values) if values else None,
            "max_chars": max(values) if values else None,
            "mean_chars": round(sum(values) / len(values), 1) if values else None,
            "cap": max_chars,
            "over_cap_count": len(cap_breaches),
            "cap_breaches": cap_breaches,
        }

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"pass": False, "count": 0, "ids": [], "length_stats": length_stats(),
                "errors": [f"strict JSON parse failed: {exc}"]}
    if not isinstance(data, dict) or set(data) != {"results"} or not isinstance(data.get("results"), list):
        return {"pass": False, "count": 0, "ids": [], "length_stats": length_stats(),
                "errors": ["expected exactly one top-level results array"]}
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
        if not isinstance(prompt, str) or not _js_trim(prompt):
            errors.append(f"result {index} visual_prompt is empty")
        else:
            chars = _utf16_length(_lead_with_style(prompt, style))
            item = {"scene_id": scene_id if isinstance(scene_id, str) else None, "chars": chars}
            lengths.append(item)
            if max_chars is not None and chars > max_chars:
                cap_breaches.append(item)
                errors.append(f"result {index} final visual_prompt is {chars} characters, above cap {max_chars}")
    if len(rows) != len(expected_ids):
        errors.append(f"expected {len(expected_ids)} results, got {len(rows)}")
    if len(ids) != len(set(ids)):
        errors.append("scene_id values are not unique")
    if ids != expected_ids:
        errors.append("scene_id values or order do not exactly match the request")
    if has_think_spill(text):
        errors.append("thinking trace marker leaked into output")
    return {"pass": not errors, "count": len(rows), "ids": ids,
            "length_stats": length_stats(), "errors": errors}


def validate_long_context(text: str, spec: dict[str, Any]) -> dict[str, Any]:
    expected_count = spec.get("expected_sections", 16)
    needle = spec.get("needle", "HARBOR LANTERN")
    section_pattern = re.compile(r"(?m)^#{2,3}\s+Section\s+(\d+)[:\s-]([^\n]+)$", re.IGNORECASE)
    matches = list(section_pattern.finditer(text))
    found_numbers = [int(m.group(1)) for m in matches]

    if not matches:
        numbered_pattern = re.compile(r"(?m)^(\d+)\.\s+([^\n]+)$")
        matches = list(numbered_pattern.finditer(text))
        found_numbers = [int(m.group(1)) for m in matches]

    errors: list[str] = []
    if len(matches) < expected_count:
        errors.append(f"expected {expected_count} sections, got {len(matches)}")
    missing_nums = [i for i in range(1, expected_count + 1) if i not in found_numbers]
    if missing_nums:
        errors.append(f"missing section numbers: {missing_nums}")

    needle_found = needle.casefold() in text.casefold()
    if not needle_found:
        errors.append(f"needle '{needle}' not found in output")

    if has_think_spill(text):
        errors.append("thinking trace marker leaked into output")

    return {
        "pass": not errors,
        "sections_count": len(matches),
        "expected_sections": expected_count,
        "needle_found": needle_found,
        "needle": needle,
        "errors": errors,
    }


def eval_stop_rule(row: dict[str, Any], max_footprint_bytes: int, current_safe_size: str) -> tuple[bool, str, str]:
    """Evaluate stop rule for one completed long-context size.

    Returns (should_stop, stop_reason, last_safe_size).
    Stops before next size if swap grew (swap_delta > 0) or peak process footprint
    exceeded max_footprint_bytes (default 26 GB).
    """
    case_name = row.get("name", "unknown")
    swap_delta = row.get("swap_delta_bytes")
    if swap_delta is None and isinstance(row.get("health_after"), dict):
        swap_delta = row["health_after"].get("swap_delta_bytes")
    swap_delta = swap_delta or 0

    footprint_val = row.get("kernel_phys_footprint_peak_bytes") or 0
    swap_grew = swap_delta > 0
    footprint_exceeded = footprint_val > max_footprint_bytes

    if swap_grew or footprint_exceeded:
        reasons = []
        if footprint_exceeded:
            reasons.append(
                f"peak process footprint of {footprint_val / GIB:.2f} GiB exceeded {max_footprint_bytes / GIB:.2f} GiB limit"
            )
        if swap_grew:
            reasons.append(f"swap grew by {swap_delta} bytes ({swap_delta / (1024**2):.2f} MiB)")
        stop_reason = f"stopped before next size: {' and '.join(reasons)} at {case_name}"
        return True, stop_reason, current_safe_size

    return False, "safe", case_name


def scene_messages(batch: dict[str, Any]) -> list[dict[str, str]]:
    expected = [{"scene_id": row[0], "narration": row[1]} for row in batch["scenes"]]
    max_chars = batch.get("max_chars", 4000)
    system = (
        "You write visual scene descriptions for an AI image generator. Return only one valid JSON object, "
        "with no markdown fences, commentary, or text outside JSON. The object must be exactly "
        "{\"results\":[{\"scene_id\":\"...\",\"visual_prompt\":\"...\"}]}. Preserve every scene_id "
        "exactly and in order. Return exactly one nonempty visual_prompt per input. Each prompt is one English "
        "paragraph naming subject, action, setting, framing, lighting, and mood. Aim for 500-900 characters in "
        f"the final styled prompt; shorter is acceptable, never pad, and never exceed {max_chars} characters. Do not "
        "reveal hidden reasoning."
    )
    user = (
        f"Art style for every prompt: {batch['style']}\n"
        f"Write all {len(batch['scenes'])} prompts for these stable scene IDs and narration beats:\n"
        + json.dumps(expected, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def scene_case_specs(prompts: dict[str, Any], suite: str) -> list[dict[str, Any]]:
    max_chars = prompts["bulk_scene_profile"]["max_chars"]
    baselines = prompts["scene_batches"] if suite == "full" else prompts["scene_batches"][:1]
    cases = []
    for source in baselines:
        batch = dict(source, max_chars=source.get("max_chars", max_chars))
        cases.append({"name": f"scenes-{batch['batch_id']}", "batch": batch,
                      "max_tokens": batch.get("max_tokens", 6144), "max_chars": batch["max_chars"]})
    if suite == "full":
        profile = prompts["bulk_scene_profile"]
        for size in profile["sizes"]:
            batch = {"batch_id": profile["profile_id"], "style": profile["style"], "max_chars": max_chars,
                     "scenes": profile["scenes"][:size["count"]]}
            cases.append({"name": f"scenes-{profile['profile_id']}-{size['count']}",
                          "batch": batch, "max_tokens": size["max_tokens"], "max_chars": max_chars})
    return cases


def scene_batch_capacity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passing = [row["scene_count"] for row in rows if row["mechanical_pass"]]
    return {
        "largest_passing_tested_batch_size": max(passing, default=None),
        "failed_batch_sizes": sorted({row["scene_count"] for row in rows if not row["mechanical_pass"]}),
        "scope": "single-run screening; not a production reliability guarantee",
    }


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
    prefill_time: float | None = None
    first_token_time: float | None = None
    generator = stream_generate(
        model, tokenizer, prompt=prompt, max_tokens=max_tokens,
        sampler=sampler_factory(temp=0.6, top_p=0.9),
    )
    try:
        for response in generator:
            if prefill_time is None:
                prefill_time = time.monotonic() - started
                first_token_time = time.monotonic()
            final = response
            chunks.append(response.text)
            reason = abort_reason(health(baseline_swap))
            if reason:
                raise InterruptedError(reason)
    finally:
        generator.close()
    elapsed = time.monotonic() - started
    generate_time = (time.monotonic() - first_token_time) if first_token_time is not None else 0.0
    text = "".join(chunks)
    output_path.write_text(text, encoding="utf-8")
    after = health(baseline_swap)
    swap_delta = after.get("swap_delta_bytes")
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
        "prefill_seconds": round(prefill_time, 3) if prefill_time is not None else None,
        "generate_seconds": round(generate_time, 3),
        "mlx_peak_bytes": int(mx.get_peak_memory()),
        "kernel_phys_footprint_peak_bytes": sampler.peak,
        "health_before": before,
        "health_after": after,
        "memory_pressure": after.get("pressure_name", "unknown"),
        "swap_delta_bytes": swap_delta,
        "think_spill": has_think_spill(text),
    }


def run_worker(args: argparse.Namespace) -> int:
    output_dir = args.output_dir
    prompts = load_prompts()
    scene_cases = scene_case_specs(prompts, args.suite)
    baseline_swap = int(os.environ.get("MOE_PROBE_BASELINE_SWAP", swap_used() or 0))
    report: dict[str, Any] = {
        "schema_version": 2, "suite_profile_version": 2,
        "status": "running", "suite": args.suite,
        "model_dir": str(args.model_dir), "started_at": time.time(),
        "environment": environment_record(args.hub_source), "cache_limit_bytes": CACHE_LIMIT,
        "cache_limit_source": CACHE_LIMIT_SOURCE,
        "prompts_sha256": sha256(PROMPTS),
        "swap_abort_delta_bytes": SWAP_ABORT, "subjective_writing_review": "not_performed",
        "suite_profile": {
            "version": 2,
            "long_script": {
                "name": prompts["long_script"]["case_id"],
                "enabled": args.suite == "full",
                "chapter_count": len(prompts["long_script"]["chapters"]),
                "target_words_per_chapter": prompts["long_script"]["target_words_per_chapter"],
                "max_tokens": prompts["long_script"]["max_tokens"],
            },
            "scene_batches": [{
                "name": case["name"], "scene_count": len(case["batch"]["scenes"]),
                "max_tokens": case["max_tokens"], "max_chars": case["max_chars"],
            } for case in scene_cases],
        },
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

        if args.suite == "long-context":
            max_footprint_bytes = int(args.max_footprint_gb * GIB)
            lc_spec = prompts.get("long_context") or DEFAULT_LONG_CONTEXT_SPEC
            targets = [
                ("input-48k", 49152),
                ("input-64k", 65536),
            ]
            last_safe_size = "none"
            stopped_early = False
            stop_reason = "all tested sizes completed within safe limits"

            for name, target_tokens in targets:
                print(f"preparing {name} prompt (~{target_tokens} tokens)...", flush=True)
                messages, measured_tokens = padded_messages(tokenizer, lc_spec, target_tokens)
                print(f"running {name} ({measured_tokens} prompt tokens, target ~4K output)...", flush=True)
                mx.random.seed(7)
                row = {"name": name, "purpose": "long_context_qualification", "target_tokens": target_tokens}
                row.update(generate_case(model, tokenizer, mx, stream_generate, make_sampler,
                                         messages, lc_spec.get("max_tokens", 4096), sampler,
                                         baseline_swap, output_dir / f"{name}.txt"))
                text = (output_dir / f"{name}.txt").read_text(encoding="utf-8")
                row["free_memory_at_start_bytes"] = report["environment"].get("available_memory_bytes_at_start")
                row["validation"] = validate_long_context(text, lc_spec)
                row["generation_complete"] = row["finish_reason"] == "stop"
                row["answer_complete"] = bool(row["validation"]["pass"] and row["generation_complete"])
                row["mechanical_pass"] = row["answer_complete"]
                report["cases"].append(row)
                write_report(output_dir, report)

                should_stop, reason, updated_safe = eval_stop_rule(row, max_footprint_bytes, last_safe_size)
                if should_stop:
                    stopped_early = True
                    stop_reason = reason
                    last_safe_size = updated_safe
                    print(f"STOPPING: {stop_reason}", flush=True)
                    break
                else:
                    last_safe_size = updated_safe

            report["stop_rule"] = {
                "max_footprint_bytes": max_footprint_bytes,
                "max_footprint_gib": args.max_footprint_gb,
                "stopped_early": stopped_early,
                "last_safe_size": last_safe_size,
                "stop_reason": stop_reason,
            }
            sampler.stopping.set()
            sampler.join(timeout=1)
            mechanical = bool(report["cases"]) and all(row["mechanical_pass"] for row in report["cases"])
            report["generation_mechanical_pass"] = mechanical
            report["physical_tier_result"] = {
                "tier_gib": report["environment"]["physical_ram_tier_gib"],
                "suite": args.suite,
                "last_safe_size": last_safe_size,
                "mechanically_qualified": bool(mechanical and not stopped_early),
                "scope": "this physical host only; no lower-memory tier is inferred",
            }
            report["status"] = "pass" if (mechanical and not stopped_early) else "fail"
            report["finished_at"] = time.time()
            write_report(output_dir, report)
            return 0 if (mechanical and not stopped_early) else 1

        specs: list[tuple[str, list[dict[str, str]], int, str]] = [
            ("speed", prompts["speed"]["messages"], prompts["speed"]["max_tokens"], "benchmark"),
        ]
        if args.suite == "full":
            for target in (8192, 32768):
                messages, _ = padded_messages(tokenizer, prompts["padded"], target)
                specs.append((f"input-{target // 1024}k", messages, prompts["padded"]["max_tokens"], "padded_benchmark"))
            specs.append((prompts["long_script"]["case_id"], prompts["long_script"]["messages"],
                          prompts["long_script"]["max_tokens"], "long_script_quality"))
        specs.append(("chapter", prompts["chapter"]["messages"], prompts["chapter"]["max_tokens"], "quality"))
        specs.extend((case["name"], scene_messages(case["batch"]), case["max_tokens"], "scene_quality")
                     for case in scene_cases)

        for name, messages, budget, purpose in specs:
            print(f"running {name}", flush=True)
            mx.random.seed(7)
            row = {"name": name, "purpose": purpose}
            row.update(generate_case(model, tokenizer, mx, stream_generate, make_sampler,
                                     messages, budget, sampler, baseline_swap, output_dir / f"{name}.txt"))
            if name == "chapter":
                row["validation"] = validate_chapter((output_dir / f"{name}.txt").read_text(), prompts["chapter"])
            elif name == prompts["long_script"]["case_id"]:
                row["validation"] = validate_long_script(
                    (output_dir / f"{name}.txt").read_text(), prompts["long_script"])
            elif name.startswith("scenes-"):
                scene_case = next(case for case in scene_cases if name == case["name"])
                batch = scene_case["batch"]
                row["scene_count"] = len(batch["scenes"])
                row["validation"] = validate_scene_json(
                    (output_dir / f"{name}.txt").read_text(), [scene[0] for scene in batch["scenes"]],
                    max_chars=scene_case["max_chars"], style=batch["style"])
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
        scene_rows = [row for row in report["cases"] if row["name"].startswith("scenes-")]
        report["scene_batch_capacity"] = scene_batch_capacity(scene_rows)
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
    free = check.get("available_memory_bytes")
    print("Free memory before start: "
          + (f"{free / GIB:.1f} GiB" if isinstance(free, (int, float)) else "not measured"), flush=True)
    baseline = swap_used()
    if refusal:
        report = {
            "schema_version": 2, "suite_profile_version": 2,
            "status": "inconclusive", "suite": args.suite,
            "model_dir": str(args.model_dir), "preflight": check,
            "inconclusive_reason": refusal, "finished_at": time.time(),
        }
        write_report(args.output_dir, report)
        print(refusal, file=sys.stderr)
        return 2
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--model-dir", str(args.model_dir),
               "--output-dir", str(args.output_dir), "--suite", args.suite,
               "--hub-source", str(args.hub_source),
               "--max-footprint-gb", str(args.max_footprint_gb)]
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
        "schema_version": 2, "suite_profile_version": 2,
        "suite": args.suite, "model_dir": str(args.model_dir), "cases": [],
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
    parser.add_argument("--suite", choices=("smoke", "full", "long-context"), default="smoke")
    parser.add_argument("--max-footprint-gb", type=float, default=DEFAULT_MAX_FOOTPRINT_GIB,
                        help=f"Peak physical footprint limit in GiB before stopping subsequent sizes (default: {DEFAULT_MAX_FOOTPRINT_GIB:.1f})")
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
