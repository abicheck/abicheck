#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Single entry point: run every example case against one tool configuration.

``docs/development/examples-validation-runbook.md`` documents the full
catalog as a sequence of separate runner invocations (compiler lanes,
build-source, bundle, special-CLI, runtime smoke, owner proofs) fed into
``collect_full_example_matrix.py``. That is still what happens under the
hood here — the fixture shapes genuinely differ (compilable v1/v2 pairs vs.
committed snapshots vs. ``.symvers`` vs. build-source JSON, etc.), so there
is no single tool that can build all of them directly. This script is the
*one command* that drives all of those runners for a chosen tool
configuration and reports one row per ground-truth case.

Tool configuration == ``--toolchain``, the compiler family used to build
each case's ``v1.so``/``v2.so`` (independent of ``ABICHECK_AST_FRONTEND``,
which selects abicheck's own L2 header parser and is left to the caller's
environment).

``--toolchain auto`` (the default) does not mean "run every case once and
accept whatever a single blanket compiler gives you". It means: build each
case with the platform-native default family (gcc on Linux), then for the
specific cases that have a documented toolchain-scoped ``known_gap`` (e.g.
case64 needs Clang for ``DW_AT_calling_convention`` on ``ms_abi``) or that
skip under the default family for a real compiler-capability reason (e.g.
case115's C23 ``_BitInt`` on an older GCC), retry *only those cases* with
the other family and keep whichever result is better. Every other case
keeps the default family's result. ``--toolchain gcc``/``clang``/``msvc``
still force one blanket family for every case (matching the existing
``tests/validate_examples.py --toolchain`` contract) when you deliberately
want a single-producer lane instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).resolve().parents[2]
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"
DEFAULT_RESULTS_DIR = REPO_DIR / "results"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_full_example_matrix as _matrix  # noqa: E402

sys.path.insert(0, str(REPO_DIR / "tests"))
import validate_examples as _ve  # noqa: E402

BUILD_SOURCE_CASES = (
    "case01",
    "case04",
    "case98",
    "case105",
    "case122",
    "case129",
    "case130",
    "case131",
    "case132",
    "case133",
)
OWNER_NAMES = (
    "btf",
    "g20",
    "kabi",
    "l3l4l5",
    "python_api",
    "reconcile",
    "snapshot_pair",
)
RETRYABLE_SKIP_PREFIX = "compiler lacks required feature"
_ALT_COMPILER_PROBE = {
    "gcc": ("gcc", "g++"),
    # Must match tests/validate_examples.py._find_compiler's own clang
    # candidate list, which tries the versioned name first (Codex review):
    # a host with only clang-18/clang++-18 installed (no bare `clang` alias)
    # would otherwise fail this probe and skip an available retry, even
    # though validate_examples.py --toolchain clang would succeed.
    "clang": ("clang-18", "clang++-18", "clang", "clang++"),
}


def _run_json(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        cmd, cwd=REPO_DIR, capture_output=True, text=True, check=False
    )
    if proc.returncode not in (0, 1):
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return json.loads(proc.stdout)


def _family_of(compiler_name: str) -> str:
    return "clang" if "clang" in compiler_name else "gcc"


def _alternate(family: str) -> str:
    return "gcc" if family == "clang" else "clang"


def _has_compiler(family: str) -> bool:
    return any(shutil.which(c) for c in _ALT_COMPILER_PROBE[family])


