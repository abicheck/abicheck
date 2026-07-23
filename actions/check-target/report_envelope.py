#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""CLI wrapper backing ``actions/check-target/run.sh`` (G30 P1.3, ADR-047
§7).

Thin argparse wrapper around ``abicheck.buildsource.check_report``'s pure
report-envelope construction -- mirrors ``actions/resolve-baseline/
resolve_baseline.py``'s pattern: reads/writes the report JSON file, prints
``key=value`` lines on stdout that ``run.sh`` forwards to ``GITHUB_OUTPUT``.

Exactly one of three modes runs, selected by ``--mode``:

- ``augment`` -- the common path: read the analysis step's own JSON report
  (``--report-in``), layer the ADR-047 §7 identity/new fields onto it, write
  the result to ``--report-out``.
- ``operational-error`` -- ``resolve-baseline`` failed; synthesize a report
  from scratch (no ``--report-in``).
- ``bootstrap`` -- ``resolve-baseline`` returned ``not_found``/bootstrap; no
  comparison ever ran, synthesize the advisory "no baseline yet" report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from abicheck.buildsource.check_report import (
    RESOLVE_FAILURE_OUTCOMES,
    augment_report,
    build_bootstrap_report,
    build_operational_error_report,
    final_exit_code,
)

#: Usage error, matching the repo-wide convention documented in AGENTS.md
#: ("64 = usage error (bad flags/inputs)").
_EXIT_USAGE_ERROR = 64


def _print_outputs(fields: dict[str, str]) -> int:
    """Print ``key=value`` lines ``run.sh`` forwards to ``GITHUB_OUTPUT``.

    Same newline guard as ``resolve_baseline.py``'s ``_print_outputs``: an
    embedded newline in any value would corrupt ``$GITHUB_OUTPUT``'s
    line-oriented parsing.
    """
    for key, value in fields.items():
        if "\n" in value or "\r" in value:
            print(
                f"::error::internal error: check-target output {key!r} "
                "contains a newline, which would corrupt GITHUB_OUTPUT -- "
                "refusing to write it.",
                file=sys.stderr,
            )
            return _EXIT_USAGE_ERROR
    for key, value in fields.items():
        print(f"{key}={value}")
    return 0


def _outputs_for_report(report: dict[str, object], report_out: Path) -> dict[str, str]:
    verdict = str(report.get("verdict") or "")
    compat = report.get("compatibility_verdict")
    return {
        "check-id": str(report.get("check_id") or ""),
        "verdict": verdict,
        "compatibility-verdict": str(compat) if compat is not None else "",
        "policy-gate-decision": str(report.get("policy_gate_decision") or ""),
        "report-path": str(report_out),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", required=True, choices=["augment", "operational-error", "bootstrap"]
    )
    parser.add_argument(
        "--report-in",
        default="",
        help="Path to the analysis step's own JSON report (mode: augment only)",
    )
    parser.add_argument("--report-out", required=True, type=Path)
    parser.add_argument("--name", required=True, help="target or bundle id")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--baseline-channel", required=True)
    parser.add_argument("--requested-depth", required=True)
    parser.add_argument(
        "--gate-mode", default="local", choices=["local", "deferred", "advisory"]
    )
    parser.add_argument("--resolve-outcome", default="")
    parser.add_argument("--resolve-message", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--tool-version", default="")
    parser.add_argument("--action-version", default="")
    args = parser.parse_args(argv)

    project = args.project or None
    head_sha = args.head_sha or None
    base_ref = args.base_ref or None
    action_version = args.action_version or None
    tool_version = args.tool_version or None

    operational_error = False

    if args.mode == "operational-error":
        if args.resolve_outcome not in RESOLVE_FAILURE_OUTCOMES:
            print(
                f"::error::--resolve-outcome {args.resolve_outcome!r} is not a "
                f"recognized resolve-baseline failure outcome (expected one of "
                f"{sorted(RESOLVE_FAILURE_OUTCOMES)})",
                file=sys.stderr,
            )
            return _EXIT_USAGE_ERROR
        report = build_operational_error_report(
            name=args.name,
            profile_id=args.profile_id,
            baseline_channel=args.baseline_channel,
            requested_depth=args.requested_depth,
            resolve_outcome=args.resolve_outcome,
            resolve_message=args.resolve_message,
            project=project,
            head_sha=head_sha,
            base_ref=base_ref,
            tool_version=tool_version,
            action_version=action_version,
        )
        operational_error = True
    elif args.mode == "bootstrap":
        report = build_bootstrap_report(
            name=args.name,
            profile_id=args.profile_id,
            baseline_channel=args.baseline_channel,
            requested_depth=args.requested_depth,
            resolve_message=args.resolve_message,
            project=project,
            head_sha=head_sha,
            base_ref=base_ref,
            tool_version=tool_version,
            action_version=action_version,
        )
    else:
        if not args.report_in:
            print(
                "::error::--report-in is required for --mode augment", file=sys.stderr
            )
            return _EXIT_USAGE_ERROR
        report_in_path = Path(args.report_in)
        if not report_in_path.is_file():
            print(
                f"::error::--report-in {args.report_in!r} does not exist -- the "
                "analysis step did not produce a report file.",
                file=sys.stderr,
            )
            return _EXIT_USAGE_ERROR
        try:
            base_report = json.loads(report_in_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"::error::failed to read --report-in: {exc}", file=sys.stderr)
            return _EXIT_USAGE_ERROR
        if not isinstance(base_report, dict):
            print("::error::--report-in is not a JSON object", file=sys.stderr)
            return _EXIT_USAGE_ERROR
        report = augment_report(
            base_report,
            name=args.name,
            profile_id=args.profile_id,
            baseline_channel=args.baseline_channel,
            requested_depth=args.requested_depth,
            gate_mode=args.gate_mode,
            project=project,
            head_sha=head_sha,
            base_ref=base_ref,
            action_version=action_version,
        )
        # augment_report already classifies any non-compatibility verdict
        # (the literal "ERROR", or a scan guard sentinel like
        # "BUDGET_OVERFLOW"/"EVIDENCE_CONTRACT_ERROR") as operational by
        # populating operational_errors -- reuse that instead of
        # re-deriving it from verdict alone, which used to miss the scan
        # guard sentinels entirely and let gate-mode: deferred/advisory
        # silently swallow them (Codex review).
        operational_error = bool(report.get("operational_errors"))

    real_exit_code = 0
    if args.mode == "augment":
        severity = report.get("severity")
        if isinstance(severity, dict) and isinstance(severity.get("exit_code"), int):
            real_exit_code = severity["exit_code"]
        elif isinstance(report.get("exit_code"), int):
            real_exit_code = report["exit_code"]  # type: ignore[assignment]

    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    outputs = _outputs_for_report(report, args.report_out)
    output_status = _print_outputs(outputs)
    if output_status != 0:
        return output_status

    exit_code = final_exit_code(
        args.gate_mode,
        real_exit_code=real_exit_code,
        operational_error=operational_error,
    )
    print(f"exit-code={exit_code}")
    # This process's own exit status reports whether the envelope was
    # written successfully -- not check-target's gate decision. run.sh reads
    # the exit-code=N line above (already flushed to stdout) and performs
    # the actual `exit $N` itself, after this step (which must never be
    # continue-on-error'd, unlike the resolve/analysis steps) has completed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
