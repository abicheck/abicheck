"""Unit tests for ADR-024 public-ABI surface resolution and scope filtering.

These exercise the *mechanism* directly on synthetic snapshots, so they need
no castxml/gcc and run in the fast suite. They are the no-false-positive
guarantees for backward-compatible changes to non-public API/ABI.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.surface import (
    PublicSurface,
    _type_identifiers,
    change_in_public_surface,
    compute_public_surface,
)


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, mangled=None):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
    )


def _rec(name, fields=(), bases=(), size=64):
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
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

        unscoped = compare(old, new)
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

    def test_scoping_off_by_default(self):
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
        assert default.out_of_surface_count == 0
        assert default.scope_to_public_surface is False

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
        result = runner.invoke(main, ["compare", str(op), str(np_)])
        # Without scoping, the internal change is a reported finding.
        assert "InternalCache" in result.stdout
