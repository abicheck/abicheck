#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate every non-compiler, non-bundle example through public CLI commands.

The GCC/Clang lanes intentionally own only compilable single-library pairs and
``run_bundle_examples.py`` owns multi-library releases.  This runner exercises
the remaining committed fixture shapes through ``python -m abicheck``:

* BTF, reconcile, environment, and Linux kABI comparisons;
* G20 single-release ``scan`` (no ``--against``) audit cross-checks;
* L3/L4/L5 evidence packs attached to ``compare``;
* a CPython-extension comparison with sibling ``.pyi`` stubs.

Fixture packaging is orchestration only: verdicts and findings always come from
the public CLI, never from a direct detector/Python API call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

REPO_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_DIR / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"
SCHEMA_VERSION = "special_cli_examples.v2"
RUNNER = "validation/scripts/run_special_cli_examples.py"
BASE_SNAPSHOT = EXAMPLES_DIR / "case170_env_runtime_floor_raised" / "old.abi.json"


class CompareSpec(NamedTuple):
    old: str
    new: str
    args: tuple[str, ...] = ()


class EvidenceSpec(NamedTuple):
    payload_rel: str
    option: str


COMPARE_CASES: dict[str, CompareSpec] = {
    "case121_kernel_btf_struct_field_added": CompareSpec("v1.btf", "v2.btf"),
    "case164_preproc_conditional_field": CompareSpec(
        "v1.abi.json",
        "v2.abi.json",
        ("--scope-public-headers", "--reconcile-build-context"),
    ),
    "case170_env_runtime_floor_raised": CompareSpec("old.abi.json", "new.abi.json"),
    "case175_kabi_crc_changed": CompareSpec("v1.symvers", "v2.symvers"),
    "case176_kabi_symbol_namespace_changed": CompareSpec("v1.symvers", "v2.symvers"),
}

SCAN_CASES = {
    "case143_audit_accidental_export",
    "case144_audit_private_header_leak",
    "case145_audit_unversioned_export",
    "case146_audit_rtti_for_internal",
    "case147_scan_depth_ladder",
    "case148_xcheck_header_build_mismatch",
    "case149_xcheck_odr_variant",
    "case150_xcheck_export_public_pair",
    "case151_xcheck_provider_matrix",
    "case181_xcheck_public_to_internal_dependency",
}

EVIDENCE_CASES: dict[str, EvidenceSpec] = {
    "case152_enum_size_flag_flip": EvidenceSpec(
        "build/build_evidence.json", "--build-info"
    ),
    "case153_struct_packing_flip": EvidenceSpec(
        "build/build_evidence.json", "--build-info"
    ),
    "case154_lto_mode_flip": EvidenceSpec("build/build_evidence.json", "--build-info"),
    "case155_char_signedness_flip": EvidenceSpec(
        "build/build_evidence.json", "--build-info"
    ),
    "case156_public_macro_removed": EvidenceSpec("source/source_abi.json", "--sources"),
    "case157_inline_function_removed": EvidenceSpec(
        "source/source_abi.json", "--sources"
    ),
    "case158_public_typedef_removed": EvidenceSpec(
        "source/source_abi.json", "--sources"
    ),
    "case160_public_api_internal_dep_added": EvidenceSpec(
        "graph/source_graph_summary.json", "--sources"
    ),
    "case161_target_dependency_added": EvidenceSpec(
        "graph/source_graph_summary.json", "--sources"
    ),
    "case162_symbol_source_owner_changed": EvidenceSpec(
        "graph/source_graph_summary.json", "--sources"
    ),
    "case187_public_struct_private_field_type": EvidenceSpec(
        "graph/source_graph_summary.json", "--sources"
    ),
}

PYTHON_CASE = "case163_python_kwarg_renamed"
CASE_IDS = set(COMPARE_CASES) | SCAN_CASES | set(EVIDENCE_CASES) | {PYTHON_CASE}


def _ground_truth() -> dict[str, dict[str, Any]]:
    return json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]


