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

"""Bug class ``extraction.header_surface_discarded_on_export_mismatch``.

A parsed header declaration must be bound to the export it names whatever
mangling scheme each side spells it in, and never to an export it does not
name. The oracle is a pair of encoders written here from the Itanium and
Microsoft C++ ABI rules (builtin parameter types only), independent of the
parsers ``extract.header_export_binding`` uses. The domain is enumerated
exhaustively: every set of up to three declarations drawn from
{2 names} x {3 scopes} x {4 parameter lists}.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.extract.header_export_binding import (
    bind_header_declarations_to_exports,
    msvc_builtin_param_types,
)
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility
from abicheck.model.export_index import ExportMatch
from abicheck.model.surface_facts import binary_export_match, in_public_surface

_ITANIUM = {"int": "i", "long": "l", "double": "d", "unsigned int": "j", "bool": "b"}
_MSVC = {"int": "H", "long": "J", "double": "N", "unsigned int": "I", "bool": "_N"}


def itanium(scope: tuple[str, ...], name: str, params: tuple[str, ...]) -> str:
    enc = "".join(f"{len(p)}{p}" for p in (*scope, name))
    head = f"_ZN{enc}E" if scope else f"_Z{enc}"
    return head + ("".join(_ITANIUM[p] for p in params) or "v")


def ms_abi(scope: tuple[str, ...], name: str, params: tuple[str, ...]) -> str:
    qual = "@".join((name, *reversed(scope)))
    args = "".join(_MSVC[p] for p in params) + "@Z" if params else "XZ"
    return f"?{qual}@@YAH{args}"


def _decl(scope, name, params, *, spelling=itanium) -> Function:
    return Function(
        name=name,
        mangled=spelling(scope, name, params),
        return_type="int",
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.HIDDEN,
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml"),
    )


def _snap(*decls) -> AbiSnapshot:
    snap = AbiSnapshot(library="l.dll", version="1", from_headers=True)
    snap.declarations.functions = [d for d in decls if isinstance(d, Function)]
    snap.declarations.variables = [d for d in decls if isinstance(d, Variable)]
    return snap


_NAMES = ("f", "g")
_SCOPES = ((), ("ns",), ("a", "b"))
_PARAMS = ((), ("int",), ("double",), ("int", "long"))
_ENTITIES = list(itertools.product(_SCOPES, _NAMES, _PARAMS))
_DOMAIN = [c for k in (1, 2, 3) for c in itertools.combinations(_ENTITIES, k)]


def test_domain_is_exhaustive():
    assert len(_ENTITIES) == 24
    assert len(_DOMAIN) == 24 + 276 + 2024


@pytest.mark.parametrize(
    ("decl_spelling", "export_spelling"),
    [(itanium, ms_abi), (ms_abi, itanium)],
    ids=["itanium-parse-ms-abi-dll", "ms-abi-parse-mingw-dll"],
)
def test_every_declaration_binds_to_its_own_export(decl_spelling, export_spelling):
    for combo in _DOMAIN:
        decls = [_decl(*e, spelling=decl_spelling) for e in combo]
        expected = {
            id(d): export_spelling(*e) for d, e in zip(decls, combo, strict=True)
        }
        snap = _snap(*decls)
        exports = [export_spelling(*e) for e in combo]
        out = bind_header_declarations_to_exports(snap, exports)
        # Overloads are told apart by their decoded parameter lists, in both
        # directions: every declaration binds exactly its own export.
        for d, e in zip(decls, combo, strict=True):
            assert d.mangled == expected[id(d)], combo
            assert in_public_surface(d), combo
            assert binary_export_match(d) is ExportMatch.DEMANGLED
        assert out.unbound_exports == (), combo


@pytest.mark.parametrize("spelling", [itanium, ms_abi], ids=["itanium", "ms-abi"])
def test_same_scheme_mismatch_is_a_different_symbol(spelling):
    # f(int) declared, f(long) exported in the SAME scheme: a changed
    # signature, never a spelling gap.
    d = _decl(("ns",), "f", ("int",), spelling=spelling)
    out = bind_header_declarations_to_exports(
        _snap(d), [spelling(("ns",), "f", ("long",))]
    )
    assert d.visibility is Visibility.HIDDEN
    assert out.bound == ()
    assert not out.any_declaration_bound


def test_decoded_signature_disagreement_vetoes_the_binding():
    d = _decl(("ns",), "f", ("int",))
    sym = ms_abi(("ns",), "f", ("long",))
    out = bind_header_declarations_to_exports(_snap(d), [sym])
    assert d.visibility is Visibility.HIDDEN
    assert out.ambiguous == (sym,)
    assert out.unbound_exports == (sym,)


@pytest.mark.known_gap_canary
def test_undecodable_overloads_stay_unbound_and_are_reported():
    """Known gap pe-overloads-with-undecoded-parameter-types-stay-unbound."""
    # Template-typed parameters: MSVC's `?$name@args@` encoding is not
    # decoded, so the two overloads cannot be told apart.
    a = _decl(("ns",), "f", ())
    b = _decl(("ns",), "f", ())
    a.params = [Param(name="x", type="std::vector<int>")]
    b.params = [Param(name="x", type="std::vector<double>")]
    syms = ["?f@ns@@YAHV?$vector@H@std@@@Z", "?f@ns@@YAHV?$vector@N@std@@@Z"]
    out = bind_header_declarations_to_exports(_snap(a, b), syms)
    assert out.bound == ()
    assert set(out.ambiguous) == set(syms)
    assert a.visibility is b.visibility is Visibility.HIDDEN


@pytest.mark.parametrize(
    ("header", "export"),
    [
        (("ns::A",), "?g@ns@@YAHUA@1@@Z"),  # struct by value, scope back-reference
        (("A",), "?g@ns@@YAHUA@1@@Z"),  # clang's scope-relative spelling
        (("const ns::A &",), "?g@ns@@YAHAEBUA@1@@Z"),
        (("ns::A *",), "?g@ns@@YAHPEAUA@1@@Z"),
        (("ns::A &&",), "?g@ns@@YAH$$QEAUA@1@@Z"),
        (("ns::E",), "?g@ns@@YAHW4E@1@@Z"),  # enum
        (("ns::A", "ns::A"), "?g@ns@@YAHUA@1@0@Z"),  # parameter back-reference
        (("const char *", "int"), "?g@ns@@YAHPEBDH@Z"),
    ],
)
def test_class_typed_parameters_decode_and_bind(header, export):
    d = _decl(("ns",), "g", ())
    d.params = [Param(name=f"p{i}", type=t) for i, t in enumerate(header)]
    bind_header_declarations_to_exports(_snap(d), [export])
    assert d.mangled == export and in_public_surface(d)


@pytest.mark.parametrize(
    ("header", "export"),
    [
        (("ns::B",), "?g@ns@@YAHUA@1@@Z"),  # another class
        (("ns::A &",), "?g@ns@@YAHAEBUA@1@@Z"),  # const-ness of the referee
        (("ns::A",), "?g@ns@@YAHPEAUA@1@@Z"),  # value vs pointer
    ],
)
def test_class_typed_parameter_mismatch_vetoes(header, export):
    d = _decl(("ns",), "g", ())
    d.params = [Param(name=f"p{i}", type=t) for i, t in enumerate(header)]
    out = bind_header_declarations_to_exports(_snap(d), [export])
    assert out.bound == () and out.ambiguous == (export,)


def test_unique_name_with_an_undecodable_signature_does_not_bind():
    """Binding by uniqueness alone would bind this function in a release
    with one overload and not in one with two: two identities for one
    function across the pair."""
    d = _decl(("ns",), "f", ())
    d.params = [Param(name="x", type="std::vector<int>")]
    sym = "?f@ns@@YAHV?$vector@H@std@@@Z"
    out = bind_header_declarations_to_exports(_snap(d), [sym])
    assert out.bound == () and out.ambiguous == (sym,)


def test_directly_matched_declarations_are_left_alone():
    direct = Function(
        name="cfun",
        mangled="cfun",
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
        **header_ast_surface_facts(exported=ExportMatch.DYNAMIC, producer="castxml"),
    )
    cxx = _decl((), "f", ("int",))
    out = bind_header_declarations_to_exports(
        _snap(direct, cxx), ["cfun", "?f@@YAHH@Z"]
    )
    assert out.direct == 1
    assert direct.mangled == "cfun"
    assert binary_export_match(direct) is ExportMatch.DYNAMIC
    assert out.bound == (("_Z1fi", "?f@@YAHH@Z"),)
    assert out.unbound_exports == ()


@pytest.mark.parametrize(
    ("export", "x86_32", "binds"),
    [
        ("_cfun", True, True),  # __cdecl
        ("_cfun@8", True, True),  # __stdcall
        ("@cfun@8", True, True),  # __fastcall
        ("cfun@@8", False, True),  # __vectorcall, every machine
        ("_cfun", False, False),  # x64 keeps the underscore: another name
        ("_cfun@8", False, False),
    ],
)
def test_x86_c_decoration_binds_only_where_the_machine_decorates(export, x86_32, binds):
    d = Function(
        name="cfun",
        mangled="cfun",
        return_type="int",
        visibility=Visibility.HIDDEN,
        is_extern_c=True,
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="clang"),
    )
    bind_header_declarations_to_exports(_snap(d), [export], x86_32=x86_32)
    assert in_public_surface(d) is binds
    assert d.mangled == (export if binds else "cfun")


def test_variable_binds_across_schemes():
    v = Variable(
        name="gv",
        mangled="_ZN5outer5inner2gvE",
        type="int",
        visibility=Visibility.HIDDEN,
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml"),
    )
    out = bind_header_declarations_to_exports(_snap(v), ["?gv@inner@outer@@3HA"])
    assert v.mangled == "?gv@inner@outer@@3HA"
    assert in_public_surface(v)
    assert out.unbound_exports == ()


def test_binding_resets_the_mangled_indexes():
    d = _decl((), "f", ("int",))
    snap = _snap(d)
    assert snap.function_map.get("_Z1fi") is d
    bind_header_declarations_to_exports(snap, ["?f@@YAHH@Z"])
    assert snap.function_map.get("?f@@YAHH@Z") is d
    assert "_Z1fi" not in snap.function_map


@pytest.mark.parametrize("params", [p for p in _PARAMS])
@pytest.mark.parametrize("member", ["YA", "YG", "QEAA", "QEBA", "SA", "UEAA"])
def test_msvc_parameter_decoder_round_trips(params, member):
    args = "".join(_MSVC[p] for p in params) + "@Z" if params else "XZ"
    sym = f"?f@C@@{member}H{args}"
    assert msvc_builtin_param_types(sym) == params


@pytest.mark.parametrize(
    "sym",
    [
        "?f@@YAHPEAH@Z",  # pointer parameter: not a builtin by value
        "??0C@@QEAA@XZ",  # constructor
        "?gv@@3HA",  # a variable
        "f",
        "?f@@YAH",  # truncated
    ],
)
def test_msvc_parameter_decoder_refuses_what_it_does_not_model(sym):
    assert msvc_builtin_param_types(sym) is None


@pytest.mark.parametrize("export", ["_Z4cfunv", "?cfun@@YAHXZ"])
def test_c_declaration_never_binds_a_cxx_export_of_the_same_name(export):
    d = Function(
        name="cfun",
        mangled="cfun",
        return_type="int",
        visibility=Visibility.HIDDEN,
        is_extern_c=True,
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml"),
    )
    out = bind_header_declarations_to_exports(_snap(d), [export], x86_32=True)
    assert d.visibility is Visibility.HIDDEN
    assert out.bound == ()


@pytest.mark.parametrize(
    ("code", "from_headers", "phrase"),
    [
        ("export-binding-unresolved", True, "could not be bound to any export"),
        ("header-backend-unavailable", False, "fell back to export-table mode"),
        ("mangling-fallback", False, "fell back to export-table mode"),
    ],
)
def test_assurance_note_says_what_the_side_kept(code, from_headers, phrase):
    from abicheck.policy.analysis_assurance._scope_fallback import (
        scope_fallback_findings,
    )

    old = AbiSnapshot(library="l", version="1")
    new = AbiSnapshot(
        library="l", version="2", scope_fallback=code, from_headers=from_headers
    )
    findings = scope_fallback_findings(old, new)
    assert [f.side for f in findings] == ["new"]
    assert phrase in findings[0].reason
    assert code in findings[0].reason


# --- review follow-ups: every decoded disagreement vetoes a binding --------


def _member(name, params, *, const=False, spelling=itanium, scope=("C",)):
    d = _decl(scope, name, params, spelling=spelling)
    d.is_const = const
    if const and spelling is itanium:
        d.mangled = d.mangled.replace("_ZN", "_ZNK", 1)
    return d


@pytest.mark.parametrize(
    ("decl_const", "export", "binds"),
    [
        (False, "?run@C@@QEAAHXZ", True),
        (True, "?run@C@@QEBAHXZ", True),
        (True, "?run@C@@QEAAHXZ", False),  # header const, DLL non-const
        (False, "?run@C@@QEBAHXZ", False),  # header non-const, DLL const
    ],
)
def test_member_const_qualification_must_agree(decl_const, export, binds):
    d = _member("run", (), const=decl_const)
    bind_header_declarations_to_exports(_snap(d), [export])
    assert in_public_surface(d) is binds


@pytest.mark.parametrize(
    ("decl_const", "export", "binds"),
    [
        (False, "_ZN1C3runEv", True),
        (True, "_ZNK1C3runEv", True),
        (True, "_ZN1C3runEv", False),
    ],
)
def test_itanium_export_const_qualification_must_agree(decl_const, export, binds):
    d = _member("run", (), const=decl_const, spelling=ms_abi)
    if decl_const:
        d.mangled = "?run@C@@QEBAHXZ"
    bind_header_declarations_to_exports(_snap(d), [export])
    assert in_public_surface(d) is binds


def test_function_never_binds_a_data_export_and_back():
    fn = _decl(("ns",), "gv", ())
    var = Variable(
        name="f",
        mangled="_ZN2ns1fE",
        type="int",
        visibility=Visibility.HIDDEN,
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml"),
    )
    out = bind_header_declarations_to_exports(
        _snap(fn, var), ["?gv@ns@@3HA", "?f@ns@@YAHXZ"]
    )
    assert out.bound == ()
    assert not in_public_surface(fn) and not in_public_surface(var)
    assert set(out.ambiguous) == {"?gv@ns@@3HA", "?f@ns@@YAHXZ"}


@pytest.mark.parametrize(
    ("declared", "export", "binds"),
    [
        (("int",), "_ZN2ns1fEi", True),
        (("int",), "_ZN2ns1fEl", False),
        ((), "_ZN2ns1fEv", True),
    ],
)
def test_ms_abi_parse_checks_the_itanium_parameter_suffix(declared, export, binds):
    d = _decl(("ns",), "f", declared, spelling=ms_abi)
    bind_header_declarations_to_exports(_snap(d), [export])
    assert in_public_surface(d) is binds


def test_file_scope_static_never_binds_a_decorated_export():
    d = Function(
        name="helper",
        mangled="helper",
        return_type="int",
        visibility=Visibility.HIDDEN,
        is_static=True,
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="clang"),
    )
    out = bind_header_declarations_to_exports(_snap(d), ["_helper"], x86_32=True)
    assert out.bound == () and not in_public_surface(d)


@pytest.mark.parametrize(
    ("sym", "params"),
    [
        ("?f@@YAPEAHH@Z", ("int",)),  # int* f(int)
        ("?f@@YAPEBDN@Z", ("double",)),  # const char* f(double)
        ("?f@ns@@YA?AVFoo@@H@Z", ("int",)),  # Foo f(int)
        ("?f@@YAAEAHXZ", ()),  # int& f()
        ("?f@@YAW4E@@H@Z", ("int",)),  # enum E f(int)
    ],
)
def test_non_builtin_return_types_are_skipped(sym, params):
    assert msvc_builtin_param_types(sym) == params


def test_judged_public_declaration_does_not_count_as_bound():
    """A CPO judged public with no export binds nothing: the snapshot still
    records that no declaration was bound to an export."""
    cpo = Variable(
        name="cpo",
        mangled="_ZN2ns3cpoE",
        type="const ns::fn",
        visibility=Visibility.PUBLIC,
        **header_ast_surface_facts(
            exported=ExportMatch.ABSENT, judged_public=True, producer="castxml"
        ),
    )
    out = bind_header_declarations_to_exports(_snap(cpo), ["?other@@YAHXZ"])
    assert in_public_surface(cpo)
    assert out.direct == 0 and not out.any_declaration_bound


def test_undecided_exports_become_export_table_entries():
    """Overloads that cannot be told apart, and a cross-scheme operator,
    stay on the surface as exported entries, so their removal is seen."""
    from abicheck.extract.header_export_binding import unbound_export_entries
    from abicheck.model.pe_facts import PeExport, PeMetadata
    from abicheck.model.surface_facts import declared_in_headers

    a, b = _decl(("ns",), "g", ()), _decl(("ns",), "g", ())
    a.params = [Param(name="x", type="std::vector<int>")]
    b.params = [Param(name="x", type="std::vector<double>")]
    bound = _decl(("ns",), "f", ("int",))
    snap = _snap(a, b, bound)
    syms = [
        "?g@ns@@YAHV?$vector@H@std@@@Z",
        "?g@ns@@YAHV?$vector@N@std@@@Z",
        "??8ns@@YA_NHH@Z",
        "?f@ns@@YAHH@Z",
    ]
    snap.pe = PeMetadata(machine="AMD64", exports=[PeExport(name=s) for s in syms])
    out = bind_header_declarations_to_exports(snap, syms)
    assert out.bound == (("_ZN2ns1fEi", "?f@ns@@YAHH@Z"),)
    assert set(out.undecided) == set(syms[:3])
    entries = unbound_export_entries(out, snap, x86_32=False)
    assert sorted(e.mangled for e in entries) == sorted(syms[:3])
    for e in entries:
        assert in_public_surface(e)
        assert declared_in_headers(e).status.value == "not_collected"


def test_bound_declaration_keeps_its_header_spelling_as_graph_alias():
    from abicheck.model.graph_entity_identity import snapshot_identities

    d = _decl(("ns",), "f", ("int",))
    snap = _snap(d)
    bind_header_declarations_to_exports(snap, ["?f@ns@@YAHH@Z"])
    (ident,) = snapshot_identities(snap).functions
    assert ident.node_id.endswith("?f@ns@@YAHH@Z")
    assert any(a.endswith("_ZN2ns1fEi") for a in ident.aliases)


def _scoped_dump(tmp_path, monkeypatch, functions, exports):
    """`_try_header_scoped_dump` over a stubbed header parse of a PE."""
    import warnings

    import abicheck.dumper as dumper
    from abicheck.model.pe_facts import PeMetadata
    from abicheck.workflows.dump import header_scoped

    hdr = tmp_path / "api.h"
    hdr.write_text("int f(int);\n")

    def _parsed(*a, **k):
        snap = AbiSnapshot(
            library="l.dll",
            version="1",
            from_headers=True,
            pe=PeMetadata(machine="AMD64", exports=exports),
        )
        snap.declarations.functions = list(functions)
        return snap

    monkeypatch.setattr(dumper, "_dump_pe", _parsed)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snap, reason = header_scoped._try_header_scoped_dump(
            "pe", tmp_path / "l.dll", [hdr], [], "1", "c++"
        )
    assert snap is not None and snap.from_headers
    return snap, reason, [str(w.message) for w in caught]


def test_ordinal_only_exports_record_the_unresolved_binding(tmp_path, monkeypatch):
    from abicheck.model.pe_facts import PeExport

    snap, reason, messages = _scoped_dump(
        tmp_path,
        monkeypatch,
        [_decl((), "f", ("int",))],
        [PeExport(name="", ordinal=1)],
    )
    assert reason == snap.scope_fallback == "export-binding-unresolved"
    assert any("could be bound" in m for m in messages)


def test_partial_binding_records_its_undecided_exports(tmp_path, monkeypatch):
    from abicheck.model.pe_facts import PeExport

    a, b = _decl(("ns",), "g", ()), _decl(("ns",), "g", ())
    a.params = [Param(name="x", type="std::vector<int>")]
    b.params = [Param(name="x", type="std::vector<double>")]
    undecided = ["?g@ns@@YAHV?$vector@H@std@@@Z", "?g@ns@@YAHV?$vector@N@std@@@Z"]
    exports = [PeExport(name=s) for s in ["?f@ns@@YAHH@Z", *undecided]]
    snap, reason, messages = _scoped_dump(
        tmp_path, monkeypatch, [_decl(("ns",), "f", ("int",)), a, b], exports
    )
    assert reason == snap.scope_fallback == "export-binding-partial"
    public = {f.mangled for f in snap.declarations.functions if in_public_surface(f)}
    assert public == {"?f@ns@@YAHH@Z", *undecided}
    assert any("2 export(s)" in m for m in messages)


def test_full_binding_records_no_gap(tmp_path, monkeypatch):
    from abicheck.model.pe_facts import PeExport

    snap, reason, _ = _scoped_dump(
        tmp_path,
        monkeypatch,
        [_decl(("ns",), "f", ("int",))],
        [PeExport(name="?f@ns@@YAHH@Z")],
    )
    assert reason is None and snap.scope_fallback is None
