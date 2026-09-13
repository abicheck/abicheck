"""Windows toolchain smoke tests (abicc #9 #50 #56 #121).

Pure unit tests that document and lock expected PE/Windows behavior in the
core diff engine without requiring a real compiler toolchain.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.dll", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


class TestPeSymbolDiff:
    def test_pe_symbol_diff_detects_removal(self) -> None:
        old = _snap(functions=[_func("foo", "foo")])
        new = _snap(functions=[])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert kinds == {ChangeKind.FUNC_REMOVED}

    def test_pe_symbol_diff_detects_addition(self) -> None:
        old = _snap(functions=[])
        new = _snap(functions=[_func("foo", "foo")])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert kinds == {ChangeKind.FUNC_ADDED}

    def test_pe_symbol_unchanged_no_diff(self) -> None:
        old = _snap(functions=[_func("foo", "foo")])
        new = _snap(functions=[_func("foo", "foo")])

        result = compare(old, new)
        assert not result.changes


class TestCallingConvMangling:
    def test_calling_conv_name_mangling_treated_as_removal_and_addition(self) -> None:
        """Document current behavior for #50.

        Calling convention drift appears as mangled symbol churn:
        old `_foo@4` (stdcall-like) vs new `foo` (cdecl-like).
        """
        old = _snap(functions=[_func("foo", "_foo@4")])
        new = _snap(functions=[_func("foo", "foo")])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert kinds == {ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_ADDED}

    def test_same_mangled_name_no_change(self) -> None:
        old = _snap(functions=[_func("foo", "_foo@4")])
        new = _snap(functions=[_func("foo", "_foo@4")])

        result = compare(old, new)
        assert not result.changes

    def test_calling_conv_change_is_symmetric(self) -> None:
        """Reverse cdecl->stdcall drift should produce the same churn pattern."""
        old = _snap(functions=[_func("foo", "foo")])
        new = _snap(functions=[_func("foo", "_foo@4")])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert kinds == {ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_ADDED}
