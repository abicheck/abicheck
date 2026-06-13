"""Unit tests for ADR-024 public-ABI surface resolution and scope filtering.

These exercise the *mechanism* directly on synthetic snapshots, so they need
no castxml/gcc and run in the fast suite. They are the no-false-positive
guarantees for backward-compatible changes to non-public API/ABI.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.surface import (
    REASON_NON_PUBLIC_TYPE,
    REASON_NOT_EXPORTED,
    REASON_PRIVATE_HEADER,
    REASON_SYSTEM_HEADER,
    PublicSurface,
    _type_identifiers,
    change_in_public_surface,
    classify_change_surface,
    compute_public_surface,
)


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, mangled=None,
        origin=ScopeOrigin.UNKNOWN):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


def _rec(name, fields=(), bases=(), size=64, origin=ScopeOrigin.UNKNOWN):
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
        origin=origin,
    )


# ── _type_identifiers ───────────────────────────────────────────────────────


class TestTypeIdentifiers:
    def test_strips_pointer_and_const(self):
        assert _type_identifiers("const Foo *") == {"Foo"}

    def test_template_args_extracted(self):
        assert _type_identifiers("Wrapper<Inner>") == {"Wrapper", "Inner"}

    def test_qualified_name_yields_both_forms(self):
        assert _type_identifiers("ns::detail::Impl") == {"ns::detail::Impl", "Impl"}

    def test_builtins_dropped(self):
        assert _type_identifiers("unsigned long") == set()

    def test_none_and_empty(self):
        assert _type_identifiers(None) == set()
        assert _type_identifiers("") == set()


# ── compute_public_surface ──────────────────────────────────────────────────


class TestComputePublicSurface:
    def test_unresolvable_without_public_symbols(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            elf_only_mode=True,
            functions=[_fn("internal", vis=Visibility.ELF_ONLY)],
        )
        surf = compute_public_surface(snap)
        assert surf.resolvable is False

    def test_public_symbol_and_reachable_type(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api_call", ret="Result *", params=("Config *",))],
            types=[_rec("Result"), _rec("Config"), _rec("InternalCache")],
        )
        surf = compute_public_surface(snap)
        assert surf.resolvable is True
        assert "api_call" in surf.public_symbols
        # Types referenced by the public function are public.
        assert "Result" in surf.public_types
        assert "Config" in surf.public_types
        # A type touched by nobody public is not.
        assert "InternalCache" not in surf.public_types
        assert "InternalCache" in surf.all_types

    def test_reachability_is_transitive_through_fields_and_bases(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("get", ret="Widget *")],
            types=[
                _rec("Widget", fields=[("impl", "Pixels")], bases=["Drawable"]),
                _rec("Pixels"),
                _rec("Drawable"),
                _rec("Unrelated"),
            ],
        )
        surf = compute_public_surface(snap)
        assert {"Widget", "Pixels", "Drawable"} <= surf.public_types
        assert "Unrelated" not in surf.public_types

    def test_private_symbol_not_public(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("public_api"),
                _fn("internal_helper", vis=Visibility.ELF_ONLY),
            ],
        )
        surf = compute_public_surface(snap)
        assert "public_api" in surf.public_symbols
        assert "internal_helper" not in surf.public_symbols
        assert "internal_helper" in surf.all_symbols


# ── change_in_public_surface ────────────────────────────────────────────────


class TestChangeClassification:
    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_public_symbol_change_is_in_surface(self):
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="api", description="")
        assert change_in_public_surface(c, s, s) is True

    def test_private_symbol_change_is_out_of_surface(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="internal", description=""
        )
        assert change_in_public_surface(c, s, s) is False

    def test_private_qualified_symbol_tail_match_is_out_of_surface(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="ns::internal",
            description="",
        )
        assert change_in_public_surface(c, s, s) is False

    def test_private_header_qualified_symbol_tail_reports_origin_reason(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("api"),
                _fn(
                    "internal",
                    vis=Visibility.PUBLIC,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="ns::internal",
            description="",
        )
        assert classify_change_surface(c, s, s) == (False, REASON_PRIVATE_HEADER)

    def test_unknown_symbol_change_is_kept_conservatively(self):
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="missing_symbol",
            description="",
        )
        assert change_in_public_surface(c, s, s) is True

    def test_private_type_change_is_out_of_surface(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="InternalCache", description=""
        )
        assert change_in_public_surface(c, s, s) is False

    def test_public_type_change_is_in_surface(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Result", description="")
        assert change_in_public_surface(c, s, s) is True

    def test_public_type_change_with_private_symbol_name_collision_is_in_surface(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("api", ret="Foo *"),
                _fn("Foo", vis=Visibility.HIDDEN, mangled="_ZL3Foov"),
            ],
            types=[_rec("Foo")],
        )
        s = self._surf(snap)
        assert "Foo" in s.public_types
        assert "Foo" in s.all_symbols
        assert "Foo" not in s.public_symbols

        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Foo", description="")

        assert change_in_public_surface(c, s, s) is True

    def test_value_abi_trait_change_uses_public_function_symbol_before_type_names(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("api")],
        )
        s = self._surf(snap)
        assert "api" in s.public_symbols
        assert "api" in s.all_types
        assert "api" not in s.public_types

        c = Change(
            kind=ChangeKind.VALUE_ABI_TRAIT_CHANGED,
            symbol="api",
            description="",
        )

        assert classify_change_surface(c, s, s) == (True, None)

    def test_leak_kind_never_filtered(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
            symbol="InternalCache",
            description="",
        )
        assert change_in_public_surface(c, s, s) is True

    def test_internal_namespace_type_never_filtered(self):
        # detail::/impl:: types are deferred to the internal-leak detector,
        # which uses broader reachability than this closure — so scoping must
        # never drop them, even when not reachable here (anti-hiding, D5.2).
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="oneapi::dal::detail::pimpl",
            description="",
        )
        assert change_in_public_surface(c, s, s) is True

    def test_unknown_type_kept_conservatively(self):
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="SomethingWeNeverSaw",
            description="",
        )
        assert change_in_public_surface(c, s, s) is True

    def test_unresolvable_keeps_everything(self):
        empty = PublicSurface()  # resolvable defaults to False
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Whatever", description="")
        assert change_in_public_surface(c, empty, empty) is True


# ── classify_change_surface: ledger reason codes (ADR-024 §D5.1) ─────────────


class TestSurfaceExclusionReason:
    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_in_surface_has_no_reason(self):
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="api", description="")
        assert classify_change_surface(c, s, s) == (True, None)

    def test_one_sided_unresolvable_keeps_everything(self):
        # Both sides must be resolvable before scoping demotes anything; an
        # unresolved side means we keep the finding (anti-hiding).
        resolvable = self._surf(
            AbiSnapshot(
                library="l", version="1",
                functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
            )
        )
        unresolvable = PublicSurface()  # resolvable defaults to False
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="internal", description="")
        assert classify_change_surface(c, resolvable, unresolvable) == (True, None)
        assert classify_change_surface(c, unresolvable, resolvable) == (True, None)

    def test_not_exported_symbol_reason(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="internal", description="")
        assert classify_change_surface(c, s, s) == (False, REASON_NOT_EXPORTED)

    def test_struct_return_convention_symbol_scoped(self):
        # struct_return_convention_changed carries the (mangled) function name in
        # Change.symbol, like calling_convention_changed — a non-exported helper's
        # return-convention churn must be demoted, not kept as an unknown type.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED, symbol="internal", description=""
        )
        assert classify_change_surface(c, s, s) == (False, REASON_NOT_EXPORTED)
        c_pub = Change(
            kind=ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED, symbol="api", description=""
        )
        assert classify_change_surface(c_pub, s, s) == (True, None)

    def test_non_public_type_reason(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="InternalCache", description=""
        )
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)

    @pytest.mark.parametrize("sym", ["api", "internal"])
    def test_change_in_public_surface_matches_classifier(self, sym):
        # The boolean wrapper must agree with the tuple classifier.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol=sym, description="")
        assert change_in_public_surface(c, s, s) == classify_change_surface(c, s, s)[0]


# ── end-to-end via compare(scope_to_public_surface=...) ──────────────────────


class TestScopedCompareNoFalsePositives:
    """Backward-compatible changes to *non-public* API/ABI yield no findings."""

    def test_internal_struct_layout_change_filtered(self):
        # A public API returns Result; InternalCache is internal (touched by
        # nobody public). Growing InternalCache is a layout change that must
        # NOT be reported when scoping to the public surface.
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=128)],
        )

        unscoped = compare(old, new, scope_to_public_surface=False)
        scoped = compare(old, new, scope_to_public_surface=True)

        # Without scoping the internal layout change shows up...
        assert any("InternalCache" in c.symbol for c in unscoped.changes)
        # ...with scoping it is moved to the audit ledger, not reported.
        assert not any("InternalCache" in c.symbol for c in scoped.changes)
        assert scoped.out_of_surface_count >= 1
        assert any("InternalCache" in c.symbol for c in scoped.out_of_surface_changes)
        assert scoped.verdict == Verdict.NO_CHANGE

    def test_public_struct_layout_change_still_reported(self):
        # The SAME kind of change to a PUBLIC type must still fire — scoping
        # must never hide a real break (ADR-024 anti-hiding).
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=128)],
        )
        scoped = compare(old, new, scope_to_public_surface=True)
        assert any("Result" in c.symbol for c in scoped.changes)
        assert scoped.verdict in (Verdict.BREAKING, Verdict.API_BREAK)

    def test_public_struct_layout_change_with_private_symbol_collision_still_reported(
        self,
    ):
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[
                _fn("get_foo", ret="Foo *"),
                _fn("Foo", vis=Visibility.HIDDEN, mangled="_ZL3Foov"),
            ],
            types=[_rec("Foo", size=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[
                _fn("get_foo", ret="Foo *"),
                _fn("Foo", vis=Visibility.HIDDEN, mangled="_ZL3Foov"),
            ],
            types=[_rec("Foo", size=128)],
        )

        scoped = compare(old, new, scope_to_public_surface=True)

        assert any(
            c.kind == ChangeKind.TYPE_SIZE_CHANGED and c.symbol == "Foo"
            for c in scoped.changes
        )
        assert not any(
            c.kind == ChangeKind.TYPE_SIZE_CHANGED and c.symbol == "Foo"
            for c in scoped.out_of_surface_changes
        )
        assert scoped.verdict in (Verdict.BREAKING, Verdict.API_BREAK)

    def test_public_value_abi_trait_change_with_non_public_type_collision_still_reported(
        self,
    ):
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("api")],
        )
        old.dwarf_advanced = AdvancedDwarfMetadata(
            has_dwarf=True,
            value_abi_traits={"api": "p0:trivial"},
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("api")],
        )
        new.dwarf_advanced = AdvancedDwarfMetadata(
            has_dwarf=True,
            value_abi_traits={"api": "p0:nontrivial"},
        )

        scoped = compare(old, new, scope_to_public_surface=True)

        assert any(
            c.kind == ChangeKind.VALUE_ABI_TRAIT_CHANGED and c.symbol == "api"
            for c in scoped.changes
        )
        assert not any(
            c.kind == ChangeKind.VALUE_ABI_TRAIT_CHANGED and c.symbol == "api"
            for c in scoped.out_of_surface_changes
        )
        assert scoped.verdict in (Verdict.BREAKING, Verdict.API_BREAK)

    def test_scoping_on_by_default(self):
        # ADR-024 Phase 5: header-scoping is the default. An internal-only
        # layout change is demoted to the ledger out of the box; passing
        # scope_to_public_surface=False restores the unscoped report.
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=128)],
        )
        default = compare(old, new)
        assert default.scope_to_public_surface is True
        assert default.out_of_surface_count >= 1
        assert not any("InternalCache" in c.symbol for c in default.changes)

        unscoped = compare(old, new, scope_to_public_surface=False)
        assert unscoped.out_of_surface_count == 0
        assert any("InternalCache" in c.symbol for c in unscoped.changes)

    def test_internal_leak_still_detected_under_scoping(self):
        # End-to-end anti-hiding guarantee: a detail:: type that leaks via a
        # public-header class (reached by compute_leak_paths' broader root set,
        # NOT by this closure) must still produce a leak finding under scoping.
        # Without the internal-namespace exemption the TYPE_SIZE_CHANGED would
        # be filtered before DetectInternalLeaks runs, hiding the leak.
        def _mk(impl_size):
            return AbiSnapshot(
                library="lib",
                version="x",
                # A public symbol so the surface is resolvable, but it does NOT
                # reference Widget — so Widget/detail::Impl are unreachable here.
                functions=[_fn("public_unrelated")],
                types=[
                    _rec("Widget", bases=["ns::detail::Impl"]),
                    _rec("ns::detail::Impl", size=impl_size),
                ],
            )

        old, new = _mk(64), _mk(128)
        scoped = compare(old, new, scope_to_public_surface=True)
        kinds = {c.kind for c in scoped.changes}
        assert (
            ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API in kinds
        ), f"leak hidden by scoping; got kinds={[k.value for k in kinds]}"
        # The internal type's change must not have been silently filtered.
        assert not any(
            "detail::Impl" in c.symbol for c in scoped.out_of_surface_changes
        )

    def test_adding_internal_symbol_is_compatible(self):
        old = AbiSnapshot(library="lib", version="1", functions=[_fn("public_api")])
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api"), _fn("new_internal", vis=Visibility.ELF_ONLY)],
        )
        scoped = compare(old, new, scope_to_public_surface=True)
        assert scoped.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


class TestScopeCli:
    """End-to-end CLI wiring for --scope-public-headers / --show-filtered."""

    def _write(self, path, snap):
        from abicheck.serialization import snapshot_to_json

        path.write_text(snapshot_to_json(snap))

    def _make_pair(self, tmp_path):
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=128)],
        )
        op, np_ = tmp_path / "old.json", tmp_path / "new.json"
        self._write(op, old)
        self._write(np_, new)
        return op, np_

    def test_cli_scope_filters_and_shows_ledger(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        op, np_ = self._make_pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["compare", str(op), str(np_), "--scope-public-headers", "--show-filtered"],
        )
        assert result.exit_code == 0, result.output
        # The internal layout change is in the audit ledger on stderr...
        assert "Filtered as non-public ABI surface" in result.stderr
        assert "InternalCache" in result.stderr
        # ...and absent from the reported findings on stdout.
        assert "InternalCache" not in result.stdout

    def test_cli_without_scope_reports_internal_change(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        op, np_ = self._make_pair(tmp_path)
        runner = CliRunner()
        # Scoping is on by default now, so --no-scope-public-headers is needed
        # to surface the internal-struct change.
        result = runner.invoke(
            main, ["compare", str(op), str(np_), "--no-scope-public-headers"]
        )
        # The internal struct's size change is breaking, so compare exits
        # non-zero (2/4) — assert that so a crash (exit 1, no real output)
        # can't masquerade as a pass.
        assert result.exit_code in (2, 4), result.output
        # Without scoping, the internal change is a reported finding.
        assert "InternalCache" in result.stdout


# ── machine-readable surface ledger (ADR-024 §D4/D5 disclosure) ──────────────


class TestSurfaceLedgerOutput:
    """The out-of-surface audit ledger is disclosed in JSON and SARIF.

    ADR-024 rejects libabigail's hard ``--headers-dir`` drop precisely so the
    "why was this excluded" trail stays auditable — that trail must reach the
    machine-readable formats, not just stderr text.
    """

    def _scoped_result(self):
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=128)],
        )
        return compare(old, new, scope_to_public_surface=True)

    def test_json_includes_surface_scope_ledger(self):
        import json

        from abicheck.reporter import to_json

        d = json.loads(to_json(self._scoped_result()))
        assert "surface_scope" in d
        ledger = d["surface_scope"]
        assert ledger["enabled"] is True
        assert ledger["out_of_surface_count"] >= 1
        entries = ledger["out_of_surface_changes"]
        symbols = {c["symbol"] for c in entries}
        assert any("InternalCache" in s for s in symbols)
        # ADR-024 §D5.1: each demoted finding carries a reason code.
        internal = next(c for c in entries if "InternalCache" in c["symbol"])
        assert internal["reason"] == REASON_NON_PUBLIC_TYPE

    def test_leaf_json_includes_surface_scope_ledger(self):
        # The leaf report mode takes an early-return path in to_json(); the
        # ledger must be present there too, not just in the full report.
        import json

        from abicheck.reporter import to_json

        d = json.loads(to_json(self._scoped_result(), report_mode="leaf"))
        assert "surface_scope" in d
        symbols = {c["symbol"] for c in d["surface_scope"]["out_of_surface_changes"]}
        assert any("InternalCache" in s for s in symbols)

    def test_sarif_includes_surface_scope_ledger(self):
        from abicheck.sarif import to_sarif

        props = to_sarif(self._scoped_result())["runs"][0]["properties"]
        assert "surfaceScope" in props
        ledger = props["surfaceScope"]
        assert ledger["enabled"] is True
        assert ledger["outOfSurfaceCount"] >= 1
        entries = ledger["outOfSurfaceChanges"]
        symbols = {c["symbol"] for c in entries}
        assert any("InternalCache" in s for s in symbols)
        internal = next(c for c in entries if "InternalCache" in c["symbol"])
        assert internal["reason"] == REASON_NON_PUBLIC_TYPE

    def test_ledger_absent_when_scoping_off(self):
        import json

        from abicheck.reporter import to_json
        from abicheck.sarif import to_sarif

        old = AbiSnapshot(library="lib", version="1", functions=[_fn("public_api")])
        new = AbiSnapshot(library="lib", version="2", functions=[_fn("public_api")])
        res = compare(old, new, scope_to_public_surface=False)
        assert "surface_scope" not in json.loads(to_json(res))
        assert "surfaceScope" not in to_sarif(res)["runs"][0]["properties"]


# ── classify_change_surface: provenance reason codes (ADR-015 v6 / ADR-024 D1) ─


class TestProvenanceReasons:
    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_private_header_symbol_demoted_even_when_exported(self):
        # A symbol the binary exports (PUBLIC linkage) but that originates in a
        # private header is demoted with the provenance reason — the leaked
        # private-header case scoping targets.
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[
                _fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER),
                _fn("leaked", origin=ScopeOrigin.PRIVATE_HEADER),
            ],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="leaked", description="")
        in_surf, reason = classify_change_surface(c, s, s)
        assert in_surf is False
        assert reason == REASON_PRIVATE_HEADER

    def test_system_header_symbol_demoted(self):
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[
                _fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER),
                _fn("from_libc", origin=ScopeOrigin.SYSTEM_HEADER),
            ],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="from_libc", description="")
        assert classify_change_surface(c, s, s) == (False, REASON_SYSTEM_HEADER)

    def test_public_header_origin_kept_in_surface(self):
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="public_api", description="")
        assert classify_change_surface(c, s, s) == (True, None)

    def test_unknown_origin_falls_back_to_linkage_reason(self):
        # No public set was used → origin UNKNOWN → provenance never fires;
        # the linkage reason (not-exported) is emitted as before.
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[
                _fn("public_api"),
                _fn("hidden", vis=Visibility.ELF_ONLY),
            ],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="hidden", description="")
        assert classify_change_surface(c, s, s) == (False, REASON_NOT_EXPORTED)

    def test_private_header_type_finding_demoted(self):
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("api", ret="Result *")],
            types=[
                _rec("Result", origin=ScopeOrigin.PUBLIC_HEADER),
                _rec("InternalCache", origin=ScopeOrigin.PRIVATE_HEADER),
            ],
        )
        s = self._surf(snap)
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="InternalCache", description="")
        assert classify_change_surface(c, s, s) == (False, REASON_PRIVATE_HEADER)

    def test_disagreeing_sides_block_demotion(self):
        # Public-header origin on one side blocks demotion (conservative).
        old = AbiSnapshot(
            library="l", version="1",
            functions=[
                _fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER),
                _fn("sym", origin=ScopeOrigin.PRIVATE_HEADER),
            ],
        )
        new = AbiSnapshot(
            library="l", version="2",
            functions=[
                _fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER),
                _fn("sym", origin=ScopeOrigin.PUBLIC_HEADER),
            ],
        )
        s_old, s_new = compute_public_surface(old), compute_public_surface(new)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="sym", description="")
        # sym is in public_symbols on both sides; the public-header side blocks
        # the private-header demotion, so it stays in surface.
        assert classify_change_surface(c, s_old, s_new) == (True, None)


# ── widening overlay (ADR-024 §D6 / Phase 4) ─────────────────────────────────


class TestWideningOverlay:
    """--public-symbol / force_public_symbols promote a symbol into the
    public surface even when header provenance/export would demote it."""

    def _run(self, changes, old, new, force_public):
        from abicheck.post_processing import FilterNonPublicSurface, PipelineContext

        ctx = PipelineContext(
            old=old, new=new, scope_to_public_surface=True,
            force_public_symbols=set(force_public),
        )
        kept = FilterNonPublicSurface().run(list(changes), ctx)
        return kept, ctx.out_of_surface

    def _pair(self):
        old = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("public_api"), _fn("stub_sym", vis=Visibility.ELF_ONLY)],
        )
        new = AbiSnapshot(
            library="l", version="2",
            functions=[_fn("public_api"), _fn("stub_sym", vis=Visibility.ELF_ONLY)],
        )
        return old, new

    def test_forced_symbol_kept_in_surface(self):
        old, new = self._pair()
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="stub_sym", description="")
        # Without widening: demoted (not-exported / non-public).
        kept_off, ledger_off = self._run([c], old, new, force_public=set())
        assert kept_off == [] and len(ledger_off) == 1
        # With widening: kept, not on the ledger.
        kept_on, ledger_on = self._run([c], old, new, force_public={"stub_sym"})
        assert kept_on == [c] and ledger_on == []

    def test_forced_symbol_matches_qualified_tail(self):
        old, new = self._pair()
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="ns::stub_sym", description="")
        kept, ledger = self._run([c], old, new, force_public={"stub_sym"})
        assert kept == [c] and ledger == []

    def test_widening_does_not_affect_unlisted_symbols(self):
        old, new = self._pair()
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="stub_sym", description="")
        kept, ledger = self._run([c], old, new, force_public={"other"})
        assert kept == [] and len(ledger) == 1


def test_collect_force_public_symbols_merges_flag_and_file(tmp_path):
    from abicheck.cli import _collect_force_public_symbols

    lst = tmp_path / "syms.txt"
    lst.write_text("# public symbols\nfoo\n\n  bar  \n# comment\nbaz\n")
    out = _collect_force_public_symbols(("qux", "foo"), lst)
    assert out == {"foo", "bar", "baz", "qux"}


def test_collect_force_public_symbols_no_file():
    from abicheck.cli import _collect_force_public_symbols

    assert _collect_force_public_symbols((), None) == set()
    assert _collect_force_public_symbols(("a", " ", "b"), None) == {"a", "b"}


class TestWideningCLI:
    """End-to-end: --public-symbol re-promotes a demoted finding via the CLI."""

    def _write(self, path, snap):
        from abicheck.serialization import snapshot_to_json

        path.write_text(snapshot_to_json(snap))

    def _pair(self, tmp_path):
        # InternalCache is an internal struct (no public API references it);
        # its layout change is demoted under scoping. Widening by its name
        # re-promotes it into the reported surface.
        old = AbiSnapshot(
            library="lib", version="1",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=64)],
        )
        new = AbiSnapshot(
            library="lib", version="2",
            functions=[_fn("public_api", ret="Result *")],
            types=[_rec("Result", size=64), _rec("InternalCache", size=128)],
        )
        op, np_ = tmp_path / "old.json", tmp_path / "new.json"
        self._write(op, old)
        self._write(np_, new)
        return op, np_

    def test_public_symbol_flag_repromotes_finding(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        op, np_ = self._pair(tmp_path)
        runner = CliRunner()
        # Scoped without widening: the internal change is filtered out of stdout.
        scoped = runner.invoke(
            main, ["compare", str(op), str(np_), "--scope-public-headers"]
        )
        assert "InternalCache" not in scoped.stdout
        # Scoped + widened: the change is back in the report.
        widened = runner.invoke(
            main,
            ["compare", str(op), str(np_), "--scope-public-headers",
             "--public-symbol", "InternalCache"],
        )
        assert "InternalCache" in widened.stdout

    def test_public_symbols_list_file(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        op, np_ = self._pair(tmp_path)
        syms = tmp_path / "public.syms"
        syms.write_text("# guaranteed exports\nInternalCache\n")
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["compare", str(op), str(np_), "--scope-public-headers",
             "--public-symbols-list", str(syms)],
        )
        assert "InternalCache" in result.stdout
