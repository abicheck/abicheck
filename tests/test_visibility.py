"""B7: INTERNAL/HIDDEN visibility (abi-dumper #16).

Symbols with STV_HIDDEN or STV_INTERNAL ELF visibility are implementation
details and must NOT be reported as ABI changes when they change.

Only PUBLIC and ELF_ONLY visibility functions are part of the public ABI
surface. HIDDEN functions are filtered from the diff completely.

Detection mechanism:
- checker._diff_functions() filters: only processes PUBLIC and ELF_ONLY
- Visibility.HIDDEN is in the model
- Hidden functions changing between snapshots should produce zero changes
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import AbiSnapshot, Function, Visibility


def _func(
    name: str, mangled: str, vis: Visibility = Visibility.PUBLIC, **kwargs: object
) -> Function:
    defaults: dict[str, object] = dict(return_type="void")
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, visibility=vis, **defaults)  # type: ignore[arg-type]


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


class TestHiddenVisibilityModel:
    """Verify Visibility.HIDDEN exists in the model."""

    def test_visibility_hidden_exists(self) -> None:
        """Visibility enum must have HIDDEN member (STV_HIDDEN ELF visibility)."""
        assert hasattr(Visibility, "HIDDEN")
        assert Visibility.HIDDEN.value == "hidden"

    def test_function_can_have_hidden_visibility(self) -> None:
        """Function can be created with Visibility.HIDDEN."""
        f = _func("internal_impl", "_Zinternal", vis=Visibility.HIDDEN)
        assert f.visibility == Visibility.HIDDEN

    def test_hidden_visibility_roundtrip(self) -> None:
        """Visibility.HIDDEN survives serialization roundtrip."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _snap(functions=[_func("hidden_fn", "_Zhidden", vis=Visibility.HIDDEN)])
        d = snapshot_to_dict(snap)
        assert d["functions"][0]["visibility"] == "hidden"
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].visibility == Visibility.HIDDEN


class TestHiddenFunctionNotReported:
    """Hidden functions must NOT produce ABI change reports (abi-dumper #16)."""

    def test_hidden_function_removed_no_change(self) -> None:
        """Removing a HIDDEN function → no ABI change emitted."""
        old = _snap(
            functions=[
                _func("public_api", "_Zpub"),
                _func("hidden_impl", "_Zhidden", vis=Visibility.HIDDEN),
            ]
        )
        new = _snap(
            functions=[
                _func("public_api", "_Zpub"),
                # hidden_impl removed — no ABI impact
            ]
        )
        result = compare(old, new)
        # No FUNC_REMOVED for hidden function
        func_removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        hidden_removed = [c for c in func_removed if "hidden" in c.symbol.lower()]
        assert not hidden_removed, (
            "HIDDEN function removal must not be reported as ABI break"
        )

    def test_hidden_function_added_no_change(self) -> None:
        """Adding a HIDDEN function → no ABI change emitted."""
        old = _snap(functions=[_func("pub", "_Zpub")])
        new = _snap(
            functions=[
                _func("pub", "_Zpub"),
                _func("new_hidden", "_Znhidden", vis=Visibility.HIDDEN),
            ]
        )
        result = compare(old, new)
        func_added = [c for c in result.changes if c.kind == ChangeKind.FUNC_ADDED]
        hidden_added = [c for c in func_added if "hidden" in c.symbol.lower()]
        assert not hidden_added, (
            "HIDDEN function addition must not be reported as ABI change"
        )

    def test_hidden_function_signature_change_no_abi_change(self) -> None:
        """Changing a HIDDEN function's signature → no ABI change."""
        old = _snap(
            functions=[
                _func("pub", "_Zpub"),
                _func("hidden_fn", "_Zhfn", vis=Visibility.HIDDEN, return_type="void"),
            ]
        )
        new = _snap(
            functions=[
                _func("pub", "_Zpub"),
                _func("hidden_fn", "_Zhfn", vis=Visibility.HIDDEN, return_type="int"),
            ]
        )
        result = compare(old, new)
        # hidden_fn changed return type — but it's HIDDEN, so no ABI change
        hidden_changes = [
            c
            for c in result.changes
            if "hfn" in c.symbol or "hidden" in c.symbol.lower()
        ]
        assert not hidden_changes

    def test_public_function_change_still_detected(self) -> None:
        """PUBLIC function change is still detected alongside hidden functions."""
        old = _snap(
            functions=[
                _func("pub_api", "_Zpub", return_type="void"),
                _func("hidden_fn", "_Zhfn", vis=Visibility.HIDDEN),
            ]
        )
        new = _snap(
            functions=[
                _func("pub_api", "_Zpub", return_type="int"),  # return type changed
                _func("hidden_fn", "_Zhfn", vis=Visibility.HIDDEN),
            ]
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_RETURN_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_visibility_public_to_hidden_is_abi_break(self) -> None:
        """PUBLIC → HIDDEN: symbol is no longer exported → ABI break (FUNC_VISIBILITY_CHANGED)."""
        old = _snap(functions=[_func("api", "_Zapi", vis=Visibility.PUBLIC)])
        new = _snap(functions=[_func("api", "_Zapi", vis=Visibility.HIDDEN)])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        # This is breaking: callers can no longer resolve the symbol
        assert ChangeKind.FUNC_VISIBILITY_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_hidden_only_snapshot_no_changes(self) -> None:
        """Two snapshots with only hidden functions → no ABI changes."""
        old = _snap(
            functions=[
                _func("h1", "_Zh1", vis=Visibility.HIDDEN),
                _func("h2", "_Zh2", vis=Visibility.HIDDEN),
            ]
        )
        new = _snap(
            functions=[
                _func("h1", "_Zh1", vis=Visibility.HIDDEN),
                _func(
                    "h2_renamed", "_Zh2", vis=Visibility.HIDDEN
                ),  # name change, same mangled
            ]
        )
        result = compare(old, new)
        # Hidden-only library: no public ABI surface
        func_breaks = [
            c
            for c in result.changes
            if c.kind
            in (
                ChangeKind.FUNC_REMOVED,
                ChangeKind.FUNC_RETURN_CHANGED,
                ChangeKind.FUNC_PARAMS_CHANGED,
            )
        ]
        assert not func_breaks
