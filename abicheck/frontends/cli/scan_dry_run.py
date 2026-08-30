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

"""Single-binary ``scan --dry-run`` report builder.

Split out of :mod:`abicheck.cli_scan` (CLI cleanup phase two, PR 5 follow-up)
purely for line budget: that module is ``no_growth``-debt-tracked at its
adoption baseline, and adding the ``--abi3`` dry-run precondition check
(:mod:`abicheck.workflows.scan_abi3_dry_run`) had no room left to land
in-place. Lives under :mod:`abicheck.frontends.cli`, alongside its
``--artifact-set`` sibling :mod:`abicheck.frontends.cli.artifact_set_dry_run`
(the same split, for the same reason, one PR earlier).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...buildsource.scan_levels import EvidenceDepth, SourceMethod


def _dry_run_exit_code_lines(
    scheme_label: str, sev_config: Any, against: Path | None
) -> list[str]:
    """The dry-run's exit-code preview, for the scheme this run actually resolved.

    Split out of :func:`render_scan_dry_run` so the two schemes' wording sits
    side by side rather than inside an already-long renderer.

    *scheme_label* is the caller's already-resolved label (from
    ``cli_compare_receipt.dry_run_scheme_label``, the same one ``compare
    --dry-run`` prints); the severity branch keys off *sev_config* being
    present, which is exactly when the caller resolved that scheme.

    That branch is reachable only with ``--against``: every severity flag is a
    comparison-only flag, and without a baseline there is no comparison to
    gate, so an audit-only run always previews the legacy codes.
    """
    tail = "5 budget overflow, 6 not_comparable"
    lines = [
        "dry-run exit codes: 0 valid, 1 requested depth not satisfiable, "
        "64 usage error",
        f"exit-code scheme: {scheme_label}",
    ]
    if sev_config is not None and against is not None:
        levels = ", ".join(
            f"{attr}={getattr(getattr(sev_config, attr, None), 'value', '?')}"
            for attr in (
                "abi_breaking",
                "potential_breaking",
                "quality_issues",
                "addition",
            )
        )
        lines.append(f"resolved severity: {levels}")
        lines.append(
            "a real scan run's exit codes are 0 no error-level findings, "
            "1 error-level addition/quality findings (or incomplete contract "
            "coverage under --contract), 2 error-level "
            "potential_breaking, 4 error-level abi_breaking, "
            f"{tail} -- a category set to warning/info never gates, so a "
            "breaking comparison can exit 0"
        )
        return lines
    lines.append(
        "a real scan run's exit codes are 0 compatible, "
        "1 incomplete contract coverage (--contract only), "
        f"2 API break, 4 ABI break, {tail}"
    )
    return lines


def render_scan_dry_run(
    *,
    artifact: Path,
    against: Path | None,
    sources: Path | None,
    effective_build_info: Path | None,
    changed: list[str],
    changed_src: str,
    seeded: bool,
    depth: str | None,
    eff_depth_enum: EvidenceDepth,
    resolved: SourceMethod,
    collect_mode: str,
    header_backend: str,
    fmt: str,
    build_targets: tuple[str, ...] = (),
    scheme_label: str = "legacy (0/2/4)",
    sev_config: Any = None,
    abi3_floor: tuple[int, int] | None = None,
    estimates: list[Any] | None = None,
    estimate_error: str | None = None,
) -> Any:
    """Build the ``scan --dry-run`` report (ADR-043 D4): resolve, never scan.

    *estimates* is the caller's already-computed
    :func:`~abicheck.service_scan.estimate_scan` result (the same read-only
    per-layer cost/TU-count projection ``--estimate`` used to provide), and
    *estimate_error* is set instead when computing it raised. Taken as
    already-computed data rather than resolved here so this module needs no
    import of ``service_scan`` itself: it already sits on the large,
    already-accepted CLI-registration import cycle (``cli -> cli_scan ->
    frontends.cli.scan_dry_run -> ...``), and importing it directly from here
    would grow that cycle's membership (AI-readiness ``import-cycle-growth``,
    fresh evidence) -- the identical reason
    :mod:`abicheck.frontends.cli.artifact_set_dry_run` takes its own
    ``totals``/``notes`` as already-computed data instead of calling
    ``estimate_artifact_set`` itself.

    *scheme_label*/*sev_config* describe this invocation's **already-resolved**
    gate (the caller resolves them before emitting), so the preview states the
    contract the real run would actually use. Stating the legacy codes
    unconditionally was wrong once `scan --against` gained a severity gate --
    `--severity-preset info-only` previewed "0 compatible / 4 ABI break" for a
    run that exits 0 on a breaking comparison (Codex review). Same defect
    `compare --dry-run` already had and fixed, which is why the scheme label
    comes from its :func:`~abicheck.cli_compare_receipt.dry_run_scheme_label`
    rather than a second spelling of the same idea. *abi3_floor* is validated
    via :func:`~abicheck.workflows.scan_abi3_dry_run.apply_abi3_dry_run_check`.
    """
    from ...dry_run import DryRunResult, tool_status
    from ...workflows.scan_abi3_dry_run import apply_abi3_dry_run_check

    result = DryRunResult(command="scan")
    result.add(
        "Inputs",
        f"artifact: {artifact}",
        f"against: {against}" if against else "against: (none -- one-build audit only)",
    )
    scope_label = "changed" if seeded else "target"
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(auto)'}",
        f"effective collect mode: {collect_mode}",
        f"source scope: {scope_label}" if resolved.value == "s5" else None,
        f"changed paths ({changed_src}): {len(changed)}",
    )
    result.add("Headers and compile context", f"ast-frontend: {header_backend}")
    result.add(
        "Build/source inputs",
        f"--sources: {sources}" if sources else None,
        f"--build-info: {effective_build_info}" if effective_build_info else None,
        f"--build-target: {', '.join(build_targets)}" if build_targets else None,
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Consumer/contract scoping",
        "audit checks: always run (pattern pre-scan + intra-version cross-source)",
        "compatibility comparison: will run against --against"
        if against is not None
        else "compatibility comparison: will NOT run (no --against)",
    )
    apply_abi3_dry_run_check(result, artifact, abi3_floor)
    result.add(
        "Output and exit-code behavior",
        f"format: {fmt}",
        *_dry_run_exit_code_lines(scheme_label, sev_config, against),
    )
    if estimate_error is not None:
        result.warn(f"could not project per-layer cost: {estimate_error}")
    elif estimates is not None:
        total = sum(e.est_seconds for e in estimates)
        result.add(
            "Resolved depth and source scope",
            *(
                # A query-only build.query (service_scan._estimate_total_tus)
                # marks its note "[UNKNOWN" rather than folding a confident
                # `0` into the count -- render that honestly too, since
                # "0 TU(s), ~0.00s" reads as a real (near-zero) cost rather
                # than "not counted yet" (Codex review).
                f"{e.layer}: TU count/cost unknown -- {e.note}"
                if "[UNKNOWN" in e.note
                else f"{e.layer}: {e.tus} TU(s), ~{e.est_seconds:.2f}s -- {e.note}"
                for e in estimates
            ),
            f"projected total: {total:.2f}s",
            # Codex review: estimate_scan's TU count is a workspace-wide probe
            # (compile-DB/source-tree file count) -- it does not scope to
            # build_targets the way the real scan's Bazel collection would, so
            # a --build-target run's actual TU count is typically LOWER than
            # this preview states. Flagged rather than silently misleading.
            "note: --build-target given -- the TU counts above are an "
            "UNSCOPED workspace-wide estimate; the real run's Bazel "
            "collection will scope to the requested root target(s) and "
            "typically touch fewer TUs than shown"
            if build_targets
            else None,
            "note: at least one layer's TU count/cost is unknown (see above) "
            "-- it contributes 0.0s to the projected total, understating it"
            if any("[UNKNOWN" in e.note for e in estimates)
            else None,
        )
    return result