def _case_family(name: str, family_c: str, family_cxx: str) -> str:
    """The compiler family that actually built *name*, honoring split CC/CXX.

    ``tests/validate_examples.py`` reports ``compiler_c``/``compiler_cxx``
    separately (it picks per-source-language: ``.c`` vs ``.cpp``), so a run
    with e.g. ``CC=gcc CXX=clang++`` builds C cases with gcc and C++ cases
    with clang under the *same* ``--toolchain auto`` invocation. Blanket-
    labeling every case with ``family_cxx`` mislabels C cases and — worse —
    picks the wrong alternate family for their retry decision (Codex
    review). Falls back to *family_cxx* when the case's own source can't be
    resolved (mirrors ``_resolve_case_sources``'s own error contract).
    """
    resolved = _ve._resolve_case_sources(name, None)
    # `CaseResult` is itself a NamedTuple (so a tuple instance too) -- the
    # error leg must be excluded explicitly, not just `isinstance(_, tuple)`,
    # or a missing/unresolvable case name misparses its CaseResult fields as
    # (case_dir, sources) and crashes on the unpack.
    if not isinstance(resolved, _ve.CaseResult):
        _case_dir, (v1_src, _v2_src, _v1_hdr, _v2_hdr) = resolved
        if v1_src.suffix == ".c":
            return family_c
    return family_cxx


def _retry_candidates(
    primary_results: list[dict[str, Any]], gt: dict[str, Any], alt_family: str
) -> list[str]:
    names = []
    for r in primary_results:
        if r["status"] == "SKIP" and r["message"].startswith(RETRYABLE_SKIP_PREFIX):
            names.append(r["name"])
        elif r["status"] == "XFAIL":
            gaps = gt.get(r["name"], {}).get("known_gap_toolchains")
            if gaps and alt_family not in gaps:
                names.append(r["name"])
    return names


def _run_compiler_lane(
    toolchain: str,
) -> tuple[dict[str, dict[str, Any]], str, list[str]]:
    """Return ({case_name: result}, human-readable description, retry failures).

    The third element surfaces a toolchain-sensitive retry that itself
    FAIL/ERROR/BUILD_ERROR-ed, or produced no result row at all (Codex
    review): silently keeping the primary (gcc) XFAIL/SKIP result in that
    case would let ``_single_library_status`` promote it to COVERED via its
    own ``source_smoke`` oracle, hiding a real regression in the alternate
    toolchain path behind a match that never actually proved anything.
    """
    primary = _run_json(
        [
            sys.executable,
            "tests/validate_examples.py",
            "--toolchain",
            toolchain,
            "--json",
        ]
    )
    # Even a forced --toolchain doesn't fail closed (Codex review):
    # tests/validate_examples.py._find_compiler falls back to another family
    # when the requested one isn't on PATH, so compiler_c/compiler_cxx in the
    # JSON metadata is the *actual* producer, not necessarily `toolchain`.
    # Blindly labeling every row `toolchain_used=toolchain` would then claim
    # a family that never actually built the case.
    primary_family_c = _family_of(primary.get("compiler_c", "gcc"))
    primary_family_cxx = _family_of(primary.get("compiler_cxx", "gcc"))
    case_family = {
        r["name"]: _case_family(r["name"], primary_family_c, primary_family_cxx)
        for r in primary["results"]
    }
    if toolchain != "auto":
        by_case = {
            r["name"]: {**r, "toolchain_used": case_family[r["name"]]}
            for r in primary["results"]
        }
        return by_case, f"toolchain={toolchain} (forced for every case)", []

    gt = json.loads(GROUND_TRUTH.read_text())["verdicts"]
    by_case = {
        r["name"]: {**r, "toolchain_used": case_family[r["name"]]}
        for r in primary["results"]
    }

    # A split CC/CXX (e.g. CC=gcc CXX=clang++) means a C case's alternate is
    # the opposite of family_c while a C++ case's alternate is the opposite
    # of family_cxx — these can differ, so candidates are grouped by their
    # own case's alternate family and retried in separate batches (Codex
    # review) rather than one blanket `--toolchain <alt>` call that would
    # force the wrong family onto whichever language disagrees.
    candidates_by_alt: dict[str, list[str]] = {}
    for r in primary["results"]:
        alt_fam = _alternate(case_family[r["name"]])
        candidates_by_alt.setdefault(alt_fam, []).extend(
            _retry_candidates([r], gt, alt_fam)
        )
    candidates_by_alt = {
        fam: names for fam, names in candidates_by_alt.items() if names
    }

    retried = 0
    retry_failures: list[str] = []
    total_candidates = sum(len(names) for names in candidates_by_alt.values())
    attempted_candidates = 0
    for alt_family, names in candidates_by_alt.items():
        if not _has_compiler(alt_family):
            sys.stderr.write(
                f"note: {len(names)} toolchain-sensitive case(s) found but no {alt_family} "
                "compiler on PATH — keeping the base-family result for them.\n"
            )
            continue
        attempted_candidates += len(names)
        alt_result = _run_json(
            [
                sys.executable,
                "tests/validate_examples.py",
                "--toolchain",
                alt_family,
                "--json",
                *names,
            ]
        )
        alt_by_case = {r["name"]: r for r in alt_result["results"]}
        for name in names:
            alt_r = alt_by_case.get(name)
            if alt_r is None:
                retry_failures.append(
                    f"{name}: {alt_family} retry produced no result row"
                )
                continue
            if alt_r["status"] == "PASS" and by_case[name]["status"] != "PASS":
                by_case[name] = {**alt_r, "toolchain_used": alt_family}
                retried += 1
            elif alt_r["status"] in ("FAIL", "ERROR", "BUILD_ERROR"):
                retry_failures.append(
                    f"{name}: {alt_family} retry returned {alt_r['status']}"
                )
    desc = (
        f"toolchain=auto (base c={primary_family_c}/cxx={primary_family_cxx}; "
        f"{total_candidates} toolchain-sensitive case(s) found, "
        f"{attempted_candidates} retried, {retried} improved to PASS)"
    )
    return by_case, desc, retry_failures


