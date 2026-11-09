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
        # Overloads are told apart by a decoded MSVC parameter list only:
        # with Itanium exports, two same-named declarations stay unbound.
        names = [(e[0], e[1]) for e in combo]
        for d, e in zip(decls, combo, strict=True):
            unique = names.count((e[0], e[1])) == 1
            if unique or export_spelling is ms_abi:
                assert d.mangled == expected[id(d)], combo
                assert in_public_surface(d), combo
                assert binary_export_match(d) is ExportMatch.DEMANGLED
            else:
                assert d.visibility is Visibility.HIDDEN, combo
                assert expected[id(d)] in out.unbound_exports, combo


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
    """Known gap pe-overloads-with-class-typed-parameters-stay-unbound."""
    # Class-typed parameters: MSVC's encoding is not decoded here, so the
    # two overloads cannot be told apart.
    a = _decl(("ns",), "f", ())
    b = _decl(("ns",), "f", ())
    a.params = [Param(name="x", type="ns::A")]
    b.params = [Param(name="x", type="ns::B")]
    syms = ["?f@ns@@YAHVA@1@@Z", "?f@ns@@YAHVB@1@@Z"]
    out = bind_header_declarations_to_exports(_snap(a, b), syms)
    assert out.bound == ()
    assert set(out.ambiguous) == set(syms)
    assert a.visibility is b.visibility is Visibility.HIDDEN


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
        "?f@@YAHPEAH@Z",  # pointer parameter: not decoded
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
