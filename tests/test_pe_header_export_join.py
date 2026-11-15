# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The default PE header dump joins castxml's Itanium spellings to MSVC exports.

Windows ``case47`` and ``case99`` reported ``public_not_exported`` plus
``exported_not_public`` on every class member and namespaced function: castxml
spells ``_ZN10Calculator3addEii`` while the DLL exports
``?add@Calculator@@QEAAHHH@Z``. The oracles below are the MSVC names copied
from the failing Windows log, not values computed by the code under test.
"""

from __future__ import annotations

import pytest

from abicheck.extract.header_export_binding import (
    bind_pe_header_exports,
    bind_pe_header_operand,
)
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import AbiSnapshot, Function, Param, ScopeOrigin, Visibility
from abicheck.model.export_index import ExportMatch
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.model.surface_facts import (
    binary_export_match,
    bound_export_spelling,
    in_public_surface,
)

_AMD64 = "IMAGE_FILE_MACHINE_AMD64"


def _decl(
    name: str,
    mangled: str,
    params: tuple[str, ...] = (),
    *,
    exported: ExportMatch = ExportMatch.ABSENT,
) -> Function:
    """A castxml header declaration: Itanium-spelled, as the header parse left it."""
    return Function(
        name=name,
        mangled=mangled,
        return_type="int" if name in {"add", "multiply", "subtract"} else "void",
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC
        if exported is ExportMatch.DYNAMIC
        else Visibility.HIDDEN,
        **header_ast_surface_facts(exported=exported, producer="castxml"),
    )


def _pe_snapshot(exports: list[str], *decls: Function) -> AbiSnapshot:
    snap = AbiSnapshot(library="lib.dll", version="1", from_headers=True)
    snap.platform = "pe"
    snap.pe = PeMetadata(
        machine=_AMD64, exports=[PeExport(name=sym) for sym in exports]
    )
    snap.declarations.functions = list(decls)
    return snap


# (declaration, Itanium spelling castxml gives it, MSVC export the DLL carries)
_CASES = [
    # Class member, the case47 shape.
    (
        _decl("add", "_ZN10Calculator3addEii", ("int", "int")),
        "?add@Calculator@@QEAAHHH@Z",
    ),
    (
        _decl("multiply", "_ZN10Calculator8multiplyEii", ("int", "int")),
        "?multiply@Calculator@@QEAAHHH@Z",
    ),
    # Namespaced free function, the case99 shape.
    (_decl("sort", "_ZN3lib4sortEv"), "?sort@lib@@YAXXZ"),
    # Two levels of namespace.
    (
        _decl("sort", "_ZN3lib12experimental4sortEv"),
        "?sort@experimental@lib@@YAXXZ",
    ),
    (
        _decl("other_fn", "_ZN3lib12experimental8other_fnEv"),
        "?other_fn@experimental@lib@@YAXXZ",
    ),
    # Global free function, with a parameter.
    (
        _decl("f", "_Z1fi", ("int",)),
        "?f@@YAXH@Z",
    ),
]


@pytest.mark.parametrize("decl, export", _CASES, ids=[e for _, e in _CASES])
def test_declaration_binds_to_its_msvc_export(decl: Function, export: str) -> None:
    snap = _pe_snapshot([export], decl)
    bind_pe_header_exports(snap)
    assert bound_export_spelling(decl) == export
    assert binary_export_match(decl) is ExportMatch.DEMANGLED
    assert in_public_surface(decl)


def test_overloads_bind_by_their_own_parameter_list() -> None:
    # Two `add` overloads: each binds only to the export of its own signature.
    int_add = _decl("add", "_ZN10Calculator3addEii", ("int", "int"))
    dbl_add = _decl("add", "_ZN10Calculator3addEdd", ("double", "double"))
    snap = _pe_snapshot(
        ["?add@Calculator@@QEAAHHH@Z", "?add@Calculator@@QEAANNN@Z"],
        int_add,
        dbl_add,
    )
    bind_pe_header_exports(snap)
    assert bound_export_spelling(int_add) == "?add@Calculator@@QEAAHHH@Z"
    assert bound_export_spelling(dbl_add) == "?add@Calculator@@QEAANNN@Z"


def test_extern_c_declaration_binds_to_its_plain_export() -> None:
    # extern "C" is not decorated on x64: the export is the bare name.
    # The header backend's own exact lookup already matched it: the binder
    # leaves a direct match alone and it stays public.
    decl = _decl("c_api", "c_api", exported=ExportMatch.DYNAMIC)
    snap = _pe_snapshot(["c_api"], decl)
    bind_pe_header_exports(snap)
    assert decl.mangled == "c_api"
    assert in_public_surface(decl)


def test_negative_control_unexported_declaration_stays_unbound() -> None:
    # A real missing export: the header declares it, the DLL does not carry it.
    # It must stay unbound so public_not_exported still reports it.
    missing = _decl("gone", "_ZN10Calculator4goneEv")
    snap = _pe_snapshot(["?add@Calculator@@QEAAHHH@Z"], missing)
    bind_pe_header_exports(snap)
    assert missing.mangled == "_ZN10Calculator4goneEv"
    assert missing.visibility is Visibility.HIDDEN


def test_signature_mismatch_is_not_bound() -> None:
    # The DLL exports `add(double, double)`; the header declares `add(int, int)`.
    # Same name, different parameter list: a different symbol, not a binding.
    decl = _decl("add", "_ZN10Calculator3addEii", ("int", "int"))
    snap = _pe_snapshot(["?add@Calculator@@QEAANNN@Z"], decl)
    bind_pe_header_exports(snap)
    assert decl.mangled == "_ZN10Calculator3addEii"
    assert decl.visibility is Visibility.HIDDEN


def test_snapshot_without_pe_exports_is_left_alone() -> None:
    decl = _decl("add", "_ZN10Calculator3addEii", ("int", "int"))
    snap = AbiSnapshot(library="lib.so", version="1", from_headers=True)
    snap.declarations.functions = [decl]
    bind_pe_header_exports(snap)
    assert decl.mangled == "_ZN10Calculator3addEii"


def test_header_pe_dump_tail_binds_before_export_reconciliation() -> None:
    # The PE builders' shared tail is where the default header dump gets its
    # export facts; the binding must already have happened by then.
    from abicheck.extract.export_table_read import finish_binary_snapshot

    decl = _decl("add", "_ZN10Calculator3addEii", ("int", "int"))
    snap = _pe_snapshot(["?add@Calculator@@QEAAHHH@Z"], decl)
    finish_binary_snapshot(snap)
    assert bound_export_spelling(decl) == "?add@Calculator@@QEAAHHH@Z"
    assert decl.mangled == "_ZN10Calculator3addEii"
    assert binary_export_match(decl) is ExportMatch.DEMANGLED


def test_headerless_pe_tail_leaves_declarations_alone() -> None:
    from abicheck.extract.export_table_read import finish_binary_snapshot

    decl = _decl("add", "_ZN10Calculator3addEii", ("int", "int"))
    snap = _pe_snapshot(["?add@Calculator@@QEAAHHH@Z"], decl)
    snap.from_headers = False
    finish_binary_snapshot(snap)
    assert decl.mangled == "_ZN10Calculator3addEii"


def _stored_gen71_pe(*decls: Function, exports: list[str]) -> AbiSnapshot:
    """A header-backed PE baseline as a pre-generation-72 extractor wrote it:
    castxml's Itanium spellings, never joined to the DLL's exports."""
    return _pe_snapshot(exports, *decls)


def _bound_gen72_pe(*decls: Function, exports: list[str]) -> AbiSnapshot:
    """The same library dumped at generation 72: the join ran at extraction."""
    snap = _pe_snapshot(exports, *decls)
    bind_pe_header_exports(snap)
    return snap


def _kinds(result) -> list[tuple[str, str]]:
    return sorted((str(c.kind.value), c.symbol) for c in result.changes)


def test_stored_gen71_baseline_compares_clean_against_gen72_dump() -> None:
    from abicheck.workflows.compare_policy import compare_snapshots

    def members() -> list[Function]:
        return [
            _decl("add", "_ZN10Calculator3addEii", ("int", "int")),
            _decl("multiply", "_ZN10Calculator8multiplyEii", ("int", "int")),
        ]

    exports = ["?add@Calculator@@QEAAHHH@Z", "?multiply@Calculator@@QEAAHHH@Z"]
    old = _stored_gen71_pe(*members(), exports=exports)
    new = _bound_gen72_pe(*members(), exports=exports)
    result = compare_snapshots(old, new)
    assert _kinds(result) == [], result.changes
    # The caller's stored baseline is not mutated by the join.
    assert old.declarations.functions[0].mangled == "_ZN10Calculator3addEii"


def test_member_really_removed_in_v2_is_still_reported() -> None:
    from abicheck.workflows.compare_policy import compare_snapshots

    old = _stored_gen71_pe(
        _decl("add", "_ZN10Calculator3addEii", ("int", "int")),
        _decl("multiply", "_ZN10Calculator8multiplyEii", ("int", "int")),
        exports=["?add@Calculator@@QEAAHHH@Z", "?multiply@Calculator@@QEAAHHH@Z"],
    )
    # v2 drops multiply from the header and from the DLL.
    new = _bound_gen72_pe(
        _decl("add", "_ZN10Calculator3addEii", ("int", "int")),
        exports=["?add@Calculator@@QEAAHHH@Z"],
    )
    kinds = _kinds(compare_snapshots(old, new))
    assert kinds == [
        ("func_removed", "_ZN10Calculator8multiplyEii"),
        ("public_surface_shrank", "<surface>"),
    ], kinds


@pytest.mark.parametrize(
    "export",
    [
        # Decodable, and a different parameter list (int, int) than int*.
        "?add@Calculator@@QEAAHHH@Z",
        # Undecodable on the export side too (a pointer parameter).
        "?add@Calculator@@QEAAHPEAH@Z",
    ],
)
def test_undecodable_parameter_declaration_does_not_bind_a_lone_export(
    export: str,
) -> None:
    # The header declares add(int*). Its parameter identity cannot be decided
    # from either spelling, so the lone export of the same name must not claim
    # it: a real missing export would otherwise be hidden.
    decl = _decl("add", "_ZN10Calculator3addEPi", ("int*",))
    snap = _pe_snapshot([export], decl)
    bind_pe_header_exports(snap)
    assert decl.mangled == "_ZN10Calculator3addEPi"
    assert decl.visibility is Visibility.HIDDEN


def test_no_baseline_run_joins_a_stored_pe_snapshot(monkeypatch) -> None:
    # A no-baseline run is the one path with old=None. A single new-only run
    # reports no pairwise change, so the observable effect is the operand that
    # reaches the comparison: it must carry the joined MSVC spelling.
    import abicheck.workflows.no_baseline_compare as nb

    received: list[Function] = []
    real_compare = nb._diff_pair

    def spy(old, new, **kwargs):
        received.extend(new.declarations.functions)
        return real_compare(old, new, **kwargs)

    monkeypatch.setattr(nb, "_diff_pair", spy)
    stored = _stored_gen71_pe(
        _decl("add", "_ZN10Calculator3addEii", ("int", "int")),
        exports=["?add@Calculator@@QEAAHHH@Z"],
    )
    nb.run_no_baseline_compare(stored)
    assert [bound_export_spelling(d) for d in received] == [
        "?add@Calculator@@QEAAHHH@Z"
    ]


def test_env_matrix_compare_joins_a_stored_pe_snapshot() -> None:
    from abicheck.environment_matrix import EnvironmentMatrix
    from abicheck.workflows.compare_policy import compare_snapshots

    def members() -> list[Function]:
        return [_decl("add", "_ZN10Calculator3addEii", ("int", "int"))]

    exports = ["?add@Calculator@@QEAAHHH@Z"]
    old = _stored_gen71_pe(*members(), exports=exports)
    new = _bound_gen72_pe(*members(), exports=exports)
    result = compare_snapshots(old, new, env_matrix=EnvironmentMatrix())
    assert _kinds(result) == [], result.changes


def test_raw_checker_compare_is_clean_on_bound_operands() -> None:
    # Documented limitation: a raw checker.compare call does not bind a stored
    # pre-72 PE operand itself. Callers must pass bound operands, as this does.
    from abicheck.checker import compare
    from abicheck.extract.header_export_binding import bind_pe_header_operand

    def members() -> list[Function]:
        return [_decl("add", "_ZN10Calculator3addEii", ("int", "int"))]

    exports = ["?add@Calculator@@QEAAHHH@Z"]
    old = bind_pe_header_operand(_stored_gen71_pe(*members(), exports=exports))
    new = bind_pe_header_operand(_stored_gen71_pe(*members(), exports=exports))
    assert _kinds(compare(old, new)) == []


_CALC_EXPORT = "?add@Calculator@@QEAAHHH@Z"


def _calc_side(*, inline: bool, exported: bool, present: bool = True) -> AbiSnapshot:
    """One version of Calculator::add as a header-backed PE operand."""
    if not present:
        return _pe_snapshot([], *[])
    decl = _decl("add", "_ZN10Calculator3addEii", ("int", "int"))
    decl.is_inline = inline
    decl.origin = ScopeOrigin.PUBLIC_HEADER
    return _pe_snapshot([_CALC_EXPORT] if exported else [], decl)


def _compare_public(old: AbiSnapshot, new: AbiSnapshot):
    from abicheck.workflows.compare_policy import compare_snapshots

    return compare_snapshots(bind_pe_header_operand(old), bind_pe_header_operand(new))


def test_inline_to_outlined_is_compatible_without_an_inline_pair() -> None:
    # case47's shape: inline and unexported in v1, out-of-line and exported in v2.
    result = _compare_public(
        _calc_side(inline=True, exported=False),
        _calc_side(inline=False, exported=True),
    )
    kinds = _kinds(result)
    assert result.verdict.value == "COMPATIBLE", kinds
    assert ("func_added", "_ZN10Calculator3addEii") in kinds, kinds
    assert all(kind != "inline_function_removed" for kind, _ in kinds), kinds


def test_outlined_to_inline_is_a_break() -> None:
    result = _compare_public(
        _calc_side(inline=False, exported=True),
        _calc_side(inline=True, exported=False),
    )
    assert result.verdict.value == "BREAKING", _kinds(result)
    assert ("func_visibility_changed", "_ZN10Calculator3addEii") in _kinds(result)


def test_unchanged_exported_member_is_no_change() -> None:
    result = _compare_public(
        _calc_side(inline=False, exported=True),
        _calc_side(inline=False, exported=True),
    )
    assert result.verdict.value == "NO_CHANGE", _kinds(result)
    assert _kinds(result) == []


def test_really_removed_member_is_still_reported() -> None:
    result = _compare_public(
        _calc_side(inline=False, exported=True),
        _calc_side(inline=False, exported=False, present=False),
    )
    kinds = _kinds(result)
    assert result.verdict.value == "BREAKING", kinds
    assert ("func_removed", "_ZN10Calculator3addEii") in kinds, kinds


def test_no_baseline_operand_passes_through_unchanged() -> None:
    # A no-baseline comparison has no old operand: binding it is a no-op.
    assert bind_pe_header_operand(None) is None


def _var(
    name: str, mangled: str, typ: str, *, exported: ExportMatch = ExportMatch.ABSENT
):
    from abicheck.model import Variable

    var = Variable(
        name=name,
        mangled=mangled,
        type=typ,
        visibility=Visibility.PUBLIC
        if exported is ExportMatch.DYNAMIC
        else Visibility.HIDDEN,
        **header_ast_surface_facts(exported=exported, producer="castxml"),
    )
    var.origin = ScopeOrigin.PUBLIC_HEADER
    return var


def _variable_noise(decls, exports: list[str]) -> list[str]:
    """Public-surface noise a header-backed PE variable snapshot reports."""
    from abicheck.workflows.compare_policy import compare_snapshots

    snap = AbiSnapshot(library="lib.dll", version="1", from_headers=True)
    snap.platform = "pe"
    snap.pe = PeMetadata(
        machine=_AMD64, exports=[PeExport(name=sym) for sym in exports]
    )
    snap.declarations.variables = list(decls)
    bound = bind_pe_header_operand(snap)
    result = compare_snapshots(bound, bound)
    return [
        k
        for k, _ in _kinds(result)
        if k in ("public_not_exported", "exported_not_public")
    ]


def test_cpp_namespace_global_int_binds_to_its_data_export() -> None:
    assert _variable_noise([_var("g", "g", "int")], ["?g@@3HA"]) == []


def test_namespaced_global_int_binds_to_its_data_export() -> None:
    assert _variable_noise([_var("g", "_ZN2ns1gE", "int")], ["?g@ns@@3HA"]) == []


def test_c_global_with_its_plain_export_is_unchanged() -> None:
    # A C global exports under its plain name: the header backend's exact lookup
    # already matched it, and the binder has nothing to do.
    decl = _var("g", "g", "int", exported=ExportMatch.DYNAMIC)
    assert _variable_noise([decl], ["g"]) == []


def test_c_global_with_missing_export_is_still_reported() -> None:
    # A real missing export: no plain export and no C++ data symbol to pair with.
    assert "public_not_exported" in _variable_noise([_var("g", "g", "int")], [])


def test_cpp_global_with_a_disagreeing_type_is_refused() -> None:
    # ?g@@3NA decodes to double; the declaration is int. Not the same variable.
    noise = _variable_noise([_var("g", "g", "int")], ["?g@@3NA"])
    assert "public_not_exported" in noise


def test_static_data_member_binds_to_its_data_export() -> None:
    assert _variable_noise([_var("s", "_ZN1C1sE", "int")], ["?s@C@@2HA"]) == []
