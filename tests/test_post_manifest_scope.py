"""Tests for POST-manifest surface scoping (`compare --post-manifest`).

The manifest's committed `pp_*`/ufunc-loop set is fed to compare() as an
explicit ``public_surface_allowlist``: an export finding whose symbol is not
committed (e.g. private ``__pp_*`` kernel churn) is demoted to the audit
ledger, while type-level and leak findings are always kept (conservative —
scoping must never hide a break).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import snapshot_to_json
from abicheck.surface import is_symbol_level_finding


def _cfn(name: str, ret: str = "void", params: tuple[str, ...] = ()) -> Function:
    # A C-ABI (POST) symbol: the exported symbol name == the function name.
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _snap(
    functions: list[Function], types: list[RecordType] | None = None
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libpost.so", version="1", functions=functions, types=types or []
    )


def test_private_kernel_churn_demoted_under_manifest_scope() -> None:
    # old exports the committed wrapper pp_foo and a private kernel __pp_foo_impl;
    # new drops the kernel (a real removal, breaking without scoping).
    old = _snap([_cfn("pp_foo"), _cfn("__pp_foo_impl")])
    new = _snap([_cfn("pp_foo")])

    # Baseline: the kernel removal is a breaking change.
    baseline = compare(old, new, scope_to_public_surface=False)
    assert baseline.verdict in (Verdict.BREAKING, Verdict.API_BREAK)

    # Manifest-scoped to the committed surface {pp_foo}: the kernel is not
    # committed, so its removal is demoted — verdict is clean.
    scoped = compare(old, new, public_surface_allowlist={"pp_foo"})
    assert scoped.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)
    assert any("__pp_foo_impl" in c.symbol for c in scoped.out_of_surface_changes)


def test_committed_symbol_break_still_caught_under_manifest_scope() -> None:
    # Removing a *committed* pp_* symbol must still break, even when scoped.
    old = _snap([_cfn("pp_foo"), _cfn("__pp_foo_impl")])
    new = _snap([_cfn("__pp_foo_impl")])  # pp_foo dropped

    scoped = compare(old, new, public_surface_allowlist={"pp_foo"})
    assert scoped.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    assert not any("pp_foo" == c.symbol for c in scoped.out_of_surface_changes)


def test_type_layout_break_kept_under_manifest_scope() -> None:
    # A struct passed to a committed export changing layout is a real break; the
    # type finding has no matching pp_* symbol, so scoping must keep it (never
    # silently drop a type-level change).
    old = _snap(
        [_cfn("pp_use", params=("Widget *",))],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="x", type="int")],
            )
        ],
    )
    new = _snap(
        [_cfn("pp_use", params=("Widget *",))],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=128,
                fields=[TypeField(name="x", type="long")],
            )
        ],
    )
    scoped = compare(old, new, public_surface_allowlist={"pp_use"})
    # The layout change is retained (kept), not demoted off the surface.
    assert scoped.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    assert not any("Widget" in (c.symbol or "") for c in scoped.out_of_surface_changes)


def test_manifest_allowlist_matches_exactly_not_by_suffix() -> None:
    # Codex: the manifest allowlist is a set of exact C export names. An
    # uncommitted namespaced helper that merely shares the leaf name
    # (`internal::pp_foo` vs committed `pp_foo`) must be demoted, not kept —
    # suffix tolerance belongs only to the user force_public overlay.
    from abicheck.post_processing import FilterNonPublicSurface, PipelineContext

    # Both symbols must be *real exports* in the snapshot to be subject to the
    # committed-surface filter at all.
    snap = _snap([_cfn("pp_foo"), _cfn("internal::pp_foo")])
    ctx = PipelineContext(old=snap, new=_snap([]), public_surface_allowlist={"pp_foo"})
    committed = Change(kind=ChangeKind.FUNC_REMOVED, symbol="pp_foo", description="")
    namespaced = Change(
        kind=ChangeKind.FUNC_REMOVED, symbol="internal::pp_foo", description=""
    )

    kept = FilterNonPublicSurface().run([committed, namespaced], ctx)
    assert committed in kept and namespaced not in kept
    assert namespaced in ctx.out_of_surface


def test_force_public_ignored_in_manifest_scope_without_header_scoping() -> None:
    # Codex: the CLI warns --public-symbol is ignored under
    # --no-scope-public-headers. The allowlist branch must honor that — a
    # force_public private symbol is NOT re-added when header scoping is off, but
    # IS when it is on.
    from abicheck.post_processing import FilterNonPublicSurface, PipelineContext

    snap = _snap([_cfn("pp_foo"), _cfn("__pp_impl")])
    finding = Change(kind=ChangeKind.FUNC_REMOVED, symbol="__pp_impl", description="")

    off = PipelineContext(
        old=snap,
        new=_snap([_cfn("pp_foo")]),
        public_surface_allowlist={"pp_foo"},
        force_public_symbols={"__pp_impl"},
    )
    assert finding not in FilterNonPublicSurface().run([finding], off)  # ignored

    finding2 = Change(kind=ChangeKind.FUNC_REMOVED, symbol="__pp_impl", description="")
    on = PipelineContext(
        old=snap,
        new=_snap([_cfn("pp_foo")]),
        public_surface_allowlist={"pp_foo"},
        force_public_symbols={"__pp_impl"},
        scope_to_public_surface=True,
    )
    assert finding2 in FilterNonPublicSurface().run([finding2], on)  # honored


def test_removed_contract_symbols_recovers_non_default_demotion() -> None:
    # Codex: a wrapper demoted from a default export (pp_old@@POST_1) to a
    # non-default-only alias (pp_old@POST_1) no longer satisfies unversioned
    # client links, so it must be recovered even though the name still appears in
    # the new snapshot's symbol table.
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
    from abicheck.post_manifest import removed_contract_symbols

    old = AbiSnapshot(
        library="l",
        version="1",
        elf=ElfMetadata(
            soname="l.so",
            symbols=[
                ElfSymbol(name="pp_old", sym_type=SymbolType.FUNC, is_default=True)
            ],
        ),
    )
    new = AbiSnapshot(
        library="l",
        version="2",
        elf=ElfMetadata(
            soname="l.so",
            symbols=[
                ElfSymbol(name="pp_old", sym_type=SymbolType.FUNC, is_default=False)
            ],
        ),
    )
    assert removed_contract_symbols(old, new) == {"pp_old"}


def test_removed_contract_symbols_recovers_dropped_callable_wrapper() -> None:
    # The Tier-2 recovery set the service path unions into a raw allowlist.
    from abicheck.post_manifest import removed_contract_symbols

    old = _snap([_cfn("pp_foo"), _cfn("pp_removed"), _cfn("__pp_impl")])
    new = _snap([_cfn("pp_foo")])
    removed = removed_contract_symbols(old, new)
    assert removed == {"pp_removed"}  # __pp_impl (private) and pp_foo (kept) excluded


def test_compare_snapshots_recovers_removed_wrapper_from_raw_allowlist() -> None:
    # Recovery is centralized in compare_snapshots: an API caller passing the raw
    # new-manifest surface still has a dropped committed wrapper recovered, so its
    # removal stays in the verdict rather than being demoted.
    from abicheck.checker_policy import Verdict
    from abicheck.service import compare_snapshots

    old = _snap([_cfn("pp_foo"), _cfn("pp_removed")])
    new = _snap([_cfn("pp_foo")])
    result = compare_snapshots(old, new, public_surface_allowlist={"pp_foo"})
    assert result.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    assert not any("pp_removed" == c.symbol for c in result.out_of_surface_changes)
    # __pp_* private churn is still demoted; no allowlist -> no scoping.
    plain = compare_snapshots(old, new)  # no allowlist
    assert plain.verdict in (Verdict.BREAKING, Verdict.API_BREAK)


def test_compare_snapshots_recovers_omitted_still_exported_wrapper() -> None:
    from abicheck.checker_policy import Verdict
    from abicheck.service import compare_snapshots

    old = _snap([_cfn("pp_foo"), _cfn("pp_omitted", "int", ("int",))])
    new = _snap([_cfn("pp_foo"), _cfn("pp_omitted", "long", ("long",))])

    result = compare_snapshots(old, new, public_surface_allowlist={"pp_foo"})

    assert result.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    assert not any("pp_omitted" == c.symbol for c in result.out_of_surface_changes)


def test_loader_level_finding_survives_manifest_scope() -> None:
    # Codex: a SONAME/NEEDED change (symbol is a pseudo-name like DT_SONAME, not
    # a real export) breaks linked clients regardless of the POST export set, so
    # the exact-export-name filter must never demote it.
    from abicheck.post_processing import FilterNonPublicSurface, PipelineContext

    snap = _snap([_cfn("pp_foo")])
    ctx = PipelineContext(old=snap, new=snap, public_surface_allowlist={"pp_foo"})
    soname = Change(kind=ChangeKind.SONAME_CHANGED, symbol="DT_SONAME", description="")
    needed = Change(kind=ChangeKind.NEEDED_REMOVED, symbol="DT_NEEDED", description="")

    kept = FilterNonPublicSurface().run([soname, needed], ctx)
    assert soname in kept and needed in kept
    assert not ctx.out_of_surface


def test_hidden_friend_removal_survives_manifest_scope() -> None:
    # Codex: a hidden friend can never appear in an ELF/PE/Mach-O export
    # table by construction, but its mangled name still shows up in a
    # header/L2 snapshot's function list -- the same list
    # _snapshot_export_ids reads with no visibility filter. Without the
    # hidden-friend exclusion, is_symbol_level_finding(c) and "sym in
    # export_ids" both come back True, so the removal reads as "a real
    # export missing from the committed manifest" and gets silently
    # demoted -- hiding a genuine public ADL break.
    from abicheck.post_processing import FilterNonPublicSurface, PipelineContext

    snap_old = _snap([_cfn("pp_foo")])
    hidden_friend = Function(
        name="operator==",
        mangled="_ZN2ns3FooeqERKS0_S1_",
        return_type="bool",
        params=[Param(name="a", type="const Foo&"), Param(name="b", type="const Foo&")],
        visibility=Visibility.HIDDEN,
        is_hidden_friend=True,
    )
    snap_old.functions.append(hidden_friend)
    snap_new = _snap([_cfn("pp_foo")])
    ctx = PipelineContext(
        old=snap_old, new=snap_new, public_surface_allowlist={"pp_foo"}
    )
    finding = Change(
        kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
        symbol=hidden_friend.mangled,
        old_value=hidden_friend.name,
        description="",
    )

    kept = FilterNonPublicSurface().run([finding], ctx)
    assert finding in kept
    assert finding not in ctx.out_of_surface


def test_metadata_only_private_export_is_demoted() -> None:
    # Codex: a private __pp_* helper present only in platform export metadata
    # (ELF .dynsym) — not in DWARF functions/variables — must still be
    # recognized as a concrete export and demoted, not kept.
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
    from abicheck.post_processing import FilterNonPublicSurface, PipelineContext

    elf = ElfMetadata(
        soname="libpost.so",
        symbols=[
            ElfSymbol(name="__pp_impl", sym_type=SymbolType.FUNC),
        ],
    )
    snap = AbiSnapshot(library="libpost.so", version="1", elf=elf)
    ctx = PipelineContext(old=snap, new=snap, public_surface_allowlist={"pp_foo"})
    finding = Change(
        kind=ChangeKind.SYMBOL_TYPE_CHANGED, symbol="__pp_impl", description=""
    )

    kept = FilterNonPublicSurface().run([finding], ctx)
    assert finding not in kept
    assert finding in ctx.out_of_surface


def test_is_symbol_level_finding_partitions_kinds() -> None:
    # Function/variable changes are symbol-level; type/member changes are not.
    def _c(kind: ChangeKind) -> Change:
        return Change(kind=kind, symbol="x", description="")

    assert is_symbol_level_finding(_c(ChangeKind.FUNC_REMOVED))
    assert not is_symbol_level_finding(_c(ChangeKind.TYPE_FIELD_REMOVED))


def test_compare_cli_post_manifest_flag_scopes_to_committed_surface(
    tmp_path: Path,
) -> None:
    # End-to-end: `compare old.json new.json --post-manifest m.json` loads the
    # manifest, scopes to its pp_* surface, and demotes private kernel churn.
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(
        snapshot_to_json(_snap([_cfn("pp_foo"), _cfn("__pp_foo_impl")])),
        encoding="utf-8",
    )
    new_p.write_text(snapshot_to_json(_snap([_cfn("pp_foo")])), encoding="utf-8")

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "post_abi": 1,
                "exports": [
                    {
                        "name": "foo",
                        "c_symbol": "pp_foo",
                        "params": ["Float64"],
                        "return_dtype": "Float64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_p),
            str(new_p),
            "--post-manifest",
            str(manifest),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output  # kernel churn demoted -> compatible
    doc = json.loads(res.output[res.output.find("{") :])
    demoted = json.dumps(doc.get("surface_scope", {}))
    assert "__pp_foo_impl" in demoted


def test_post_manifest_ledger_shown_even_with_no_scope_public_headers(
    tmp_path: Path,
) -> None:
    # Codex: a manifest allowlist scopes the comparison independently of header
    # scoping. Combined with --no-scope-public-headers, demoted findings must
    # still appear in the surface_scope ledger — a clean verdict must not hide
    # that filtering happened.
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(
        snapshot_to_json(_snap([_cfn("pp_foo"), _cfn("__pp_foo_impl")])),
        encoding="utf-8",
    )
    new_p.write_text(snapshot_to_json(_snap([_cfn("pp_foo")])), encoding="utf-8")

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "post_abi": 1,
                "exports": [
                    {
                        "name": "foo",
                        "c_symbol": "pp_foo",
                        "params": ["Float64"],
                        "return_dtype": "Float64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_p),
            str(new_p),
            "--post-manifest",
            str(manifest),
            "--no-scope-public-headers",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output[res.output.find("{") :])
    assert "surface_scope" in doc, "ledger hidden despite manifest scoping"
    assert "__pp_foo_impl" in json.dumps(doc["surface_scope"])


def test_compare_cli_post_manifest_keeps_omitted_old_pp_symbol_in_scope(
    tmp_path: Path,
) -> None:
    # Regression: a new manifest that omits a still-exported old pp_* wrapper
    # must not be able to scope that wrapper's ABI changes out of the verdict.
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(
        snapshot_to_json(
            _snap([_cfn("pp_foo"), _cfn("pp_undeclared", "int", ("int",))])
        ),
        encoding="utf-8",
    )
    new_p.write_text(
        snapshot_to_json(
            _snap([_cfn("pp_foo"), _cfn("pp_undeclared", "long", ("long",))])
        ),
        encoding="utf-8",
    )

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "post_abi": 1,
                "exports": [
                    {
                        "name": "foo",
                        "c_symbol": "pp_foo",
                        "params": ["Float64"],
                        "return_dtype": "Float64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_p),
            str(new_p),
            "--post-manifest",
            str(manifest),
            "--no-scope-public-headers",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 4, res.output
    doc = json.loads(res.output[res.output.find("{") :])
    assert doc["verdict"] == "BREAKING"
    assert "pp_undeclared" in json.dumps(doc.get("changes", []))
    assert "pp_undeclared" not in json.dumps(doc.get("surface_scope", {}))


def test_contract_scope_allowlist_unions_old_committed_symbols() -> None:
    # The union recovers any old committed wrapper, including one omitted from
    # the new manifest but still exported in both snapshots. Such wrappers stay
    # in-surface so manifest omissions cannot hide their ABI changes.
    from abicheck.post_manifest import contract_scope_allowlist, parse_manifest

    manifest = parse_manifest(
        {
            "post_abi": 1,
            "exports": [
                {
                    "name": "foo",
                    "c_symbol": "pp_foo",
                    "params": ["Float64"],
                    "return_dtype": "Float64",
                }
            ],
        }
    )
    old = _snap(
        [_cfn("pp_foo"), _cfn("pp_removed"), _cfn("pp_undeclared"), _cfn("__pp_impl")]
    )
    new = _snap([_cfn("pp_foo"), _cfn("pp_undeclared")])

    allow = contract_scope_allowlist(manifest, old, new)
    assert "pp_foo" in allow  # committed
    assert "pp_removed" in allow  # removed committed wrapper -> kept in-surface
    assert "pp_undeclared" in allow  # old committed wrapper -> kept in-surface
    assert "__pp_impl" not in allow  # private kernel -> demoted


def test_contract_scope_allowlist_excludes_removed_data_variables() -> None:
    # POST commitments are callable wrappers/ufunc loops; a removed pp_* *data*
    # variable is not a wrapper removal, so it must not be unioned into the
    # allowlist (its var_removed stays demoted under manifest scoping).
    from abicheck.model import Variable
    from abicheck.post_manifest import contract_scope_allowlist, parse_manifest

    manifest = parse_manifest(
        {
            "post_abi": 1,
            "exports": [
                {
                    "name": "foo",
                    "c_symbol": "pp_foo",
                    "params": ["Float64"],
                    "return_dtype": "Float64",
                }
            ],
        }
    )
    old = AbiSnapshot(
        library="l",
        version="1",
        functions=[_cfn("pp_foo")],
        variables=[Variable(name="pp_data", mangled="pp_data", type="int")],
    )
    new = _snap([_cfn("pp_foo")])  # pp_data variable removed

    allow = contract_scope_allowlist(manifest, old, new)
    assert "pp_data" not in allow


def test_compare_cli_malformed_post_manifest_is_clean_usage_error(
    tmp_path: Path,
) -> None:
    # A malformed --post-manifest must produce a clean usage error, not a raw
    # traceback (the load is wrapped in click.UsageError).
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(_snap([_cfn("pp_foo")])), encoding="utf-8")
    new_p.write_text(snapshot_to_json(_snap([_cfn("pp_foo")])), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")

    res = CliRunner().invoke(
        main,
        ["compare", str(old_p), str(new_p), "--post-manifest", str(bad)],
    )
    assert res.exit_code != 0
    assert "--post-manifest" in res.output and "invalid JSON" in res.output
    assert res.exception is None or isinstance(res.exception, SystemExit)


def test_contract_scope_allowlist_recovers_hidden_wrapper() -> None:
    # P1: a committed wrapper made *hidden* in the new build still has a Function
    # entry, but clients can no longer link to it. It must count as "removed"
    # (not present) so its visibility break stays in-surface.
    from abicheck.model import Visibility
    from abicheck.post_manifest import contract_scope_allowlist, parse_manifest

    manifest = parse_manifest(
        {
            "post_abi": 1,
            "exports": [
                {
                    "name": "foo",
                    "c_symbol": "pp_foo",
                    "params": ["Float64"],
                    "return_dtype": "Float64",
                }
            ],
        }
    )
    old = _snap([_cfn("pp_foo"), _cfn("pp_hidden")])
    hidden = _cfn("pp_hidden")
    hidden.visibility = Visibility.HIDDEN
    new = _snap([_cfn("pp_foo"), hidden])

    allow = contract_scope_allowlist(manifest, old, new)
    # pp_hidden is public in old, hidden (unlinkable) in new -> recovered.
    assert "pp_hidden" in allow


def test_compare_cli_removed_committed_wrapper_still_breaks(tmp_path: Path) -> None:
    # P1: `--post-manifest new.json` after a release DROPS a committed pp_*
    # wrapper. The new manifest no longer lists it, but removing a committed
    # export is breaking — it must NOT be demoted just because the (new) manifest
    # omits it. Its symbol still lives in the old binary.
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(
        snapshot_to_json(_snap([_cfn("pp_foo"), _cfn("pp_removed")])), encoding="utf-8"
    )
    new_p.write_text(snapshot_to_json(_snap([_cfn("pp_foo")])), encoding="utf-8")

    manifest = tmp_path / "new.json.manifest"
    manifest.write_text(
        json.dumps(
            {
                "post_abi": 2,  # pp_removed dropped from v2 manifest
                "exports": [
                    {
                        "name": "foo",
                        "c_symbol": "pp_foo",
                        "params": ["Float64"],
                        "return_dtype": "Float64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_p),
            str(new_p),
            "--post-manifest",
            str(manifest),
            "--format",
            "json",
        ],
    )
    assert res.exit_code != 0, res.output  # removed committed wrapper -> breaking
    doc = json.loads(res.output[res.output.find("{") :])
    # pp_removed must be a live finding, not demoted to the filtered ledger.
    assert "pp_removed" not in json.dumps(doc.get("surface_scope", {}))
