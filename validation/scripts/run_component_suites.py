#!/usr/bin/env python3
"""Run source-family component suites and emit remeasurement metadata."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

SCHEMA_VERSION = "component_suites.v1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "validation" / "data" / "component_suites.json"

SUITES: dict[str, dict[str, object]] = {
    "elf-symbol-surface": {
        "source_layers": ["L0", "L1", "L2"],
        "platforms": ["linux"],
        "tests": [
            "tests/test_elf_metadata_unit.py",
            "tests/test_elf_parse_integration.py",
            "tests/test_elf_symbol_filters.py",
            "tests/test_elf_version_policy.py",
            "tests/test_surface.py",
            "tests/test_surface_scope_parity.py",
            "tests/test_confidence_evidence.py",
            "tests/test_stripped_degradation.py",
        ],
    },
    "debug-metadata": {
        "source_layers": ["L1"],
        "platforms": ["linux"],
        "tests": [
            "tests/test_dwarf_snapshot.py",
            "tests/test_dwarf_metadata_coverage.py",
            "tests/test_dwarf_unified.py",
            "tests/test_debug_resolver.py",
            "tests/test_btf_metadata.py",
            "tests/test_btf_integration.py",
            "tests/test_ctf_metadata.py",
            "tests/test_pdb_metadata.py",
            "tests/test_pdb_parser.py",
            "tests/test_pe_metadata_unit.py",
            "tests/test_macho_metadata_unit.py",
        ],
    },
    "build-source-package": {
        "source_layers": ["L3", "L4", "L5"],
        "platforms": ["linux", "macos", "windows"],
        "tests": [
            "tests/test_build_context.py",
            "tests/test_package.py",
            "tests/test_package_extractor_matrix.py",
        ],
    },
    "impact-context": {
        "source_layers": ["L0", "L1", "L2", "L3", "L4", "L5"],
        "platforms": ["linux", "macos", "windows"],
        "tests": [
            "tests/test_bundle.py",
            "tests/test_stack_checker.py",
            "tests/test_appcompat.py",
            "tests/test_appcompat_examples.py",
        ],
    },
    "report-policy": {
        "source_layers": ["L0", "L1", "L2", "L3", "L4", "L5"],
        "platforms": ["linux", "macos", "windows"],
        "tests": [
            "tests/test_report_schema.py",
            "tests/test_reporter.py",
            "tests/test_sarif.py",
            "tests/test_junit_report.py",
            "tests/test_policy_changekind_matrix.py",
            "tests/test_policy_file.py",
            "tests/test_baseline.py",
            "tests/test_suppression_matrix.py",
        ],
    },
}

SUMMARY_PATTERNS = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "errors": re.compile(r"(\d+) errors?"),
    "skipped": re.compile(r"(\d+) skipped"),
    "warnings": re.compile(r"(\d+) warnings?"),
}


def platform_tag() -> str:
    """Return the platform tag used in component-suite records."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    return system or sys.platform


def optional_dependency_blocker(test_path: str) -> str | None:
    """Return a blocker reason for optional dependencies needed by a test."""
    if test_path in {
        "tests/test_pe_metadata_unit.py",
        "tests/test_pdb_metadata.py",
        "tests/test_pdb_parser.py",
    } and importlib.util.find_spec("pefile") is None:
        return "missing Python dependency: pefile"
    return None


def missing_blockers(tests: list[str]) -> list[str]:
    """Return missing-file and dependency blockers for a test list."""
    blockers = []
    for test_path in tests:
        if not (ROOT / test_path).exists():
            blockers.append(f"missing test file: {test_path}")
            continue
        dep_blocker = optional_dependency_blocker(test_path)
        if dep_blocker:
            blockers.append(dep_blocker)
    return sorted(set(blockers))


def parse_pytest_counts(output: str) -> dict[str, int]:
    """Extract pytest summary counts from combined stdout/stderr."""
    counts: dict[str, int] = {}
    for key, pattern in SUMMARY_PATTERNS.items():
        match = pattern.search(output)
        if match:
            counts[key] = int(match.group(1))
    return counts


def suite_record(
    name: str,
    suite: dict[str, object],
    *,
    status: str,
    command: list[str],
    exit_code: int | None,
    seconds: float,
    stdout: str = "",
    stderr: str = "",
    blockers: list[str] | None = None,
) -> dict[str, object]:
    """Build one component-suite result record."""
    output = f"{stdout}\n{stderr}"
    return {
        "schema_version": SCHEMA_VERSION,
        "component": "component-suite",
        "case_id": name,
        "suite": name,
        "platform": platform_tag(),
        "python": platform.python_version(),
        "source_layers": suite["source_layers"],
        "supported_platforms": suite["platforms"],
        "tests": suite["tests"],
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "seconds": round(seconds, 2),
        "counts": parse_pytest_counts(output),
        "blocked_reasons": blockers or [],
    }


def run_suite(name: str, *, dry_run: bool, pytest_args: list[str]) -> dict[str, object]:
    """Run or plan one named component suite."""
    suite = SUITES[name]
    tests = list(suite["tests"])
    supported_platforms = list(suite["platforms"])
    command = [sys.executable, "-m", "pytest", "-q", *pytest_args, *tests]
    current_platform = platform_tag()
    if current_platform not in supported_platforms:
        return suite_record(
            name,
            suite,
            status="blocked",
            command=command,
            exit_code=None,
            seconds=0.0,
            blockers=[
                f"unsupported platform: {current_platform} "
                f"(supported: {', '.join(str(p) for p in supported_platforms)})"
            ],
        )
    blockers = missing_blockers(tests)
    if blockers:
        return suite_record(
            name,
            suite,
            status="blocked",
            command=command,
            exit_code=None,
            seconds=0.0,
            blockers=blockers,
        )
    if dry_run:
        return suite_record(
            name,
            suite,
            status="planned",
            command=command,
            exit_code=None,
            seconds=0.0,
        )

    start = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        return suite_record(
            name,
            suite,
            status="failed",
            command=command,
            exit_code=124,
            seconds=time.time() - start,
            stdout=exc.stdout or "",
            stderr=(
                f"pytest timed out after {exc.timeout}s\n"
                f"{exc.stderr or ''}"
            ),
            blockers=[f"pytest timed out after {exc.timeout}s"],
        )
    status = "passed" if proc.returncode == 0 else "failed"
    return suite_record(
        name,
        suite,
        status=status,
        command=command,
        exit_code=proc.returncode,
        seconds=time.time() - start,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def make_report(records: list[dict[str, object]]) -> dict[str, object]:
    """Build the component-suite run report."""
    return {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/run_component_suites.py",
        "platform": platform_tag(),
        "command": [sys.executable, *sys.argv],
        "suite_count": len(records),
        "status_counts": {
            status: sum(1 for record in records if record["status"] == status)
            for status in sorted({str(record["status"]) for record in records})
        },
        "records": records,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", choices=sorted(SUITES))
    parser.add_argument("--all", action="store_true", help="run all component suites")
    parser.add_argument("--dry-run", action="store_true", help="emit planned records")
    parser.add_argument("--json", action="store_true", help="print report JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="extra argument passed to pytest before test paths",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for component-suite remeasurement."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    names = sorted(SUITES) if args.all or not args.suite else args.suite
    records = [
        run_suite(name, dry_run=args.dry_run, pytest_args=args.pytest_arg)
        for name in names
    ]
    report = make_report(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    if any(record["status"] == "failed" for record in records):
        return 1
    if any(record["status"] == "blocked" for record in records):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
