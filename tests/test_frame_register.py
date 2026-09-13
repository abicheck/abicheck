"""Tests for #117 — FRAME_REGISTER_CHANGED detection.

Verifies that the CFA/frame-pointer convention drift detector:
- emits FRAME_REGISTER_CHANGED when rsp ↔ rbp changes for a function
- does NOT emit when registers are identical
- integrates correctly with compare()
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(frame_registers: dict[str, str]) -> AbiSnapshot:
    """Minimal AbiSnapshot with mocked frame_registers."""
    meta = AdvancedDwarfMetadata(has_dwarf=True, frame_registers=dict(frame_registers))
    snap = AbiSnapshot(
        library="lib.so",
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="foo",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        dwarf_advanced=meta,
    )
    return snap


class TestFrameRegisterChanged:
    """FRAME_REGISTER_CHANGED must fire when CFA register changes for a function."""

    def test_rbp_to_rsp_is_breaking(self) -> None:
        """rbp → rsp (-fomit-frame-pointer) must emit FRAME_REGISTER_CHANGED."""
        old = _snap({"foo": "rbp"})
        new = _snap({"foo": "rsp"})
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FRAME_REGISTER_CHANGED in kinds

    def test_rsp_to_rbp_is_breaking(self) -> None:
        """rsp → rbp must also emit FRAME_REGISTER_CHANGED (any convention change)."""
        old = _snap({"foo": "rsp"})
        new = _snap({"foo": "rbp"})
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FRAME_REGISTER_CHANGED in kinds

    def test_no_change_no_event(self) -> None:
        """Same register in both versions must not emit FRAME_REGISTER_CHANGED."""
        old = _snap({"foo": "rsp"})
        new = _snap({"foo": "rsp"})
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FRAME_REGISTER_CHANGED not in kinds

    def test_old_value_new_value_populated(self) -> None:
        """Change must include old_value and new_value for diagnostics."""
        old = _snap({"bar": "rbp"})
        new = _snap({"bar": "rsp"})
        result = compare(old, new)
        change = next(
            c for c in result.changes if c.kind == ChangeKind.FRAME_REGISTER_CHANGED
        )
        assert change.old_value == "rbp"
        assert change.new_value == "rsp"

    def test_symbol_name_in_change(self) -> None:
        """Change must include the affected function name."""
        old = _snap({"_ZN3Foo3barEv": "rbp"})
        new = _snap({"_ZN3Foo3barEv": "rsp"})
        result = compare(old, new)
        change = next(
            c for c in result.changes if c.kind == ChangeKind.FRAME_REGISTER_CHANGED
        )
        assert "_ZN3Foo3barEv" in change.symbol

    def test_added_function_not_reported(self) -> None:
        """New function in new version must not trigger FRAME_REGISTER_CHANGED."""
        old = _snap({})
        new = _snap({"new_fn": "rsp"})
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FRAME_REGISTER_CHANGED not in kinds

    def test_removed_function_not_reported(self) -> None:
        """Removed function must not trigger FRAME_REGISTER_CHANGED (handled by ELF diff)."""
        old = _snap({"old_fn": "rbp"})
        new = _snap({})
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FRAME_REGISTER_CHANGED not in kinds

    def test_multiple_functions_only_changed_reported(self) -> None:
        """Only functions with register change must be in results."""
        old = _snap({"fn_a": "rbp", "fn_b": "rsp", "fn_c": "rbp"})
        new = _snap({"fn_a": "rsp", "fn_b": "rsp", "fn_c": "rbp"})
        result = compare(old, new)
        frame_changes = [
            c for c in result.changes if c.kind == ChangeKind.FRAME_REGISTER_CHANGED
        ]
        changed_syms = {c.symbol for c in frame_changes}
        assert "fn_a" in changed_syms
        assert "fn_b" not in changed_syms  # no change
        assert "fn_c" not in changed_syms  # no change

    def test_verdict_is_breaking(self) -> None:
        """FRAME_REGISTER_CHANGED must result in BREAKING verdict."""
        old = _snap({"_Z6methodv": "rbp"})
        new = _snap({"_Z6methodv": "rsp"})
        result = compare(old, new)
        assert result.verdict.value == "BREAKING"
