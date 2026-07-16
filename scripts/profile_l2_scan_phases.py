#!/usr/bin/env python3
"""Temporary current-main L2 scan phase profiler.

Runs two small catalog examples and decomposes dump+compare into binary loading,
DWARF, CastXML subprocess, XML parsing, model construction, serialization,
comparison, and fresh-process import/CLI overhead. Intended for an ephemeral CI
probe branch; it has no product-code hooks and does not alter scanner behavior.
"""
from __future__ import annotations

import inspect
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "profile-results"
OUT.mkdir(exist_ok=True)
REPETITIONS = 3

CASES = (
    {
        "name": "case02_param_type_change",
        "old_src": "v1.c",
        "new_src": "v2.c",
        "old_hdr": "v1.h",
        "new_hdr": "v2.h",
        "compiler": "gcc",
        "lang": "C",
    },
    {
        "name": "case09_cpp_vtable",
        "old_src": "v1.cpp",
        "new_src": "v2.cpp",
        "old_hdr": "v1.h",
        "new_hdr": "v2.h",
        "compiler": "g++",
        "lang": "C++",
    },
)


def now() -> float:
    return time.perf_counter()


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000.0, 3)


def command_version(command: list[str]) -> str:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0] if text else f"exit={proc.returncode}"


class PhaseRecorder:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self._restore: list[tuple[Any, str, Any]] = []

    def add(self, label: str, elapsed: float) -> None:
        self.seconds[label] += elapsed
        self.calls[label] += 1

    def patch_callable(self, owner: Any, name: str, label: str) -> bool:
        if not hasattr(owner, name):
            return False
        original = getattr(owner, name)
        if not callable(original):
            return False

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            started = now()
            try:
                return original(*args, **kwargs)
            finally:
                self.add(label, now() - started)

        self._restore.append((owner, name, original))
        setattr(owner, name, wrapped)
        return True

    def patch_subprocess(self, subprocess_module: Any) -> None:
        original = subprocess_module.run

        def wrapped(command: Any, *args: Any, **kwargs: Any) -> Any:
            started = now()
            try:
                return original(command, *args, **kwargs)
            finally:
                elapsed = now() - started
                executable = ""
                if isinstance(command, (list, tuple)) and command:
                    executable = Path(str(command[0])).name.lower()
                elif isinstance(command, str):
                    executable = command.split()[0].lower() if command.split() else ""
                if "castxml" in executable:
                    label = "castxml_subprocess"
                elif executable in {"gcc", "g++", "cc", "c++", "clang", "clang++"}:
                    label = "compiler_probe_subprocess"
                else:
                    label = "other_subprocess"
                self.add(label, elapsed)

        self._restore.append((subprocess_module, "run", original))
        subprocess_module.run = wrapped

    def restore(self) -> None:
        for owner, name, original in reversed(self._restore):
            setattr(owner, name, original)
        self._restore.clear()


@contextmanager
def instrument_dump() -> Iterator[PhaseRecorder]:
    import abicheck.dumper as dumper
    import abicheck.elf_metadata as elf_metadata
    import abicheck.provenance as provenance

    recorder = PhaseRecorder()
    recorder.patch_callable(dumper, "_pyelftools_exported_symbols", "elf_symbol_tables")
    recorder.patch_callable(elf_metadata, "parse_elf_metadata", "elf_metadata")
    recorder.patch_callable(dumper, "_resolve_debug_metadata", "debug_metadata")
    recorder.patch_callable(dumper, "_cache_key", "header_cache_key")
    recorder.patch_callable(dumper, "_populate_elf_visibility", "visibility_mapping")
    recorder.patch_callable(dumper, "backfill_dwarf_layout", "dwarf_layout_backfill")
    recorder.patch_callable(dumper, "dwarf_layout_types_or_empty", "dwarf_layout_projection")
    recorder.patch_callable(provenance, "apply_provenance", "provenance")

    # DefusedXML parsing is separate from native CastXML execution.
    recorder.patch_callable(dumper.DefusedET, "parse", "castxml_xml_parse")

    parser_cls = getattr(dumper, "_CastxmlParser", None)
    if parser_cls is not None:
        for method in (
            "parse_functions",
            "parse_variables",
            "parse_types",
            "parse_enums",
            "parse_typedefs",
            "parse_constants",
        ):
            recorder.patch_callable(parser_cls, method, f"model_{method}")

    # dumper.subprocess is the stdlib module object. Patch only while dump runs.
    recorder.patch_subprocess(dumper.subprocess)
    try:
        yield recorder
    finally:
        recorder.restore()


