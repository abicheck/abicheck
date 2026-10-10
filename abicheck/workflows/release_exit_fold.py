# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A directory/package release's compatibility-axis exit fold (ADR-064).

Each member's contribution is the scalar resolver's own
(:func:`abicheck.workflows.member_compare.stamp_member_exit_contributions`);
this module folds them across the release and adds the release-global
bundle/probe-matrix findings no member carries. The release's own axes
(removed library, scope completeness, not-comparable, operational error) and
their precedence stay with :mod:`abicheck.policy.release_exit_decision`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..policy.evaluate import effective_kind_sets

if TYPE_CHECKING:
    from ..bundle import BundleDiffResult
    from ..checker_types import DiffResult
    from ..policy.release_gate_options import GateOptions

__all__ = [
    "fold_release_global_severity",
    "release_compatibility_base_exit",
    "release_severity_exit_code",
]


def release_severity_exit_code(
    library_results: list[dict[str, object]],
    gate: GateOptions,
) -> int | None:
    """The severity-aware exit code aggregated across all libraries.

    ``None`` when no severity setting is in effect (callers keep the legacy
    verdict-based exit). Otherwise the worst member compatibility
    contribution, each one the scalar resolver's own
    (``resolve_compare_exit_decision``, stamped by the fan-out under
    ``_compatibility_contribution`` with this run's severity config), so a
    member's per-library ``--policy-file`` overrides and frozen-namespace
    floor count exactly as a single-pair ``compare`` of it counts them. Must
    run before private keys are stripped; release-global bundle/matrix
    findings are folded in separately via :func:`fold_release_global_severity`.
    """
    if gate.severity is None:
        return None
    return max(
        (
            code
            for entry in library_results
            if isinstance(entry, dict)
            and isinstance(code := entry.get("_compatibility_contribution"), int)
        ),
        default=0,
    )


def fold_release_global_severity(
    base_code: int,
    bundle_result: BundleDiffResult | None,
    matrix_result: DiffResult | None,
    gate: GateOptions,
) -> int:
    """Fold release-global (bundle + matrix) findings into the severity exit.

    The per-library aggregation in :func:`release_severity_exit_code`
    cannot see bundle-level findings or build-config matrix findings, which are
    computed later and update ``worst_verdict``. Without this, a release whose
    per-library diffs are clean but whose bundle/matrix analysis flags an
    error-level break would exit 0 under, e.g., the default preset. Returns the
    worst of *base_code* and the bundle/matrix severity codes. A no-op
    (returns *base_code* unchanged) when ``gate.severity is None``.
    """
    config = gate.severity
    if config is None:
        return base_code

    from ..policy.severity import compute_exit_code

    worst = base_code
    if bundle_result is not None and bundle_result.bundle_findings:
        # Bundle findings carry canonical (partitioned) ChangeKinds.
        # G38 stabilization Phase 10 (Codex review, fresh evidence): this
        # omitted `policy=` entirely, unlike the matrix_result branch right
        # below it -- so a policy that reclassifies a bundle kind (e.g.
        # `plugin_abi` demoting `calling_convention_changed`, which
        # `BundleDiffResult.bundle_verdict` already honors via its own
        # `.policy` field) never reached the severity-aware exit code,
        # letting the displayed verdict and the process exit disagree.
        # G38 Phase 16 (Codex review): `policy_file` had the identical gap.
        bundle_changes = [f.to_change() for f in bundle_result.bundle_findings]
        worst = max(
            worst,
            compute_exit_code(
                bundle_changes,
                config,
                policy=bundle_result.policy,
                policy_file=bundle_result.policy_file,
            ),
        )
    if matrix_result is not None and matrix_result.changes:
        worst = max(
            worst,
            compute_exit_code(
                matrix_result.changes,
                config,
                policy=matrix_result.policy,
                kind_sets=effective_kind_sets(matrix_result),
                policy_file=matrix_result.policy_file,
            ),
        )
    return worst


def release_compatibility_base_exit(
    worst_verdict: str, severity_exit_code: int | None
) -> int:
    """The compatibility axis's own exit code for a stderr notice's wording
    -- the severity-aware code when one is in effect, else the legacy
    verdict mapping with the release's own operational ``ERROR`` floor.
    ``not_comparable`` is ``16`` under either scheme, exactly as
    ``_exit_compare_release`` exits it ahead of every floor (CodeRabbit)."""
    if worst_verdict == "not_comparable":
        return 16
    if severity_exit_code is not None:
        return max(severity_exit_code, 4 if worst_verdict == "ERROR" else 0)
    from ..model.change_catalog.registry import Verdict
    from ..policy.severity import legacy_exit_code

    if worst_verdict in Verdict.__members__:
        return legacy_exit_code(Verdict[worst_verdict])
    return 4 if worst_verdict == "ERROR" else 0
