"""DispositionPipeline step-order and custom-pipeline tests.

Moved out of ``test_architecture_refactor.py``, which sits at its no-growth
ratchet; the default pipeline's step list grows with each ADR-084 step.
"""

from __future__ import annotations

import pytest

from abicheck.policy.finding_disposition import dispose_findings
from abicheck.policy.finding_disposition.default_pipeline import DEFAULT_PIPELINE


class TestDispositionPipeline:
    """Pipeline-based post-processing."""

    def test_default_pipeline_has_expected_steps(self):
        """DEFAULT_PIPELINE has all expected steps in order."""

        expected_names = [
            "filter_reserved_field_renames",
            "filter_opaque_size_changes",
            "downgrade_opaque_struct_changes",
            "deduplicate_ast_dwarf",
            "deduplicate_cross_detector",
            "downgrade_opaque_type_changes",
            "enrich_source_locations",
            "filter_non_public_surface",
            "demote_off_python_surface",
            "annotate_layout_unverifiable_covered_by_vtable_changed",
            "mark_reachability",
            "apply_suppression",
            *("suppress_renamed_pairs", "fold_export_hygiene_into_existence"),
            "clear_orphaned_vtable_gap_correlation",
            *("disambiguate_type_symbols", "filter_redundant"),
            "enrich_affected_symbols",
            "attribute_stdlib_embedding",
            "attribute_target_isa_consequences",
            "detect_internal_leaks",
            "demote_unreachable_internal_churn",
            "detect_cpp_patterns",
            "detect_namespace_patterns",
            "detect_template_patterns",
            "detect_versioned_symbol_scheme",
            "escalate_frozen_namespace_violations",
            "demote_unproven_binary_api_breaks",
        ]
        assert [s.name for s in DEFAULT_PIPELINE.steps] == expected_names

    def test_pipeline_runs_on_empty_changes(self):
        """Pipeline produces valid context with empty change list."""
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        ctx = dispose_findings([], old, new)
        assert ctx.kept == []
        assert ctx.redundant == []
        assert ctx.suppressed == []

    def test_pipeline_with_changes(self):
        """Pipeline processes changes through all steps."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.model.change import Change

        old = AbiSnapshot(
            library="test",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    params=[],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        new = AbiSnapshot(library="test", version="2.0", functions=[])
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="foo",
                description="function removed",
            ),
        ]
        ctx = dispose_findings(changes, old, new)
        assert len(ctx.kept) == 1
        assert ctx.kept[0].kind == ChangeKind.FUNC_REMOVED

    def test_kept_alias_violation_raises_runtime_error(self):
        """A step that rebinds ``changes`` to a new list after FilterRedundant,
        without resyncing ``ctx.kept``, must fail loudly.

        Regression guard for the ``ctx.kept`` aliasing contract: FilterRedundant
        sets ``ctx.kept = kept`` and later steps (DetectCppPatterns,
        DemoteUnreachableInternalChurn) rely on that being the *same list
        object* so their in-place suppression/demotion is visible through
        ``ctx.kept``. A step that instead rebinds ``changes`` to a fresh list
        (e.g. a list comprehension) without updating ``ctx.kept`` would
        silently discard whatever was tracked there — this must raise instead.
        """

        from abicheck.checker_policy import ChangeKind
        from abicheck.model import AbiSnapshot
        from abicheck.model.change import Change
        from abicheck.policy.finding_disposition.pipeline import DispositionPipeline
        from abicheck.post_processing import FilterRedundant

        class _ReboundStep:
            name = "rebinds_changes_bug"

            def run(self, changes, ctx):
                # Simulates the exact bug class the contract guards against:
                # a fresh list instead of an in-place mutation or ctx.kept resync.
                return [c for c in changes]

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="foo", description="added"),
        ]
        pipeline = DispositionPipeline([FilterRedundant(), _ReboundStep()])
        with pytest.raises(RuntimeError, match="aliasing contract"):
            pipeline.run(changes, old, new)

    def test_kept_alias_preserved_through_default_pipeline(self):
        """End-to-end regression: a finding demoted by
        DemoteUnreachableInternalChurn (which runs after FilterRedundant) must
        actually disappear from ``ctx.kept`` — not just from that step's own
        return value — proving the ``ctx.kept`` alias survives every
        intervening step (DetectInternalLeaks, EnrichAffectedSymbols, etc.)."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.model import AbiSnapshot
        from abicheck.model.change import Change

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::detail::Private",
                description="size changed",
            ),
        ]
        ctx = dispose_findings(changes, old, new)
        assert any(c.symbol == "ns::detail::Private" for c in ctx.out_of_surface)
        assert all(c.symbol != "ns::detail::Private" for c in ctx.kept)

    def test_custom_pipeline_with_subset_of_steps(self):
        """Custom pipeline with only some steps."""
        from abicheck.model import AbiSnapshot
        from abicheck.policy.finding_disposition.pipeline import DispositionPipeline
        from abicheck.post_processing import (
            DeduplicateAstDwarf,
            FilterReservedFieldRenames,
        )

        pipeline = DispositionPipeline(
            [
                FilterReservedFieldRenames(),
                DeduplicateAstDwarf(),
            ]
        )
        assert [s.name for s in pipeline.steps] == [
            "filter_reserved_field_renames",
            "deduplicate_ast_dwarf",
        ]
        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        ctx = pipeline.run([], old, new)
        assert ctx.kept == []
