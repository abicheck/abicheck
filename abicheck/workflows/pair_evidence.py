# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The evidence fold every compare route runs on a resolved snapshot pair.

Both comparison entry points -- the typed pipeline's
``service_compare_pipeline.classify_compare_pair`` (which the release
fan-out reaches through ``member_compare.compare_member``) and the native
``compare`` CLI -- resolve their operands and policy their own way, then
call :func:`fold_pair_evidence` and the Tier-2 ``compare_snapshots``
chokepoint. The fold happens here once, in one order:

1. build-info/source facts (ADR-028/033) diffed into ``extra_changes``;
2. the candidate-only ``--abi3`` audit folded in (ADR-068 D3) -- before
   classification, so policy, suppression, the disposition ledger and the
   verdict all score it.

Resolution (inputs, suppression strictness, packs, project overrides,
probe-matrix and L0 folds) and post-processing (receipts, assurance,
exit decision) stay with each caller for now: those still differ between
routes and are recorded in ``docs/contribute/known-gaps.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = ["FoldedEvidence", "fold_pair_evidence"]


@dataclass(frozen=True)
class FoldedEvidence:
    """:func:`fold_pair_evidence`'s result: the change set to classify, plus
    the facts callers attach or report after classification."""

    extra_changes: list[Change] | None
    layer_coverage_rows: list[dict[str, object]]
    evidence_metrics: dict[str, object]
    abi3_failure: str | None


def fold_pair_evidence(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    collect_mode: str,
    extra_changes: list[Change] | None,
    policy_file: Any,
    abi3_floor: tuple[int, int] | None,
    candidate_name: str | None = None,
    on_output: Callable[[str], None] | None = None,
) -> FoldedEvidence:
    """Diff embedded build/source facts into *extra_changes*, then fold the
    abi3 audit.

    *extra_changes* are findings the caller already produced (probe matrix,
    L0 hard removals). *on_output* receives the build-source diff's report
    lines. A ``SnapshotError`` from the build-source step propagates
    unchanged for the caller to translate.
    """
    from ..buildsource.evidence_report import prepare_embedded_build_source
    from . import abi3_audit

    extra_changes, layer_coverage_rows, evidence_metrics, _ev_changes = (
        prepare_embedded_build_source(
            old,
            new,
            collect_mode,
            extra_changes,
            None,
            None,
            None,
            None,
            policy_file=policy_file,
            on_output=on_output,
        )
    )
    extra_changes, abi3_failure = abi3_audit.fold(
        extra_changes, new, abi3_floor, candidate_name=candidate_name
    )
    return FoldedEvidence(
        extra_changes, layer_coverage_rows, evidence_metrics, abi3_failure
    )