def filtered_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    parameters = inspect.signature(function).parameters
    accepted = {key: value for key, value in kwargs.items() if key in parameters}
    return function(*args, **accepted)


def dump_one(
    library: Path,
    header: Path,
    include_dir: Path,
    *,
    version: str,
    compiler: str,
    lang: str,
) -> tuple[Any, dict[str, Any]]:
    from abicheck.dumper import dump

    kwargs = {
        "extra_includes": [include_dir],
        "version": version,
        "compiler": compiler,
        "lang": lang,
        "public_headers": [header],
        "public_header_dirs": [include_dir],
        "header_backend": "castxml",
    }
    started = now()
    with instrument_dump() as recorder:
        snapshot = filtered_call(dump, library, [header], **kwargs)
    total = now() - started
    measured = sum(recorder.seconds.values())
    # Parser model methods are disjoint from XML parse/native subprocess. Other
    # wrappers are selected to avoid parent/child overlap in this path.
    residual = max(0.0, total - measured)
    phases = {key: round(value, 9) for key, value in recorder.seconds.items()}
    phases["other_dump_python"] = round(residual, 9)
    phases["dump_total"] = round(total, 9)
    return snapshot, {
        "seconds": phases,
        "calls": dict(recorder.calls),
        "snapshot_counts": {
            "functions": len(getattr(snapshot, "functions", ()) or ()),
            "variables": len(getattr(snapshot, "variables", ()) or ()),
            "types": len(getattr(snapshot, "types", ()) or ()),
            "enums": len(getattr(snapshot, "enums", ()) or ()),
            "typedefs": len(getattr(snapshot, "typedefs", ()) or ()),
        },
    }


def call_compare(old: Any, new: Any) -> Any:
    from abicheck.checker import compare

    kwargs = {
        "scope_to_public_surface": False,
        "scope_public_headers": False,
    }
    return filtered_call(compare, old, new, **kwargs)


def serialize_snapshot(snapshot: Any) -> tuple[float, int, str]:
    started = now()
    representation: str
    try:
        import abicheck.serialization as serialization

        if hasattr(serialization, "snapshot_to_json"):
            value = serialization.snapshot_to_json(snapshot)
            representation = value if isinstance(value, str) else json.dumps(value)
        elif hasattr(serialization, "snapshot_to_dict"):
            representation = json.dumps(serialization.snapshot_to_dict(snapshot))
        elif hasattr(snapshot, "to_dict"):
            representation = json.dumps(snapshot.to_dict())
        else:
            representation = repr(snapshot)
    except Exception as exc:  # noqa: BLE001
        representation = f"SERIALIZATION_ERROR:{type(exc).__name__}:{exc}"
    return now() - started, len(representation.encode("utf-8")), representation[:160]


def compile_case(case: dict[str, str], case_dir: Path, work: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for side, source_key in (("old", "old_src"), ("new", "new_src")):
        output = work / f"lib{side}.so"
        command = [
            case["compiler"],
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            "-I",
            str(case_dir),
            "-o",
            str(output),
            str(case_dir / case[source_key]),
        ]
        started = now()
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        elapsed = now() - started
        result[side] = {
            "seconds": round(elapsed, 9),
            "returncode": proc.returncode,
            "command": command,
            "stderr": proc.stderr[-1000:],
            "path": str(output),
            "size_bytes": output.stat().st_size if output.exists() else None,
        }
        if proc.returncode != 0:
            raise RuntimeError(f"compile failed for {case['name']} {side}: {proc.stderr}")
    return result


def fresh_process_timings() -> dict[str, Any]:
    commands = {
        "python_empty": [sys.executable, "-c", "pass"],
        "import_abicheck": [sys.executable, "-c", "import abicheck"],
        "import_dumper": [sys.executable, "-c", "import abicheck.dumper"],
        "import_checker": [sys.executable, "-c", "import abicheck.checker"],
        "import_cli": [sys.executable, "-c", "import abicheck.cli"],
        "cli_help": [sys.executable, "-m", "abicheck", "--help"],
    }
    rows: dict[str, Any] = {}
    for name, command in commands.items():
        samples: list[float] = []
        errors: list[str] = []
        for _ in range(5):
            started = now()
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
            samples.append(now() - started)
            if proc.returncode != 0:
                errors.append((proc.stderr or proc.stdout)[-500:])
        rows[name] = {
            "samples_seconds": [round(value, 9) for value in samples],
            "median_seconds": round(statistics.median(samples), 9),
            "errors": errors,
        }
    empty = rows["python_empty"]["median_seconds"]
    for name, row in rows.items():
        row["net_over_empty_seconds"] = round(max(0.0, row["median_seconds"] - empty), 9)
    return rows


def try_cli_scan(
    work: Path,
    old_lib: Path,
    new_lib: Path,
    old_hdr: Path,
    new_hdr: Path,
) -> dict[str, Any]:
    cache = work / "cli-cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    env = os.environ.copy()
    env["XDG_CACHE_HOME"] = str(cache)
    old_json = work / "cli-old.json"
    new_json = work / "cli-new.json"
    commands = (
        [sys.executable, "-m", "abicheck", "dump", str(old_lib), "-H", str(old_hdr),
         "--public-header", str(old_hdr), "--version", "old", "-o", str(old_json)],
        [sys.executable, "-m", "abicheck", "dump", str(new_lib), "-H", str(new_hdr),
         "--public-header", str(new_hdr), "--version", "new", "-o", str(new_json)],
        [sys.executable, "-m", "abicheck", "compare", str(old_json), str(new_json),
         "--format", "json"],
    )
    rows: list[dict[str, Any]] = []
    total_started = now()
    for command in commands:
        started = now()
        proc = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
        elapsed = now() - started
        rows.append({
            "command": command,
            "seconds": round(elapsed, 9),
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-500:],
            "stderr_tail": proc.stderr[-500:],
        })
        if proc.returncode not in (0, 2, 4, 8):
            break
    return {"steps": rows, "total_seconds": round(now() - total_started, 9)}