def _resolve_single_library(
    name: str,
    entry: dict[str, Any],
    compiler_result: dict[str, Any] | None,
    build_source_result: dict[str, Any] | None,
) -> dict[str, Any]:
    lanes = [
        _matrix._lane_record("auto-debug-headers", compiler_result),
        _matrix._lane_record("build-source", build_source_result),
    ]
    status, proof_lane, note = _matrix._single_library_status(name, entry, lanes)
    toolchain_used = (compiler_result or {}).get("toolchain_used")
    return {
        "status": status,
        "proof_lane": proof_lane,
        "note": note,
        "toolchain_used": toolchain_used,
        "lanes": lanes,
    }


def _resolve_bundle(name: str, bundle_result: dict[str, Any] | None) -> dict[str, Any]:
    lane = _matrix._lane_record("bundle-compare-release", bundle_result)
    if bundle_result is None:
        return {
            "status": "UNRESOLVED",
            "proof_lane": "bundle-compare-release",
            "note": "bundle runner did not report this case",
            "lanes": [lane],
        }
    if bundle_result.get("status") == "PASS":
        return {
            "status": "COVERED",
            "proof_lane": "bundle-compare-release",
            "note": "",
            "lanes": [lane],
        }
    if bundle_result.get("status") in {"FAIL", "ERROR"}:
        return {
            "status": "FAILED",
            "proof_lane": "bundle-compare-release",
            "note": bundle_result.get("message", ""),
            "lanes": [lane],
        }
    return {
        "status": "UNRESOLVED",
        "proof_lane": "bundle-compare-release",
        "note": bundle_result.get("message", ""),
        "lanes": [lane],
    }


def _resolve_special(
    name: str, special_result: dict[str, Any] | None
) -> dict[str, Any]:
    lane = _matrix._lane_record("special-abicheck-cli", special_result)
    status, proof_lane, note = _matrix._special_cli_status(special_result)
    return {"status": status, "proof_lane": proof_lane, "note": note, "lanes": [lane]}


