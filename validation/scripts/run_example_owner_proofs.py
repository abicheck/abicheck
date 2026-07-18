#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the dedicated non-default example owners and emit proof JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"
SCHEMA_VERSION = "example_owner_proofs.v1"

OWNER_PROOFS = {
    "btf": "tests/test_workflow_kernel_accel.py::test_committed_btf_example_matches_ground_truth",
    "g20": "tests/test_g20_catalog.py",
    "header_graph": "tests/test_header_graph_examples.py",
    "kabi": "tests/test_kabi_examples.py",
    "l3l4l5": "tests/test_l3l4l5_examples.py",
    "python_api": "tests/test_python_api_examples.py",
    "reconcile": "tests/test_diff_reconcile.py::test_case164_fixtures_reconcile",
    "snapshot_pair": "tests/test_environment_drift.py::TestCase170Example",
}


#: pytest's terse (`-q`) final summary line, e.g. "3 passed, 1 skipped in
#: 0.5s" or "4 skipped in 0.3s". Used to catch a proof that "PASS"es on
#: returncode alone while every one of its cases was skipped (e.g.
#: header_graph on a host missing clang/g++) -- a skip runs zero assertions,
#: so an all-skipped run is not proof and must not report PASS.
_PASSED_RE = re.compile(r"(\d+) passed")
_SKIPPED_RE = re.compile(r"(\d+) skipped")


def _run_owner(owner: str, proof: str) -> dict[str, object]:
    command = [sys.executable, "-m", "pytest", proof, "-q"]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        passed = sum(int(n) for n in _PASSED_RE.findall(completed.stdout))
        skipped = sum(int(n) for n in _SKIPPED_RE.findall(completed.stdout))
        status = "PASS" if completed.returncode == 0 else "FAIL"
        if status == "PASS" and passed == 0 and skipped > 0:
            status = "FAIL"
            output = (
                f"all {skipped} case(s) skipped -- zero assertions ran, "
                f"not proof (missing toolchain?)\n{output}"
            )
        return {
            "owner": owner,
            "status": status,
            "proof": proof,
            "command": command,
            "returncode": completed.returncode,
            "output_tail": output[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "owner": owner,
            "status": "FAIL",
            "proof": proof,
            "command": command,
            "returncode": 124,
            "output_tail": f"timeout after {exc.timeout}s",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owners", nargs="*", choices=sorted(OWNER_PROOFS))
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args(argv)

    owners = args.owners or sorted(OWNER_PROOFS)
    results = [_run_owner(owner, OWNER_PROOFS[owner]) for owner in owners]
    summary = dict(Counter(str(row["status"]) for row in results))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/run_example_owner_proofs.py",
        "ground_truth_sha256": hashlib.sha256(GROUND_TRUTH.read_bytes()).hexdigest(),
        "selected_owners": len(owners),
        "summary": summary,
        "results": results,
    }
    if args.json_out:
        print(json.dumps(payload, indent=2))
    else:
        print("Dedicated example owners:", json.dumps(summary, sort_keys=True))
        for result in results:
            if result["status"] != "PASS":
                print(
                    f"FAIL: {result['owner']} {result['output_tail']}",
                    file=sys.stderr,
                )
    return 1 if any(row["status"] != "PASS" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