def _expected_returncode(verdict: str) -> int:
    # Public compare contract: source breaks exit 2 and ABI breaks exit 4.
    # These are semantic findings, not operational failures.
    if verdict == "API_BREAK":
        return 2
    if verdict == "BREAKING":
        return 4
    return 0


def _change_kinds(payload: dict[str, Any]) -> set[str]:
    return {
        str(item["kind"])
        for key in ("changes", "findings")
        for item in (payload.get(key) or [])
        if isinstance(item, dict) and item.get("kind")
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_json_command(command: list[str], timeout: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(REPO_DIR)},
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "payload": None,
            "message": f"public CLI timed out after {timeout}s",
            "stdout": str(exc.stdout or "")[-1000:],
            "stderr": str(exc.stderr or "")[-1000:],
            "seconds": round(time.perf_counter() - started, 3),
        }

    try:
        decoded = json.loads(completed.stdout)
        if isinstance(decoded, dict):
            payload = decoded
            message = ""
        else:
            payload = None
            message = (
                f"public CLI JSON root must be an object, got {type(decoded).__name__}"
            )
    except json.JSONDecodeError as exc:
        payload = None
        message = f"public CLI did not emit valid JSON: {exc}"

    return {
        "returncode": completed.returncode,
        "payload": payload,
        "message": message,
        "stdout": completed.stdout[-1000:] if payload is None else "",
        "stderr": completed.stderr[-1000:],
        "seconds": round(time.perf_counter() - started, 3),
    }


def _result(
    case_id: str,
    command: list[str],
    entry: dict[str, Any],
    execution: dict[str, Any],
    errors: list[str],
    *,
    got: str | None,
    got_kinds: set[str],
    input_sha256: dict[str, str],
    setup_commands: list[list[str]] | None = None,
) -> dict[str, Any]:
    expected = str(entry.get("expected"))
    return {
        "case_id": case_id,
        "status": "FAIL" if errors else "PASS",
        "command": command,
        "setup_commands": setup_commands or [],
        "returncode": execution["returncode"],
        "expected_returncode": _expected_returncode(expected),
        "expected": expected,
        "got": got,
        "expected_kinds": sorted(entry.get("expected_kinds") or []),
        "expected_absent_kinds": sorted(entry.get("expected_absent_kinds") or []),
        "got_kinds": sorted(got_kinds),
        "input_sha256": input_sha256,
        "message": "; ".join(errors),
        "stderr": execution["stderr"],
        "seconds": execution["seconds"],
    }


