#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build and validate the ADR-023 bundle example cases.

``tests/validate_examples.py`` intentionally validates single-library v1/v2
pairs.  Bundle examples use a release-directory shape instead:

    examples/<case>/{old,new}/<library>.cpp

This runner builds those examples through the normal examples CMake project,
runs ``abicheck compare-release`` on each old/new directory, and checks the
bundle verdict/kinds declared in ``examples/ground_truth.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_DIR / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"
SCHEMA_VERSION = "bundle_examples.v1"


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(REPO_DIR)},
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return subprocess.CompletedProcess(
            cmd,
            124,
            stdout=str(stdout),
            stderr=(str(stderr) + f"\ntimeout after {timeout}s").strip(),
        )


def _load_bundle_cases() -> dict[str, dict]:
    data = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]
    return {
        name: entry
        for name, entry in sorted(data.items())
        if entry.get("bundle") is True or entry.get("category") == "bundle"
    }


def _configure(build_dir: Path) -> str | None:
    cmake = shutil.which("cmake")
    if cmake is None:
        return "cmake not found"
    result = _run(
        [
            cmake,
            "-S",
            str(EXAMPLES_DIR),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Debug",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        return (result.stderr or result.stdout)[:1000]
    return None


def _build_case(build_dir: Path, case_name: str, entry: dict) -> str | None:
    if case_name == "case84_bundle_soname_skew":
        return _build_case84(build_dir)

    cmake = shutil.which("cmake")
    if cmake is None:
        return "cmake not found"

    libs = list(entry.get("bundle_libraries") or [])
    if not libs:
        libs = sorted(
            p.stem
            for p in (EXAMPLES_DIR / case_name / "old").glob("lib*.c*")
            if p.is_file()
        )
    if not libs:
        return "no bundle libraries declared or discovered"

    targets = [
        f"{case_name}_{side}_{lib}"
        for side in ("old", "new")
        for lib in libs
    ]
    result = _run([cmake, "--build", str(build_dir), "--target", *targets], timeout=240)
    if result.returncode != 0:
        return (result.stderr or result.stdout)[:1000]
    return None


def _build_case84(build_dir: Path) -> str | None:
    gcc = shutil.which("gcc")
    if gcc is None:
        return "gcc not found"
    case_dir = EXAMPLES_DIR / "case84_bundle_soname_skew"
    old_dir = build_dir / "case84_bundle_soname_skew" / "old"
    new_dir = build_dir / "case84_bundle_soname_skew" / "new"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        (old_dir, "onedal_core.c", "libonedal_core.so.1"),
        (old_dir, "onedal_thread.c", "libonedal_thread.so.1"),
        (old_dir, "onedal_dpc.c", "libonedal_dpc.so.1"),
        (new_dir, "onedal_core.c", "libonedal_core.so.2"),
        (new_dir, "onedal_thread.c", "libonedal_thread.so.1"),
        (new_dir, "onedal_dpc.c", "libonedal_dpc.so.2"),
    ]
    for out_dir, src_name, soname in specs:
        result = _run(
            [
                gcc,
                "-shared",
                "-fPIC",
                f"-Wl,-soname,{soname}",
                str(case_dir / src_name),
                "-o",
                str(out_dir / soname),
            ],
            timeout=60,
        )
        if result.returncode != 0:
            return (result.stderr or result.stdout)[:1000]
    return None


def _compare_release(build_dir: Path, case_name: str) -> tuple[dict | None, str | None]:
    old_dir = build_dir / case_name / "old"
    new_dir = build_dir / case_name / "new"
    cmd = [
        sys.executable,
        "-m",
        "abicheck.cli",
        "compare",
        str(old_dir),
        str(new_dir),
        "--format",
        "json",
    ]
    manifest = EXAMPLES_DIR / case_name / "manifest.yaml"
    if manifest.exists():
        cmd.extend(["--manifest", str(manifest)])
    if case_name == "case84_bundle_soname_skew":
        cmd.extend(["--bundle-cohort", "libonedal_"])

    result = _run(cmd, timeout=120)
    if result.returncode not in (0, 1, 2, 3, 4):
        return None, (result.stderr or result.stdout)[:1000]
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, result.stdout[:1000]




def _change_kinds(entry: dict) -> set[str]:
    kinds: set[str] = set()
    for key in ("changes", "findings", "bundle_findings"):
        for item in entry.get(key) or []:
            if isinstance(item, dict) and item.get("kind"):
                kinds.add(str(item["kind"]))
    return kinds


def _library_name(entry: dict) -> str:
    for key in ("library", "name", "path", "old", "new", "old_path", "new_path"):
        value = entry.get(key)
        if value:
            return Path(str(value)).name
    return ""


def _validate_expected_libraries(payload: dict, expected_libraries: dict) -> list[str]:
    if not expected_libraries:
        return []
    actual = payload.get("libraries") or payload.get("library_results") or []
    if not isinstance(actual, list):
        return ["payload has no per-library result list"]

    errors: list[str] = []
    for expected_name, expected in expected_libraries.items():
        if isinstance(expected, str):
            expected = {"verdict": expected}
        if not isinstance(expected, dict):
            errors.append(f"{expected_name}: malformed expected_libraries entry")
            continue
        matches = [
            item for item in actual
            if isinstance(item, dict) and (
                _library_name(item) == expected_name
                or str(expected_name) in str(item.get("library", ""))
                or str(expected_name) in str(item.get("name", ""))
                or str(expected_name) in str(item.get("path", ""))
            )
        ]
        if not matches:
            errors.append(f"{expected_name}: missing per-library result")
            continue
        item = matches[0]
        expected_verdict = expected.get("verdict") or expected.get("expected")
        if expected_verdict and item.get("verdict") != expected_verdict:
            errors.append(
                f"{expected_name}: expected verdict {expected_verdict!r}, "
                f"got {item.get('verdict')!r}"
            )
        expected_kinds = set(expected.get("kinds") or expected.get("expected_kinds") or [])
        if expected_kinds:
            got = _change_kinds(item)
            missing = expected_kinds - got
            unexpected = got - expected_kinds
            if missing or unexpected:
                errors.append(
                    f"{expected_name}: expected kinds {sorted(expected_kinds)!r}, "
                    f"got {sorted(got)!r}, missing={sorted(missing)!r}, "
                    f"unexpected={sorted(unexpected)!r}"
                )
    return errors

def _validate_case(case_name: str, entry: dict, build_dir: Path) -> dict:
    started = time.perf_counter()
    expected_verdict = entry.get("expected_bundle_verdict")
    expected_kinds = set(entry.get("expected_bundle_kinds") or [])

    build_err = _build_case(build_dir, case_name, entry)
    if build_err is not None:
        return {
            "case_id": case_name,
            "status": "ERROR",
            "expected": expected_verdict,
            "got": None,
            "message": f"build failed: {build_err}",
            "seconds": round(time.perf_counter() - started, 3),
        }

    payload, compare_err = _compare_release(build_dir, case_name)
    if compare_err is not None or payload is None:
        return {
            "case_id": case_name,
            "status": "ERROR",
            "expected": expected_verdict,
            "got": None,
            "message": f"compare-release failed: {compare_err}",
            "seconds": round(time.perf_counter() - started, 3),
        }

    got_verdict = payload.get("bundle_verdict") or payload.get("verdict")
    got_kinds = {f.get("kind") for f in payload.get("bundle_findings", [])}
    got_kinds.discard(None)
    missing = expected_kinds - got_kinds
    unexpected = got_kinds - expected_kinds
    if entry.get("allow_extra_bundle_kinds", True):
        unexpected = set()
    library_errors = _validate_expected_libraries(payload, entry.get("expected_libraries") or {})
    if got_verdict == expected_verdict and not missing and not unexpected and not library_errors:
        status = "PASS"
        message = ""
    else:
        status = "FAIL"
        message = (
            f"expected verdict={expected_verdict!r} kinds={sorted(expected_kinds)!r}; "
            f"got verdict={got_verdict!r} kinds={sorted(got_kinds)!r}; "
            f"missing={sorted(missing)!r}; unexpected={sorted(unexpected)!r}; "
            f"library_errors={library_errors!r}"
        )

    return {
        "case_id": case_name,
        "status": status,
        "expected": expected_verdict,
        "got": got_verdict,
        "expected_kinds": sorted(expected_kinds),
        "got_kinds": sorted(k for k in got_kinds if k),
        "message": message,
        "seconds": round(time.perf_counter() - started, 3),
    }


def _summary(results: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filters", nargs="*", help="Case-name substrings to run")
    parser.add_argument("--json", action="store_true", dest="json_out")
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args(argv)

    if sys.platform != "linux":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "runner": "validation/scripts/run_bundle_examples.py",
            "summary": {"SKIP": 1},
            "results": [
                {
                    "case_id": "bundle-corpus",
                    "status": "SKIP",
                    "message": "bundle examples are ELF/Linux-only",
                }
            ],
        }
        print(json.dumps(payload, indent=2) if args.json_out else payload["results"][0]["message"])
        return 0

    cases = _load_bundle_cases()
    if args.filters:
        cases = {
            name: entry
            for name, entry in cases.items()
            if any(f in name for f in args.filters)
        }

    with tempfile.TemporaryDirectory(prefix="abicheck-bundle-examples-") as tmp:
        build_dir = args.build_dir or Path(tmp) / "build"
        configure_err = _configure(build_dir)
        if configure_err is not None:
            print(f"ERROR: cmake configure failed: {configure_err}", file=sys.stderr)
            return 2

        results = [_validate_case(name, entry, build_dir) for name, entry in cases.items()]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/run_bundle_examples.py",
        "platform": "linux",
        "ground_truth_cases": len(cases),
        "selected_cases": len(cases),
        "summary": _summary(results),
        "results": results,
    }
    if args.json_out:
        print(json.dumps(payload, indent=2))
    else:
        print("Bundle examples:", json.dumps(payload["summary"]))
        for result in results:
            if result["status"] != "PASS":
                print(f"{result['status']}: {result['case_id']} {result['message']}")

    return 1 if any(r["status"] in {"FAIL", "ERROR"} for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
