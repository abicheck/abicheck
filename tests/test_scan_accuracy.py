"""Scan accuracy tests — property-based and mutation-based tests for FP/FN prevention.

These tests systematically verify that:
1. Identical snapshots always produce NO_CHANGE (false positive resistance).
2. Known single mutations always produce the expected ChangeKind (false negative resistance).
3. Cross-detector deduplication doesn't lose real changes.
4. Confidence/evidence tiers are computed correctly.
"""
from __future__ import annotations

import copy

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import Confidence
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _base_snap(version: str = "1.0") -> AbiSnapshot:
    """Create a realistic baseline snapshot with functions, types, enums, vars."""
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=[
            Function(
                name="process", mangled="_Z7processv",
                return_type="int", params=[Param(name="data", type="void *", kind=ParamKind.POINTER)],
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="init", mangled="_Z4initv",
                return_type="void", params=[],
                visibility=Visibility.PUBLIC,
            ),
        ],
        variables=[
            Variable(name="version", mangled="_Z7versionv", type="const char *",
                     visibility=Visibility.PUBLIC),
        ],
        types=[
            RecordType(
                name="Config", kind="struct",
                fields=[
                    TypeField(name="width", type="int", offset_bits=0),
                    TypeField(name="height", type="int", offset_bits=32),
                ],
                size_bits=64,
            ),
        ],
        enums=[
            EnumType(
                name="Color",
                members=[
                    EnumMember(name="RED", value=0),
                    EnumMember(name="GREEN", value=1),
                    EnumMember(name="BLUE", value=2),
                ],
            ),
        ],
        typedefs={"ColorType": "enum Color"},
    )


class TestIdenticalSnapshotsNoChange:
    """Identical snapshots must always produce NO_CHANGE — false positive resistance."""

    def test_identical_snapshots_no_change(self):
        snap = _base_snap()
        result = compare(snap, copy.deepcopy(snap))
        assert result.verdict == Verdict.NO_CHANGE
        assert len(result.changes) == 0

    def test_identical_empty_snapshots_no_change(self):
        snap = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[], variables=[], types=[], enums=[],
            typedefs={},
        )
        result = compare(snap, copy.deepcopy(snap))
        assert result.verdict == Verdict.NO_CHANGE

    def test_version_change_only_no_abi_change(self):
        """Different version strings but identical API — should be NO_CHANGE."""
        old = _base_snap("1.0")
        new = _base_snap("2.0")
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE

    def test_source_location_change_not_reported(self):
        """Moving a function to a different line is not an ABI break."""
        old = _base_snap()
        new = copy.deepcopy(old)
        old.functions[0].source_location = "foo.h:10"
        new.functions[0].source_location = "foo.h:42"
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE


class TestSingleMutationDetection:
    """Single known mutations must be detected — false negative resistance."""

    def test_func_removed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.functions = [f for f in new.functions if f.name != "process"]
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_REMOVED for c in result.changes)

    def test_func_added_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.functions.append(Function(
            name="cleanup", mangled="_Z7cleanupv",
            return_type="void", params=[], visibility=Visibility.PUBLIC,
        ))
        result = compare(old, new)
        assert result.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.FUNC_ADDED for c in result.changes)

    def test_return_type_changed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.functions[0].return_type = "long"
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_RETURN_CHANGED for c in result.changes)

    def test_param_type_changed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.functions[0].params[0].type = "int *"
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.FUNC_PARAMS_CHANGED for c in result.changes)

    def test_var_removed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.variables = []
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.VAR_REMOVED for c in result.changes)

    def test_var_type_changed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.variables[0].type = "int"
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.VAR_TYPE_CHANGED for c in result.changes)

    def test_type_size_changed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.types[0].size_bits = 128
        # Raw detector check: Config is not wired to a public function here, so
        # disable surface scoping (orthogonal concern, default-on since ADR-024).
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_SIZE_CHANGED for c in result.changes)

    def test_type_field_removed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.types[0].fields = [new.types[0].fields[0]]  # keep only 'width'
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPE_FIELD_REMOVED for c in result.changes)

    def test_enum_member_removed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.enums[0].members = [m for m in new.enums[0].members if m.name != "BLUE"]
        # Raw detector check: Color is not wired to a public function here, so
        # disable surface scoping (orthogonal concern, default-on since ADR-024).
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.ENUM_MEMBER_REMOVED for c in result.changes)

    def test_enum_member_value_changed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.enums[0].members[1] = EnumMember(name="GREEN", value=42)
        # Raw detector check: Color is not wired to a public function here, so
        # disable surface scoping (orthogonal concern, default-on since ADR-024).
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.ENUM_MEMBER_VALUE_CHANGED for c in result.changes)

    def test_enum_member_added_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.enums[0].members.append(EnumMember(name="YELLOW", value=3))
        # Raw detector check: Color is not wired to a public function here, so
        # disable surface scoping (orthogonal concern, default-on since ADR-024).
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.COMPATIBLE
        assert any(c.kind == ChangeKind.ENUM_MEMBER_ADDED for c in result.changes)

    def test_typedef_removed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.typedefs = {}
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPEDEF_REMOVED for c in result.changes)

    def test_typedef_base_changed_detected(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        new.typedefs["ColorType"] = "int"
        result = compare(old, new, scope_to_public_surface=False)
        assert result.verdict == Verdict.BREAKING
        assert any(c.kind == ChangeKind.TYPEDEF_BASE_CHANGED for c in result.changes)


class TestHiddenSymbolsFalsePositiveResistance:
    """Hidden/internal symbols must not produce false positives."""

    def test_hidden_func_change_not_reported(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        # Add a hidden function to old, remove it from new
        old.functions.append(Function(
            name="internal", mangled="_Z8internalv",
            return_type="void", params=[], visibility=Visibility.HIDDEN,
        ))
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE

    def test_hidden_var_change_not_reported(self):
        old = _base_snap()
        new = copy.deepcopy(old)
        old.variables.append(Variable(
            name="secret", mangled="_Z6secretv", type="int",
            visibility=Visibility.HIDDEN,
        ))
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE


class TestCrossDetectorDeduplication:
    """Cross-detector dedup must not drop unique changes."""

    def test_dedup_preserves_different_symbols(self):
        """Two FUNC_REMOVED for different symbols must both be kept."""
        old = _base_snap()
        new = AbiSnapshot(
            library="libtest.so.1", version="1.0",
            functions=[], variables=old.variables,
            types=old.types, enums=old.enums,
            typedefs=old.typedefs,
        )
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 2  # both 'process' and 'init' should be reported


class TestConfidenceAndEvidenceTiers:
    """Confidence levels and evidence tier tracking."""

    def test_header_only_medium_confidence(self):
        """Snapshot with header data but no ELF/DWARF → medium."""
        snap = _base_snap()
        result = compare(snap, copy.deepcopy(snap))
        # Has header data (functions, types, enums) but no binary metadata
        assert result.confidence in (Confidence.MEDIUM, Confidence.LOW)
        assert "header" in result.evidence_tiers

    def test_empty_snapshot_low_confidence(self):
        snap = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[], variables=[], types=[], enums=[],
            typedefs={},
        )
        result = compare(snap, snap)
        assert result.confidence == Confidence.LOW


class TestGroundTruthExpectedKinds:
    """Validate that ground_truth.json expected_kinds field is parseable."""

    def test_ground_truth_expected_kinds_are_valid(self):
        import json
        from pathlib import Path

        gt_path = Path(__file__).parent.parent / "examples" / "ground_truth.json"
        if not gt_path.exists():
            pytest.skip("ground_truth.json not found")

        gt = json.loads(gt_path.read_text())
        valid_kinds = {ck.value for ck in ChangeKind}

        for case_name, case_data in gt["verdicts"].items():
            expected_kinds = case_data.get("expected_kinds")
            if expected_kinds is not None:
                for kind_val in expected_kinds:
                    assert kind_val in valid_kinds, (
                        f"{case_name}: expected_kind '{kind_val}' is not a valid ChangeKind"
                    )

            absent_kinds = case_data.get("expected_absent_kinds")
            if absent_kinds is not None:
                for kind_val in absent_kinds:
                    assert kind_val in valid_kinds, (
                        f"{case_name}: expected_absent_kind '{kind_val}' is not a valid ChangeKind"
                    )