def summarize_case(raw: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"name": raw["name"]}
    for temperature in ("cold", "warm"):
        combined: dict[str, list[float]] = defaultdict(list)
        totals: list[float] = []
        for repetition in raw["repetitions"]:
            pair = repetition[temperature]
            phase_keys = set(pair["old"]["seconds"]) | set(pair["new"]["seconds"])
            for key in phase_keys:
                value = pair["old"]["seconds"].get(key, 0.0) + pair["new"]["seconds"].get(key, 0.0)
                combined[key].append(value)
            totals.append(
                pair["old"]["seconds"]["dump_total"]
                + pair["new"]["seconds"]["dump_total"]
            )
        medians = {key: median(values) or 0.0 for key, values in combined.items()}
        total_median = median(totals) or 0.0
        summary[temperature] = {
            "dump_pair_ms": ms(total_median),
            "phases_ms": {key: ms(value) for key, value in sorted(medians.items())},
            "phase_pct_of_pair": {
                key: round(100.0 * value / total_median, 2) if total_median else 0.0
                for key, value in sorted(medians.items())
                if key != "dump_total"
            },
        }
    summary["build_pair_ms"] = ms(
        raw["build"]["old"]["seconds"] + raw["build"]["new"]["seconds"]
    )
    summary["compare_first_ms"] = ms(median(raw["compare_first_seconds"]))
    summary["compare_steady_ms"] = ms(median(raw["compare_steady_seconds"]))
    summary["serialize_pair_ms"] = ms(median(raw["serialize_pair_seconds"]))
    summary["cli_scan_ms"] = ms(raw["cli"]["total_seconds"])
    return summary


