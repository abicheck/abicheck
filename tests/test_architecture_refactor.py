"""Tests for the architecture refactoring (Problems A, B, C).

Covers:
- A: ChangeKindRegistry — single-declaration metadata, derived sets
- B: DetectorRegistry — self-registering detectors
- C: PostProcessingPipeline — explicit step pipeline
"""

from __future__ import annotations

from abicheck.change_registry import (
    REGISTRY,
    ChangeKindMeta,
    ChangeKindRegistry,
    Verdict,
)
from abicheck.checker_policy import (
    ADDITION_KINDS,
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    IMPACT_TEXT,
    PLUGIN_ABI_DOWNGRADED_KINDS,
    QUALITY_KINDS,
    RISK_KINDS,
    SDK_VENDOR_COMPAT_KINDS,
    ChangeKind,
)

# ─── Part A: ChangeKindRegistry tests ────────────────────────────────────────


class TestChangeKindRegistry:
    """Single-declaration registry replaces scattered metadata."""

    def test_registry_has_all_changekind_members(self):
        """Every ChangeKind enum member has a registry entry."""
        for kind in ChangeKind:
            assert kind.value in REGISTRY, f"{kind.value} missing from registry"

    def test_registry_no_extra_entries(self):
        """Registry has no entries beyond ChangeKind enum members."""
        kind_values = {k.value for k in ChangeKind}
        for entry_key in REGISTRY.entries:
            assert entry_key in kind_values, f"Extra registry entry: {entry_key}"

    def test_breaking_kinds_derived_from_registry(self):
        """BREAKING_KINDS matches registry entries with BREAKING verdict."""
        registry_breaking = {
            ChangeKind(v) for v in REGISTRY.kinds_for_verdict(Verdict.BREAKING)
        }
        assert BREAKING_KINDS == registry_breaking

    def test_compatible_kinds_derived_from_registry(self):
        """COMPATIBLE_KINDS matches registry entries with COMPATIBLE verdict."""
        registry_compat = {
            ChangeKind(v) for v in REGISTRY.kinds_for_verdict(Verdict.COMPATIBLE)
        }
        assert COMPATIBLE_KINDS == registry_compat

    def test_api_break_kinds_derived_from_registry(self):
        """API_BREAK_KINDS matches registry entries with API_BREAK verdict."""
        registry_api = {
            ChangeKind(v) for v in REGISTRY.kinds_for_verdict(Verdict.API_BREAK)
        }
        assert API_BREAK_KINDS == registry_api

    def test_risk_kinds_derived_from_registry(self):
        """RISK_KINDS matches registry entries with COMPATIBLE_WITH_RISK verdict."""
        registry_risk = {
            ChangeKind(v)
            for v in REGISTRY.kinds_for_verdict(Verdict.COMPATIBLE_WITH_RISK)
        }
        assert RISK_KINDS == registry_risk

    def test_addition_kinds_derived_from_registry(self):
        """ADDITION_KINDS matches registry entries with is_addition=True."""
        registry_additions = {ChangeKind(v) for v in REGISTRY.addition_kinds()}
        assert ADDITION_KINDS == registry_additions

    def test_quality_kinds_is_compatible_minus_additions(self):
        """QUALITY_KINDS = COMPATIBLE_KINDS - ADDITION_KINDS."""
        assert QUALITY_KINDS == frozenset(COMPATIBLE_KINDS - ADDITION_KINDS)

    def test_impact_text_derived_from_registry(self):
        """IMPACT_TEXT dict matches registry impact fields."""
        registry_impact = {ChangeKind(k): v for k, v in REGISTRY.impact_text().items()}
        assert IMPACT_TEXT == registry_impact

    def test_sdk_vendor_overrides_from_registry(self):
        """SDK_VENDOR_COMPAT_KINDS matches registry policy_overrides."""
        registry_sdk = {
            ChangeKind(v) for v in REGISTRY.policy_overrides_for("sdk_vendor")
        }
        assert SDK_VENDOR_COMPAT_KINDS == registry_sdk

    def test_plugin_abi_overrides_from_registry(self):
        """PLUGIN_ABI_DOWNGRADED_KINDS matches registry policy_overrides."""
        registry_plugin = {
            ChangeKind(v) for v in REGISTRY.policy_overrides_for("plugin_abi")
        }
        assert PLUGIN_ABI_DOWNGRADED_KINDS == registry_plugin

    def test_duplicate_entry_raises(self):
        """Duplicate kind values in ChangeKindRegistry raise ValueError."""
        import pytest

        entries = [
            ChangeKindMeta("test_kind", Verdict.BREAKING),
            ChangeKindMeta("test_kind", Verdict.COMPATIBLE),
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            ChangeKindRegistry(entries)

    def test_adding_kind_is_one_entry(self):
        """Adding a new kind to the registry is a single ChangeKindMeta entry."""
        entry = ChangeKindMeta(
            kind="hypothetical_new_kind",
            default_verdict=Verdict.BREAKING,
            impact="This is what goes wrong.",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        # The entry contains ALL metadata in one place
        assert entry.default_verdict == Verdict.BREAKING
        assert entry.impact == "This is what goes wrong."
        assert entry.policy_overrides == {"plugin_abi": Verdict.COMPATIBLE}
        assert entry.is_addition is False


# ─── Part B: DetectorRegistry tests ──────────────────────────────────────────


def _get_populated_registry():
    """Import checker (which triggers all detector imports) and return registry."""
    import abicheck.checker  # noqa: F401 — triggers detector module imports
    from abicheck.detector_registry import registry

    return registry


class TestDetectorRegistry:
    """Self-registering detector registry."""

    def test_all_detectors_registered(self):
        """All 40 detectors are registered via decorators."""
        registry = _get_populated_registry()
        assert len(registry) == 40

    def test_detector_names_unique(self):
        """No duplicate detector names."""
        registry = _get_populated_registry()
        names = registry.detector_names
        assert len(names) == len(set(names))

    def test_expected_detectors_present(self):
        """Key detectors are in the registry."""
        registry = _get_populated_registry()
        names = set(registry.detector_names)
        expected = {
            "functions",
            "variables",
            "types",
            "enums",
            "elf",
            "pe",
            "macho",
            "dwarf",
            "advanced_dwarf",
            "enum_renames",
            "field_qualifiers",
            "field_renames",
            "param_defaults",
            "param_renames",
            "pointer_levels",
            "access_levels",
            "anon_fields",
            "var_values",
            "type_kind_changes",
            "reserved_fields",
            "const_overloads",
            "param_restrict",
            "param_va_list",
            "constants",
            "var_access",
            "elf_deleted_fallback",
            "template_inner_types",
            "symbol_renames",
            "method_qualifiers",
            "unions",
            "typedefs",
            "tls_checks",
            "protected_visibility",
            "symbol_version_alias",
            "glibcxx_dual_abi",
            "inline_namespace",
            "vtable_identity",
            "abi_surface",
            "sycl",
            "fingerprint_renames",
        }
        assert expected <= names

    def test_run_all_returns_changes_and_results(self):
        """registry.run_all() returns (changes, detector_results)."""
        registry = _get_populated_registry()
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        changes, results = registry.run_all(old, new)
        assert isinstance(changes, list)
        assert isinstance(results, list)
        # Results should have entries for all detectors (enabled or disabled)
        assert len(results) == 40

    def test_support_check_disables_detector(self):
        """Detectors with failing support checks are disabled."""
        registry = _get_populated_registry()
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        _, results = registry.run_all(old, new)
        result_map = {r.name: r for r in results}
        # PE detector should be disabled (no PE metadata)
        assert result_map["pe"].enabled is False
        assert result_map["pe"].coverage_gap == "missing PE metadata"
        # Macho detector should be disabled
        assert result_map["macho"].enabled is False
        # Advanced DWARF should be disabled
        assert result_map["advanced_dwarf"].enabled is False

    def test_duplicate_name_raises(self):
        """Registering a detector with duplicate name raises ValueError."""
        import pytest

        from abicheck.detector_registry import DetectorRegistry

        reg = DetectorRegistry()

        @reg.detector("test_det")
        def _det1(old, new):
            return []

        with pytest.raises(ValueError, match="Duplicate"):

            @reg.detector("test_det")
            def _det2(old, new):
                return []


# ─── Part C: PostProcessingPipeline tests ────────────────────────────────────


class TestPostProcessingPipeline:
    """Pipeline-based post-processing."""

    def test_default_pipeline_has_expected_steps(self):
        """DEFAULT_PIPELINE has all expected steps in order."""
        from abicheck.post_processing import DEFAULT_PIPELINE

        expected_names = [
            "filter_reserved_field_renames",
            "filter_opaque_size_changes",
            "downgrade_opaque_struct_changes",
            "deduplicate_ast_dwarf",
            "deduplicate_cross_detector",
            "downgrade_opaque_type_changes",
            "enrich_source_locations",
            "apply_suppression",
            "suppress_renamed_pairs",
            "filter_redundant",
            "enrich_affected_symbols",
            "detect_internal_leaks",
            "detect_onedal_patterns",
            "detect_namespace_patterns",
            "detect_template_patterns",
            "escalate_frozen_namespace_violations",
        ]
        assert DEFAULT_PIPELINE.step_names == expected_names

    def test_pipeline_runs_on_empty_changes(self):
        """Pipeline produces valid context with empty change list."""
        from abicheck.model import AbiSnapshot
        from abicheck.post_processing import DEFAULT_PIPELINE

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        ctx = DEFAULT_PIPELINE.run([], old, new)
        assert ctx.kept == []
        assert ctx.redundant == []
        assert ctx.suppressed == []

    def test_pipeline_with_changes(self):
        """Pipeline processes changes through all steps."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.post_processing import DEFAULT_PIPELINE

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
        ctx = DEFAULT_PIPELINE.run(changes, old, new)
        assert len(ctx.kept) == 1
        assert ctx.kept[0].kind == ChangeKind.FUNC_REMOVED

    def test_custom_pipeline_with_subset_of_steps(self):
        """Custom pipeline with only some steps."""
        from abicheck.model import AbiSnapshot
        from abicheck.post_processing import (
            DeduplicateAstDwarf,
            FilterReservedFieldRenames,
            PostProcessingPipeline,
        )

        pipeline = PostProcessingPipeline(
            [
                FilterReservedFieldRenames(),
                DeduplicateAstDwarf(),
            ]
        )
        assert pipeline.step_names == [
            "filter_reserved_field_renames",
            "deduplicate_ast_dwarf",
        ]
        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        ctx = pipeline.run([], old, new)
        assert ctx.kept == []


# ─── Integration: compare() uses registry + pipeline ─────────────────────────


class TestCompareUsesNewArchitecture:
    """compare() uses self-registering detectors and pipeline."""

    def test_compare_returns_valid_result(self):
        """compare() still returns correct DiffResult."""
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        result = compare(old, new)
        assert result.verdict.value == "NO_CHANGE"
        assert result.changes == []
        assert len(result.detector_results) == 40

    def test_compare_detects_func_removal(self):
        """compare() detects function removal via registry."""
        from abicheck.checker import compare
        from abicheck.checker_policy import ChangeKind
        from abicheck.model import AbiSnapshot, Function, Visibility

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
        new = AbiSnapshot(library="test", version="2.0")
        result = compare(old, new)
        assert result.verdict.value == "BREAKING"
        func_removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(func_removed) == 1
