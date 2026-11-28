# SPDX-License-Identifier: Apache-2.0
"""Qualify a PE data export reclassified by generation 99 on identical DLL bytes."""

from ..model.availability import FactStatus
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.pe_facts import PeSymbolType
from ..model.snapshot import AbiSnapshot
from ..model.surface_facts import EXPORT_STUB_TYPE
from .generation_drift_demotion import GENERATION_DRIFT_REASON, GENERATION_DRIFT_RULE


def qualify_pe_export_reclassification(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_generation: int,
    new_generation: int,
) -> None:
    if not (
        0
        < min(old_generation, new_generation)
        < 99
        <= max(old_generation, new_generation)
    ):
        return
    if not old.binary_sha256 or old.binary_sha256 != new.binary_sha256:
        return
    stale, fresh = (old, new) if old_generation < new_generation else (new, old)
    if stale.pe is None or fresh.pe is None:
        return
    names = {
        exp.name or f"ordinal:{exp.ordinal}"
        for exp in fresh.pe.exports
        if exp.sym_type == PeSymbolType.DATA
    }
    names.intersection_update(
        f.mangled
        for f in stale.declarations.functions
        if f.return_type == EXPORT_STUB_TYPE
        and f.declared_in_headers_fact is not None
        and f.declared_in_headers_fact.status is FactStatus.NOT_COLLECTED
    )
    names.intersection_update(
        v.mangled for v in fresh.declarations.variables if v.type == EXPORT_STUB_TYPE
    )
    kinds = {ChangeKind.CPO_KIND_CHANGED}
    if old_generation < new_generation:
        kinds.update((ChangeKind.FUNC_REMOVED, ChangeKind.VAR_ADDED))
    else:
        kinds.update((ChangeKind.FUNC_ADDED, ChangeKind.VAR_REMOVED))
    for change in changes:
        if (
            change.kind in kinds
            and change.symbol in names
            and change.effective_verdict is None
        ):
            change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
            change.modulation_rule = GENERATION_DRIFT_RULE
            change.modulation_reason = (
                f"{GENERATION_DRIFT_REASON}: generation 99 identifies a data export "
                "in the same PE binary that the earlier extractor recorded as a function"
            )