def _artifact_failures(
    gt: dict[str, Any],
    proofs: dict[str, Any],
    bundle: dict[str, Any],
    special_cli: dict[str, Any],
    runtime: dict[str, Any],
    build_source: dict[str, Any],
) -> list[str]:
    """Surface proof/runtime-smoke artifact problems the per-case matrix can't see.

    Per-case ``status`` only reflects the lane that proved *that* case's
    verdict — the dedicated-owner proofs, bundle, special-CLI, build-source,
    and runtime-smoke lanes are independent regression checks layered on
    top, so a failure (or an incomplete artifact — a missing/duplicate
    owner, a partial case list) there must not be silently absorbed into an
    all-COVERED matrix. Delegating to
    ``collect_full_example_matrix``'s own ``_proof_artifact_errors``/
    ``_artifact_errors`` — rather than a bespoke, narrower re-check here —
    is deliberate (Codex review): those already validate runner/schema
    identity, missing/duplicate/unexpected case or owner ids, declared vs.
    recomputed summaries, and bad statuses (including a *missing* owner
    row, which a hand-rolled "iterate present rows" loop can't see at all,
    and a build-source FAIL/ERROR for an L3+ proof case that the normal
    compiler lane happens to pass for a different reason — Codex review),
    and these artifacts are genuine, unmodified output of the same
    sub-runners the collector itself consumes.
    """
    bundle_cases = {
        name
        for name, entry in gt.items()
        if _matrix._case_owner(name, entry) == "bundle"
    }
    special_cli_cases = {
        name
        for name, entry in gt.items()
        if _matrix._case_owner(name, entry) not in {"single-library", "bundle"}
    }
    all_cases = set(gt)
    return [
        *_matrix._proof_artifact_errors(proofs),
        *_matrix._artifact_errors("bundle", bundle, expected_cases=bundle_cases),
        *_matrix._artifact_errors(
            "special_cli", special_cli, expected_cases=special_cli_cases
        ),
        *_matrix._artifact_errors("runtime", runtime, expected_cases=all_cases),
        *_matrix._artifact_errors(
            "build_source",
            build_source,
            expected_cases=_matrix.BUILD_SOURCE_PROOF_CASES,
        ),
    ]