def _validate_compare(
    case_id: str,
    command: list[str],
    entry: dict[str, Any],
    execution: dict[str, Any],
    input_sha256: dict[str, str],
    *,
    setup_commands: list[list[str]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if execution["message"]:
        errors.append(str(execution["message"]))
    payload = execution["payload"] or {}
    expected = str(entry.get("expected"))
    expected_rc = _expected_returncode(expected)
    got = payload.get("verdict")
    expected_kinds = set(entry.get("expected_kinds") or [])
    expected_absent = set(entry.get("expected_absent_kinds") or [])
    got_kinds = _change_kinds(payload)
    if execution["returncode"] != expected_rc:
        errors.append(
            f"exit code {execution['returncode']}, expected {expected_rc} for {expected}"
        )
    if got != expected:
        errors.append(f"verdict {got!r}, expected {expected!r}")
    if missing := expected_kinds - got_kinds:
        errors.append(f"missing kinds {sorted(missing)!r}")
    if forbidden := expected_absent & got_kinds:
        errors.append(f"forbidden kinds present {sorted(forbidden)!r}")
    return _result(
        case_id,
        command,
        entry,
        execution,
        errors,
        got=got,
        got_kinds=got_kinds,
        input_sha256=input_sha256,
        setup_commands=setup_commands,
    )


def _run_compare_case(
    case_id: str, spec: CompareSpec, entry: dict[str, Any], timeout: int
) -> dict[str, Any]:
    case_dir = EXAMPLES_DIR / case_id
    old = case_dir / spec.old
    new = case_dir / spec.new
    command = [
        sys.executable,
        "-m",
        "abicheck",
        "compare",
        str(old),
        str(new),
        *spec.args,
        "--format",
        "json",
    ]
    return _validate_compare(
        case_id,
        command,
        entry,
        _run_json_command(command, timeout),
        {spec.old: _sha256(old), spec.new: _sha256(new)},
    )


def _run_scan_case(case_id: str, entry: dict[str, Any], timeout: int) -> dict[str, Any]:
    snapshot = EXAMPLES_DIR / case_id / "snapshot.abi.json"
    command = [
        sys.executable,
        "-m",
        "abicheck",
        "scan",
        str(snapshot),
        "--format",
        "json",
    ]
    execution = _run_json_command(command, timeout)
    errors: list[str] = []
    if execution["message"]:
        errors.append(str(execution["message"]))
    payload = execution["payload"] or {}
    expected = str(entry.get("expected"))
    expected_rc = _expected_returncode(expected)
    got = payload.get("verdict")
    crosscheck = payload.get("crosscheck") or {}
    got_kinds = set((crosscheck.get("counts_by_check") or {}).keys())
    expected_kinds = set(entry.get("expected_kinds") or [])
    if execution["returncode"] != expected_rc:
        errors.append(
            f"exit code {execution['returncode']}, expected {expected_rc} for {expected}"
        )
    if payload.get("exit_code") != expected_rc:
        errors.append(
            f"JSON exit_code {payload.get('exit_code')!r}, expected {expected_rc}"
        )
    if got != expected:
        errors.append(f"scan verdict {got!r}, expected {expected!r}")
    if got_kinds != expected_kinds:
        errors.append(
            f"cross-check kinds {sorted(got_kinds)!r}, expected exact {sorted(expected_kinds)!r}"
        )
    providers = crosscheck.get("providers") or {}
    for kind, provider_assertions in (entry.get("provider_assertions") or {}).items():
        if providers.get(kind) != provider_assertions:
            errors.append(
                f"providers for {kind}: {providers.get(kind)!r}, "
                f"expected {provider_assertions!r}"
            )
    result = _result(
        case_id,
        command,
        entry,
        execution,
        errors,
        got=got,
        got_kinds=got_kinds,
        input_sha256={"snapshot.abi.json": _sha256(snapshot)},
    )
    result["expected_kinds"] = sorted(expected_kinds)
    return result


def _make_pack(
    case_id: str, side: str, spec: EvidenceSpec, root: Path
) -> tuple[Path, Path]:
    source = EXAMPLES_DIR / case_id / f"{side}.json"
    pack = root / f"{case_id}-{side}"
    destination = pack / spec.payload_rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    (pack / "manifest.json").write_text(
        json.dumps({"build_source_pack_version": 1}) + "\n", encoding="utf-8"
    )
    return pack, source


def _run_evidence_case(
    case_id: str,
    spec: EvidenceSpec,
    entry: dict[str, Any],
    timeout: int,
    temp_root: Path,
) -> dict[str, Any]:
    old_pack, old_source = _make_pack(case_id, "old", spec, temp_root)
    new_pack, new_source = _make_pack(case_id, "new", spec, temp_root)
    command = [
        sys.executable,
        "-m",
        "abicheck",
        "compare",
        str(BASE_SNAPSHOT),
        str(BASE_SNAPSHOT),
        spec.option,
        f"old={old_pack}",
        spec.option,
        f"new={new_pack}",
        "--format",
        "json",
    ]
    return _validate_compare(
        case_id,
        command,
        entry,
        _run_json_command(command, timeout),
        {
            "old.json": _sha256(old_source),
            "new.json": _sha256(new_source),
            "base_snapshot": _sha256(BASE_SNAPSHOT),
        },
    )


def _run_python_case(
    entry: dict[str, Any], timeout: int, temp_root: Path
) -> dict[str, Any]:
    case_dir = EXAMPLES_DIR / PYTHON_CASE
    root = temp_root / PYTHON_CASE
    old_dir = root / "old"
    new_dir = root / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    source = root / "module.c"
    source.write_text("void *PyInit_demo(void) { return 0; }\n", encoding="utf-8")
    old_binary = old_dir / "demo.so"
    new_binary = new_dir / "demo.so"
    setup_commands = [
        ["cc", "-shared", "-fPIC", str(source), "-o", str(old_binary)],
        ["cc", "-shared", "-fPIC", str(source), "-o", str(new_binary)],
    ]
    started = time.perf_counter()
    setup_error = ""
    setup_stderr = ""
    setup_rc = 0
    for setup in setup_commands:
        try:
            completed = subprocess.run(
                setup,
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            setup_rc = 124
            setup_stderr += str(exc.stderr or "")
            setup_error = f"setup command timed out after {timeout}s: {setup!r}"
            break
        except OSError as exc:
            setup_rc = 127
            setup_error = f"setup command could not start: {exc}"
            break
        setup_rc = completed.returncode
        setup_stderr += completed.stderr
        if setup_rc != 0:
            setup_error = f"setup command failed with exit code {setup_rc}: {setup!r}"
            break
    shutil.copyfile(case_dir / "v1.pyi", old_dir / "demo.pyi")
    shutil.copyfile(case_dir / "v2.pyi", new_dir / "demo.pyi")
    command = [
        sys.executable,
        "-m",
        "abicheck",
        "compare",
        str(old_binary),
        str(new_binary),
        "--format",
        "json",
    ]
    if setup_error:
        execution = {
            "returncode": setup_rc,
            "payload": None,
            "message": setup_error,
            "stdout": "",
            "stderr": setup_stderr[-1000:],
            "seconds": round(time.perf_counter() - started, 3),
        }
    else:
        execution = _run_json_command(command, timeout)
    return _validate_compare(
        PYTHON_CASE,
        command,
        entry,
        execution,
        {
            "v1.pyi": _sha256(case_dir / "v1.pyi"),
            "v2.pyi": _sha256(case_dir / "v2.pyi"),
        },
        setup_commands=setup_commands,
    )


def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(result["status"]) for result in results))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filters", nargs="*", help="Case-name substrings to run")
    parser.add_argument("--json", action="store_true", dest="json_out")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    ground_truth = _ground_truth()
    missing_specs = sorted(CASE_IDS - set(ground_truth))
    if missing_specs:
        print(
            "ERROR: special CLI specs missing from ground truth: "
            + ", ".join(missing_specs),
            file=sys.stderr,
        )
        return 2

    selected = sorted(
        case_id
        for case_id in CASE_IDS
        if not args.filters or any(value in case_id for value in args.filters)
    )
    with tempfile.TemporaryDirectory(prefix="abicheck-special-cli-") as temp:
        temp_root = Path(temp)
        results = []
        for case_id in selected:
            entry = ground_truth[case_id]
            if case_id in COMPARE_CASES:
                result = _run_compare_case(
                    case_id, COMPARE_CASES[case_id], entry, args.timeout
                )
            elif case_id in SCAN_CASES:
                result = _run_scan_case(case_id, entry, args.timeout)
            elif case_id in EVIDENCE_CASES:
                result = _run_evidence_case(
                    case_id,
                    EVIDENCE_CASES[case_id],
                    entry,
                    args.timeout,
                    temp_root,
                )
            else:
                result = _run_python_case(entry, args.timeout, temp_root)
            results.append(result)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "runner": RUNNER,
        "ground_truth_sha256": hashlib.sha256(GROUND_TRUTH.read_bytes()).hexdigest(),
        "platform": "linux" if sys.platform.startswith("linux") else sys.platform,
        "ground_truth_cases": len(CASE_IDS),
        "selected_cases": len(selected),
        "summary": _summary(results),
        "results": results,
    }

    if args.json_out:
        print(json.dumps(payload, indent=2))
    else:
        print("Special CLI examples:", json.dumps(payload["summary"], sort_keys=True))
        for result in results:
            if result["status"] != "PASS":
                print(f"{result['status']}: {result['case_id']} {result['message']}")

    return 1 if any(result["status"] != "PASS" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
