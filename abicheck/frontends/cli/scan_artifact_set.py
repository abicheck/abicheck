# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``scan``'s multi-artifact (``--artifact-set``) fan-out.

ADR-061 D5, the third of three extractions that took ``cli_scan.py`` off the
2000-line hard cap (see ``cli_scan_inputs.py`` and ``cli_scan_emit.py``).

This is the whole path a set-shaped invocation takes that a single-artifact
scan never does: resolving the set's member paths, rejecting the
comparison-only flags a set cannot honour, and running the members. It sits
above both siblings -- it parses through ``cli_scan_inputs`` and renders
through ``cli_scan_emit`` -- which is why it is a separate module rather
than folded into either.

``cli_scan.py`` re-exports every name unchanged, so
``cli_scan._run_artifact_set`` (named in several tests' prose) still
resolves.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click

from ...cli_scan_helpers import (
    render_crosscheck_lines,
    render_pattern_lines,
    render_preprocessor_lines,
)

_COMPARISON_ONLY_FLAGS = {
    "suppress": "--suppress",
    "policy_file_path": "--policy",
    "policy": "--policy",
    "scope_public_headers": "--scope-public-headers/--no-scope-public-headers",
    "severity_preset": "--severity-preset",
    "exit_code_scheme": "--exit-code-scheme",
    "pattern_verdicts": "--pattern-verdicts/--no-pattern-verdicts",
    "env_matrix_path": "--env-matrix",
    "contract_mode": "--contract",
    # ADR-049 D8: a pack's only application here is the baseline comparison's
    # policy file, so without one it would configure nothing.
    "pack_paths": "--pack",
    # The findings/suppressed cap only applies to the --against summary
    # `_baseline_summary` builds -- without a baseline there is no such
    # summary to cap.
    "max_findings": "--max-findings",
    # P0.4: the exit-code floor `--require-complete-analysis` imposes is
    # folded alongside `--against`'s own verdict/coverage exit code
    # (`_run_baseline_compare`) -- without a baseline there is no such
    # exit code to floor. `analysis_assurance` is still always computed and
    # available in --format json for an audit-only scan; only the flag that
    # makes it *gate* requires a comparison.
    "require_complete_analysis": "--require-complete-analysis",
}


def _resolve_artifact_set_paths(spec: tuple[str, ...]) -> tuple[list[Path], bool]:
    """``--artifact-set`` values → ``(paths, explicit)`` (ADR-056).

    ``spec`` is the tuple Click's repeatable ``--artifact-set`` collects (CLI
    cleanup phase two, PR 5 -- the comma-separated single-string form this
    replaced is gone, no alias). A single value naming a directory expands
    to every discoverable shared library in it (``explicit=False`` -- an
    unsupported file found this way is silently skipped, mirroring
    ``build_bundle_snapshot``'s directory-scan behavior); anything else is
    an explicit path list, one member per occurrence, every member of which
    must resolve (``explicit=True``, per :func:`bundle.discover_artifact_set`).
    """
    from ...workflows.extraction import discover_shared_libraries

    if len(spec) == 1:
        candidate = Path(spec[0])
        if candidate.is_dir():
            return discover_shared_libraries(candidate), False
    paths: list[Path] = []
    for part in spec:
        p = Path(part)
        if not p.exists():
            raise click.UsageError(f"--artifact-set member not found: {part}")
        paths.append(p)
    return paths, True


def _reject_comparison_only_flags(*, no_baseline_reason: str) -> None:
    """Reject any flag from :data:`_COMPARISON_ONLY_FLAGS` given explicitly
    on the command line, for a scan that has no ``--against`` baseline to
    apply them to.

    Shared by both the ``--against``-less single-binary path and the
    ``--artifact-set`` path (always audit-only, ADR-056 D2 -- Codex review:
    the single-binary check alone left this validation reachable only via
    ``against is None``, which the ``--artifact-set`` branch's early
    ``return`` never passes through, so these flags were silently parsed,
    validated, and then discarded for a set instead of erroring).
    """
    ctx = click.get_current_context()
    explicit = [
        flag
        for dest, flag in _COMPARISON_ONLY_FLAGS.items()
        if ctx.get_parameter_source(dest) == click.core.ParameterSource.COMMANDLINE
    ]
    if explicit:
        noun = "flag" if len(explicit) == 1 else "flags"
        raise click.UsageError(
            f"{', '.join(explicit)} only take effect with --against (they "
            f"configure the baseline comparison); drop {'this' if len(explicit) == 1 else 'these'} "
            f"{noun} or {no_baseline_reason}."
        )


def _render_member_findings_lines(result: Any) -> list[str]:
    """Render one artifact-set member's cross-check/pattern/preprocessor
    findings for text output (P2, Codex review): the artifact-set text
    report previously showed only ``path: verdict`` per member -- unlike the
    single-binary ``scan``'s richly-rendered report and the aggregate JSON's
    nested ``report``, it gave no finding descriptions or evidence
    explaining *why* a member was flagged, leaving CLI/Action-summary users
    unable to act on the result.

    Reuses the same section renderers the single-binary path uses
    (:func:`render_crosscheck_lines`/:func:`render_pattern_lines`/
    :func:`render_preprocessor_lines`) via a minimal attribute shim, since
    those renderers only ever read the report's already-plain-dict
    ``crosscheck``/``pattern_scan``/``preprocessor_scan`` keys -- not any
    behavior specific to the full :class:`~abicheck.scan_engine.ScanOutcome`
    object ``ScanArtifactResult.result.report`` was flattened from.
    """
    report = result.report or {}
    if not report:
        return []
    shim = SimpleNamespace(
        crosscheck=report.get("crosscheck") or {},
        crosscheck_severities=report.get("crosscheck_severities") or {},
        pattern=report.get("pattern_scan") or {},
        preprocessor=report.get("preprocessor_scan") or {},
        audit=report.get("mode") == "audit",
    )
    lines = render_crosscheck_lines(shim)
    lines += render_pattern_lines(shim)
    lines += render_preprocessor_lines(shim)
    return [f"  {ln}" if ln else "" for ln in lines]


def _render_artifact_set_text(result: Any) -> str:
    """Human-facing render of a :class:`ScanSetResult` (ADR-056).

    Reuses :func:`bundle.render_bundle_findings_markdown` (G34 Phase 4) for
    the bundle-findings section, the same helper
    ``cli_compare_release_helpers._release_md_bundle_findings`` calls for
    the two-sided ``compare``/release path — one rendering for
    :class:`bundle.BundleFinding`, regardless of which side produced it.
    """
    from ...bundle import render_bundle_findings_markdown

    lines: list[str] = [
        f"Artifact-set scan verdict: {result.verdict} (exit {result.exit_code})",
        "",
        "Per-artifact results:",
    ]
    for member in result.per_artifact:
        lines.append(f"  {member.artifact}: {member.result.verdict}")
        lines.extend(_render_member_findings_lines(member.result))
    lines.append("")
    if result.bundle_incomplete:
        lines.append("Bundle analysis: incomplete (artifact-set discovery failed)")
    elif result.verdict == "BUDGET_OVERFLOW":
        # CodeRabbit review: run_scan_set() returns BUDGET_OVERFLOW before
        # ever calling audit_bundle() -- bundle_incomplete/bundle_verdict
        # stay at their ScanSetResult defaults (False/None), so without
        # this branch the report fell through to the else below and
        # printed the misleading "Bundle analysis: None (0 finding(s))"
        # instead of stating the bundle audit never ran.
        lines.append("Bundle analysis: not run (budget overflow)")
    else:
        lines.append(
            f"Bundle analysis: {result.bundle_verdict} "
            f"({len(result.bundle_findings)} finding(s))"
        )
        lines.extend(render_bundle_findings_markdown(result.bundle_findings))
    return "\n".join(lines)