def run_full_catalog(toolchain: str, results_dir: Path) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)

    compiler_by_case, compiler_desc, retry_failures = _run_compiler_lane(toolchain)

    build_source_toolchain = [] if toolchain == "auto" else ["--toolchain", toolchain]
    build_source = _run_json(
        [
            sys.executable,
            "tests/validate_examples.py",
            *BUILD_SOURCE_CASES,
            "--artifact-variant",
            "build-source",
            "--json",
            *build_source_toolchain,
        ]
    )
    build_source_by_case = {r["name"]: r for r in build_source["results"]}

    bundle = _run_json(
        [sys.executable, "validation/scripts/run_bundle_examples.py", "--json"]
    )
    bundle_by_case = {r["case_id"]: r for r in bundle["results"]}

    special_cli = _run_json(
        [sys.executable, "validation/scripts/run_special_cli_examples.py", "--json"]
    )
    special_by_case = {r["case_id"]: r for r in special_cli["results"]}

    runtime = _run_json(
        [sys.executable, "validation/scripts/run_example_runtime_smoke.py", "--json"]
    )
    runtime_by_case = {r["case_id"]: r for r in runtime["results"]}

    proofs = _run_json(
        [
            sys.executable,
            "validation/scripts/run_example_owner_proofs.py",
            *OWNER_NAMES,
            "--json",
        ]
    )

    (results_dir / f"validate-examples-{toolchain}.json").write_text(
        json.dumps({name: r for name, r in compiler_by_case.items()}, indent=2)
    )
    (results_dir / "validate-examples-build-source.json").write_text(
        json.dumps(build_source, indent=2)
    )
    (results_dir / "bundle-examples.json").write_text(json.dumps(bundle, indent=2))
    (results_dir / "special-cli-examples.json").write_text(
        json.dumps(special_cli, indent=2)
    )
    (results_dir / "example-runtime-smoke.json").write_text(
        json.dumps(runtime, indent=2)
    )
    (results_dir / "example-owner-proofs.json").write_text(json.dumps(proofs, indent=2))

    gt = json.loads(GROUND_TRUTH.read_text())["verdicts"]
    rows = []
    for name, entry in sorted(gt.items()):
        owner = _matrix._case_owner(name, entry)
        if owner == "single-library":
            resolved = _resolve_single_library(
                name,
                entry,
                compiler_by_case.get(name),
                build_source_by_case.get(name),
            )
        elif owner == "bundle":
            resolved = _resolve_bundle(name, bundle_by_case.get(name))
        elif owner in _matrix.SPECIAL_PROOFS:
            resolved = _resolve_special(name, special_by_case.get(name))
        else:  # pragma: no cover - defensive future-proofing
            resolved = {
                "status": "UNRESOLVED",
                "proof_lane": owner,
                "note": "unknown owner",
                "lanes": [],
            }
        runtime_lane = runtime_by_case.get(name)
        row = {
            "case_id": name,
            "owner": owner,
            "expected": entry.get("expected"),
            "status": resolved["status"],
            "proof_lane": resolved["proof_lane"],
            "toolchain_used": resolved.get("toolchain_used"),
            "note": resolved["note"],
            "lanes": resolved["lanes"],
        }
        if runtime_lane is not None:
            row["runtime_smoke"] = {
                "status": runtime_lane.get("status"),
                "message": runtime_lane.get("message", ""),
            }
        rows.append(row)

    from collections import Counter

    counts = Counter(row["status"] for row in rows)
    unresolved = [row["case_id"] for row in rows if row["status"] == "UNRESOLVED"]
    failed = [row["case_id"] for row in rows if row["status"] == "FAILED"]
    owner_summary = proofs.get("summary", {})
    artifact_errors = (
        _artifact_failures(gt, proofs, bundle, special_cli, runtime, build_source)
        + retry_failures
    )

    return {
        "schema_version": "full_catalog_single_config.v1",
        "runner": "validation/scripts/run_full_catalog.py",
        "requested_toolchain": toolchain,
        "compiler_lane": compiler_desc,
        "ground_truth_cases": len(gt),
        "summary": dict(sorted(counts.items())),
        "owner_proofs_summary": owner_summary,
        "unresolved_cases": unresolved,
        "failed_cases": failed,
        "artifact_errors": artifact_errors,
        "results": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--toolchain",
        choices=("auto", "gcc", "clang", "msvc"),
        default="auto",
        help="Compiler family/configuration to run every example case against "
        "(default: auto — base family with per-case toolchain-sensitive retry).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_RESULTS_DIR / "full-catalog.json",
        help="Path to write the consolidated one-row-per-case JSON matrix.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Directory to write the individual runner JSON artifacts into.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Print the matrix JSON to stdout.",
    )
    args = parser.parse_args(argv)

    matrix = run_full_catalog(args.toolchain, args.results_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(matrix, indent=2))

    if args.json_out:
        print(json.dumps(matrix, indent=2))
    else:
        total = matrix["ground_truth_cases"]
        print(f"{args.toolchain}: {matrix['compiler_lane']}")
        print(f"summary: {matrix['summary']}  ({total} ground-truth cases)")
        if matrix["unresolved_cases"]:
            print(f"UNRESOLVED: {matrix['unresolved_cases']}", file=sys.stderr)
        if matrix["failed_cases"]:
            print(f"FAILED: {matrix['failed_cases']}", file=sys.stderr)
        if matrix["artifact_errors"]:
            print(f"ARTIFACT ERRORS: {matrix['artifact_errors']}", file=sys.stderr)
        print(f"matrix written to {args.out}")

    return (
        1
        if (
            matrix["unresolved_cases"]
            or matrix["failed_cases"]
            or matrix["artifact_errors"]
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
