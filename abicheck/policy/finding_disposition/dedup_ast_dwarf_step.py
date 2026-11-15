"""The AST/DWARF duplicate-collapse pipeline step (ADR-093 D5).

It moved here from ``post_processing.py`` so that the step records each finding
it collapses through the disposition ledger, with the declared subsumption as
its reason. The matching itself stays in :mod:`abicheck.compare.finding_dedup`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...model.change import Change
from ...model.change_catalog.kinds import ChangeKind
from ...model.change_catalog.subsumption import subsumptions_of
from ..disposition_types import Disposition

if TYPE_CHECKING:
    from .run_state import PipelineContext


class DeduplicateAstDwarf:
    """Collapse AST/DWARF duplicate findings."""

    name = "deduplicate_ast_dwarf"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        # An evolution step: no baseline, no OLD->NEW delta to read
        # (see PipelineContext.baseline_present for why not a self-compare).
        if ctx.old is None:
            return changes

        from ...compare.finding_dedup import deduplicate_ast_dwarf

        ledger = ctx.disposition_ledger

        def _record_subsumed(dropped: Change, kept_kind: ChangeKind) -> None:
            # The subsumption's declared key names the relation (ADR-093 D4).
            # The kept finding's identity is not recorded: that is the ADR-093
            # D5 gap tracked in the WS-5 plan row (PR 3).
            if ledger is None:
                return
            key = next(
                s.key.value
                for s in subsumptions_of(dropped.kind.value)
                if s.kind == kept_kind.value
            )
            ledger.record(
                dropped,
                Disposition.DEDUPLICATED,
                application_point=self.name,
                reason_code=f"subsumed_by:{kept_kind.value}:{key}",
            )

        return deduplicate_ast_dwarf(changes, ctx.old, ctx.new, _record_subsumed)