def write_markdown(report: dict[str, Any]) -> None:
    lines = [
        "# L2 scan phase profile — current main",
        "",
        f"- Source SHA: `{report['environment']['source_sha']}`",
        f"- Checked-out SHA: `{report['environment']['checked_out_sha']}`",
        f"- Python: `{report['environment']['python']}`",
        f"- CastXML: `{report['environment']['castxml']}`",
        f"- GCC: `{report['environment']['gcc']}`",
        "",
        "## Median timings",
        "",
        "| Example | Build pair | Cold dump pair | Warm dump pair | First compare | Steady compare | Serialize pair | Public CLI scan |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["summary"]:
        lines.append(
            f"| {row['name']} | {row['build_pair_ms']:.1f} ms | "
            f"{row['cold']['dump_pair_ms']:.1f} ms | {row['warm']['dump_pair_ms']:.1f} ms | "
            f"{row['compare_first_ms']:.2f} ms | {row['compare_steady_ms']:.2f} ms | "
            f"{row['serialize_pair_ms']:.2f} ms | {row['cli_scan_ms']:.1f} ms |"
        )
        lines.extend(["", f"### {row['name']} cold dump pair", "", "| Phase | ms | % |", "|---|---:|---:|"])
        for phase, value in row["cold"]["phases_ms"].items():
            if phase == "dump_total":
                continue
            pct = row["cold"]["phase_pct_of_pair"].get(phase, 0.0)
            lines.append(f"| `{phase}` | {value:.3f} | {pct:.2f}% |")
    lines.extend(["", "## Fresh-process startup", "", "| Operation | median | net over empty Python |", "|---|---:|---:|"])
    for name, row in report["fresh_process"].items():
        lines.append(
            f"| `{name}` | {row['median_seconds'] * 1000:.2f} ms | "
            f"{row['net_over_empty_seconds'] * 1000:.2f} ms |"
        )
    (OUT / "l2-scan-phase-profile.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    checked_out_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    environment = {
        "source_sha": os.environ.get("PROFILE_SOURCE_SHA", checked_out_sha),
        "checked_out_sha": checked_out_sha,
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "castxml": command_version(["castxml", "--version"]),
        "gcc": command_version(["gcc", "--version"]),
        "gxx": command_version(["g++", "--version"]),
        "cpu_count": os.cpu_count(),
    }
    raw_cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="abicheck-l2-profile-") as temporary:
        root = Path(temporary)
        for case in CASES:
            case_dir = REPO / "examples" / case["name"]
            work = root / case["name"]
            work.mkdir(parents=True)
            build = compile_case(case, case_dir, work)
            old_lib = Path(build["old"]["path"])
            new_lib = Path(build["new"]["path"])
            old_hdr = case_dir / case["old_hdr"]
            new_hdr = case_dir / case["new_hdr"]
            case_raw: dict[str, Any] = {
                "name": case["name"],
                "build": build,
                "repetitions": [],
                "compare_first_seconds": [],
                "compare_steady_seconds": [],
                "serialize_pair_seconds": [],
            }
            last_old = last_new = None
            for repetition in range(REPETITIONS):
                cache = work / f"cache-{repetition}"
                shutil.rmtree(cache, ignore_errors=True)
                cache.mkdir(parents=True)
                os.environ["XDG_CACHE_HOME"] = str(cache)
                os.environ["ABICHECK_CACHE_DIR"] = str(cache / "abicheck")

                old_snapshot, cold_old = dump_one(
                    old_lib, old_hdr, case_dir, version="old",
                    compiler=case["compiler"], lang=case["lang"],
                )
                new_snapshot, cold_new = dump_one(
                    new_lib, new_hdr, case_dir, version="new",
                    compiler=case["compiler"], lang=case["lang"],
                )
                warm_old_snapshot, warm_old = dump_one(
                    old_lib, old_hdr, case_dir, version="old",
                    compiler=case["compiler"], lang=case["lang"],
                )
                warm_new_snapshot, warm_new = dump_one(
                    new_lib, new_hdr, case_dir, version="new",
                    compiler=case["compiler"], lang=case["lang"],
                )

                started = now()
                result = call_compare(old_snapshot, new_snapshot)
                first_compare = now() - started
                steady: list[float] = []
                for _ in range(5):
                    started = now()
                    call_compare(old_snapshot, new_snapshot)
                    steady.append(now() - started)
                old_ser, old_size, old_preview = serialize_snapshot(old_snapshot)
                new_ser, new_size, new_preview = serialize_snapshot(new_snapshot)
                case_raw["compare_first_seconds"].append(first_compare)
                case_raw["compare_steady_seconds"].append(statistics.median(steady))
                case_raw["serialize_pair_seconds"].append(old_ser + new_ser)
                case_raw["repetitions"].append({
                    "index": repetition,
                    "cold": {"old": cold_old, "new": cold_new},
                    "warm": {"old": warm_old, "new": warm_new},
                    "compare": {
                        "first_seconds": first_compare,
                        "steady_samples_seconds": steady,
                        "verdict": str(getattr(result, "verdict", "unknown")),
                        "changes": len(getattr(result, "changes", ()) or ()),
                    },
                    "serialization": {
                        "old_seconds": old_ser,
                        "new_seconds": new_ser,
                        "old_size_bytes": old_size,
                        "new_size_bytes": new_size,
                        "old_preview": old_preview,
                        "new_preview": new_preview,
                    },
                })
                last_old, last_new = warm_old_snapshot, warm_new_snapshot
            assert last_old is not None and last_new is not None
            case_raw["cli"] = try_cli_scan(work, old_lib, new_lib, old_hdr, new_hdr)
            case_raw["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 3)
            raw_cases.append(case_raw)

    report = {
        "schema": "abicheck-l2-phase-profile/1",
        "environment": environment,
        "repetitions": REPETITIONS,
        "cases": raw_cases,
        "summary": [summarize_case(case) for case in raw_cases],
        "fresh_process": fresh_process_timings(),
    }
    (OUT / "l2-scan-phase-profile.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_markdown(report)
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
