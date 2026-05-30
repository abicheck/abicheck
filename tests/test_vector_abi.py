"""Tests for VECTOR_ABI_CHANGED detection.

Vector-function (SIMD clone) ABI drift: when the vector-ABI compiler flag
selection changes between builds (-mveclibabi= GCC, -fveclib= clang,
-vecabi= Intel-style), the vectorized call variants of functions resolve to a
different ABI. This is a binary break for callers of the vector entry points.

Detection is keyed on the vector-ABI flags carried in DWARF DW_AT_producer, so
these tests exercise both the producer parser and the diff, plus the policy
partition, without needing a compiler.
"""
from __future__ import annotations

from abicheck.change_registry import REGISTRY, Verdict
from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.dwarf_advanced import (
    AdvancedDwarfMetadata,
    ToolchainInfo,
    _parse_producer,
)
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(vector_abi_flags: set[str]) -> AbiSnapshot:
    """Minimal AbiSnapshot whose DWARF carries the given vector-ABI flags."""
    meta = AdvancedDwarfMetadata(
        has_dwarf=True,
        toolchain=ToolchainInfo(
            producer_string="synthetic",
            compiler="GCC",
            vector_abi_flags=set(vector_abi_flags),
        ),
    )
    return AbiSnapshot(
        library="lib.so",
        version="1.0",
        functions=[
            Function(name="foo", mangled="foo", return_type="void",
                     visibility=Visibility.PUBLIC),
        ],
        dwarf_advanced=meta,
    )


class TestParseProducerVectorAbi:
    """_parse_producer must extract vector-ABI flags across compiler families."""

    def test_gcc_mveclibabi(self) -> None:
        info = _parse_producer("GNU C++17 13.3.0 -mavx -mveclibabi=svml -g")
        assert "-mveclibabi=svml" in info.vector_abi_flags

    def test_clang_fveclib(self) -> None:
        info = _parse_producer("clang version 17.0.0 -fveclib=libmvec -O2")
        assert "-fveclib=libmvec" in info.vector_abi_flags

    def test_intel_vecabi(self) -> None:
        info = _parse_producer("Intel(R) oneAPI DPC++ -vecabi=cmdtarget -O3")
        assert "-vecabi=cmdtarget" in info.vector_abi_flags

    def test_no_vector_flag_means_empty(self) -> None:
        info = _parse_producer("GNU C++17 13.3.0 -O2 -g -fPIC")
        assert info.vector_abi_flags == set()

    def test_vector_flag_not_confused_with_abi_flags(self) -> None:
        """Vector-ABI flags go to their own bucket, not abi_flags."""
        info = _parse_producer("GNU C 13.3.0 -fshort-enums -mveclibabi=acml")
        assert "-mveclibabi=acml" in info.vector_abi_flags
        assert "-mveclibabi=acml" not in info.abi_flags
        assert "-fshort-enums" in info.abi_flags


class TestVectorAbiChanged:
    """VECTOR_ABI_CHANGED must fire when the vector-ABI flag set drifts."""

    def test_flag_added_is_detected(self) -> None:
        old = _snap(set())
        new = _snap({"-mveclibabi=svml"})
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.VECTOR_ABI_CHANGED in kinds

    def test_flag_removed_is_detected(self) -> None:
        old = _snap({"-mveclibabi=svml"})
        new = _snap(set())
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.VECTOR_ABI_CHANGED in kinds

    def test_flag_value_changed_is_detected(self) -> None:
        old = _snap({"-vecabi=legacy"})
        new = _snap({"-vecabi=cmdtarget"})
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.VECTOR_ABI_CHANGED in kinds

    def test_identical_flags_no_event(self) -> None:
        old = _snap({"-mveclibabi=svml"})
        new = _snap({"-mveclibabi=svml"})
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.VECTOR_ABI_CHANGED not in kinds

    def test_no_flags_either_side_no_event(self) -> None:
        old = _snap(set())
        new = _snap(set())
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.VECTOR_ABI_CHANGED not in kinds

    def test_old_new_values_populated(self) -> None:
        old = _snap({"-vecabi=legacy"})
        new = _snap({"-vecabi=cmdtarget"})
        change = next(c for c in compare(old, new).changes
                      if c.kind == ChangeKind.VECTOR_ABI_CHANGED)
        assert change.old_value == "-vecabi=legacy"
        assert change.new_value == "-vecabi=cmdtarget"

    def test_verdict_is_breaking(self) -> None:
        old = _snap(set())
        new = _snap({"-mveclibabi=svml"})
        assert compare(old, new).verdict.value == "BREAKING"


class TestVectorAbiPartition:
    """The new kind must be registered as BREAKING with a plugin_abi override."""

    def test_in_breaking_kinds(self) -> None:
        assert ChangeKind.VECTOR_ABI_CHANGED in BREAKING_KINDS

    def test_registry_verdict_is_breaking(self) -> None:
        meta = REGISTRY.get("vector_abi_changed")
        assert meta is not None
        assert meta.default_verdict is Verdict.BREAKING

    def test_plugin_abi_downgrades_to_compatible(self) -> None:
        meta = REGISTRY.get("vector_abi_changed")
        assert meta is not None
        assert meta.policy_overrides.get("plugin_abi") is Verdict.COMPATIBLE


class TestVectorAbiBinaryOnly:
    """The kind is producer-derived, so it must be treated as binary-only in
    source-level reports (like toolchain_flag_drift)."""

    def test_in_compat_binary_only_kinds(self) -> None:
        from abicheck.compat.cli import _BINARY_ONLY_KINDS
        assert ChangeKind.VECTOR_ABI_CHANGED in _BINARY_ONLY_KINDS

    def test_in_report_binary_only_kinds(self) -> None:
        from abicheck.report_classifications import BINARY_ONLY_KINDS
        assert "vector_abi_changed" in BINARY_ONLY_KINDS


class TestVectorAbiSerializationRoundTrip:
    """vector_abi_flags must survive a snapshot JSON round-trip so that
    comparisons run from saved snapshots still report the change."""

    def test_round_trip_preserves_vector_abi_flags(self) -> None:
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
        snap = _snap({"-mveclibabi=svml"})
        restored = snapshot_from_dict(snapshot_to_dict(snap))
        assert restored.dwarf_advanced.toolchain.vector_abi_flags == {"-mveclibabi=svml"}

    def test_round_trip_still_detects_change(self) -> None:
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
        old = snapshot_from_dict(snapshot_to_dict(_snap(set())))
        new = snapshot_from_dict(snapshot_to_dict(_snap({"-vecabi=cmdtarget"})))
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.VECTOR_ABI_CHANGED in kinds
