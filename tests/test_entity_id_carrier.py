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

"""The ``entity_id`` carrier field (ADR-063 Phase 2, third slice).

Phase 2's open design question — a real carrier field (option (a)) versus
deferring every post-parse consumer to Phase 6 (option (b)) — is resolved as
option (a). This module is that decision's executable contract, in the shape
the plan's own "Tests" section specifies for the option-(a) branch:

* the field is populated for every declaration kind immediately after
  parsing, on **both** header-AST backends (the ``integration``-marked live
  tests below, which is also where every cross-backend claim here was
  verified against real ``clang -ast-dump=json``/``castxml`` output rather
  than inferred);
* a static check confirms the resolver is only ever called by a producer,
  never recomputed on an already-parsed model object;
* and — the one place this slice deliberately departs from the plan's
  option-(a) paragraph — the field does **not** round-trip through
  ``serialization.py``: it is dropped, on purpose, because a faithful
  encoding needs the ``ScopePath``-preserving storage v2 wire DTO that plan
  scopes as its own slice. ``TestCarrierIsNotPersisted`` pins that as a
  deliberate, tested property rather than an accident, so the slice that
  adds real persistence has to change a test that states today's behaviour
  outright.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import parse as parse_xml

import pytest
from test_dumper_hybrid import _snap as _hybrid_snap

from abicheck import dumper_hybrid as _hybrid
from abicheck.dumper_castxml import _CastxmlParser
from abicheck.dumper_clang import _ClangAstParser
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumType,
    Function,
    Param,
    RecordType,
    Variable,
)
from abicheck.model.identity import (
    EntityId,
    EntityKind,
    Namespace,
    Record,
    entity_id_for_enum,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_variable,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

_ABICHECK_ROOT = Path(__file__).resolve().parent.parent / "abicheck"

#: Header exercising one declaration of every kind the carrier covers, plus
#: an overload pair (the case a bare ``(scope, kind, name)`` identity
#: collides) and both ``extern "C"`` shapes.
_PROBE_HEADER = textwrap.dedent(
    """
    namespace ns {
    struct Outer { struct Inner { int x; }; };
    enum class Color { RED, GREEN };
    typedef int Alias;
    extern int gVar;
    void f(int);
    void f(double);
    }
    extern "C" void c_fn(int);
    extern "C" int c_var;
    """
)

#: Two uninstantiated function templates, two uninstantiated class-template
#: methods, and two class-template-pattern static members, each pair sharing a
#: leaf name across different namespaces. Confirmed by direct compilation
#: (`clang -Xclang -ast-dump=json`) that clang emits NO `mangledName` for any
#: of these six, which is what makes them the counterexample below.
_UNINSTANTIATED_TEMPLATES = textwrap.dedent(
    """
    namespace A { template <class T> void f(T); }
    namespace B { template <class T> void f(T); }
    namespace C { template <class T> struct S { static int v; void m(); }; }
    namespace D { template <class T> struct S { static int v; void m(); }; }
    """
)

#: Two uninstantiated function templates sharing scope, leaf name, and an
#: identical (empty) ordinary parameter list, differing only in
#: template-parameter KIND (type vs. non-type). Confirmed by direct
#: compilation that clang emits no ``mangledName`` for either, so nothing but
#: the template-parameter-kind discriminator tells them apart (Codex review,
#: PR #943).
_TEMPLATE_PARAM_KIND_COLLISION = textwrap.dedent(
    """
    namespace ns {
    template <class T> void f();
    template <int N> void f();
    }
    """
)

#: Two more legal overloads, differing only in template-parameter
#: *packness*, not kind: ``template<class T>`` vs. ``template<class... T>``.
#: The first version of ``function_template_param_kinds`` reduced both to
#: the identical ``("type",)``, missing this collision (Codex review, PR
#: #943).
_TEMPLATE_PARAM_PACKNESS_COLLISION = textwrap.dedent(
    """
    namespace ns {
    template <class T> void f();
    template <class... T> void f();
    }
    """
)

#: A pure template-parameter RENAME, the opposite hazard: ``template<class
#: T, T N>`` and ``template<class U, U N>`` are identical, yet clang's own
#: ``qualType`` for the non-type parameter spells the dependent type
#: literally as the type parameter's own name (``"T"``/``"U"``) (Codex
#: review, PR #943).
_TEMPLATE_PARAM_DEPENDENT_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <class T, T N> void f();
    }
    """
)
_TEMPLATE_PARAM_DEPENDENT_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <class U, U N> void f();
    }
    """
)

#: Two more legal overloads, differing in a template-TEMPLATE parameter's
#: own NESTED parameter list: ``template<template<class> class TT>`` vs.
#: ``template<template<class, class> class TT>``. The first, non-recursive
#: version of this discriminator reduced both to the bare ``"template"``
#: tag (Codex review, PR #943).
_TEMPLATE_TEMPLATE_PARAM_NESTED_ARITY_COLLISION = textwrap.dedent(
    """
    namespace ns {
    template <template<class> class TT> void f();
    template <template<class, class> class TT> void f();
    }
    """
)

#: A pure RENAME of a template-TEMPLATE parameter -- the ``TT``/``UU``
#: sibling of ``_TEMPLATE_PARAM_DEPENDENT_RENAME_A``/``B`` above: clang's
#: ``qualType`` for ``N`` spells the dependent type literally as ``TT``'s
#: own name (Codex review, PR #943).
_TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <template<class> class TT, TT<int>* N> void f();
    }
    """
)
_TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <template<class> class UU, UU<int>* N> void f();
    }
    """
)

#: A pure template-parameter RENAME affecting an ORDINARY parameter, not
#: a non-type template parameter's own declared type: ``template<class
#: T> void f(T);`` and the same declaration with ``T`` renamed to ``U``
#: are the identical declaration, but clang's own ordinary-parameter
#: spelling names the template parameter literally (``"T"``/``"U"``) --
#: confirmed by direct compilation (Codex review, PR #943).
_TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <class T> void f(T);
    }
    """
)
_TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <class U> void f(U);
    }
    """
)

#: A pure RENAME of a non-type parameter referenced by a LATER non-type
#: parameter's own dependent type (``decltype(N)``) -- confirmed by
#: direct compilation (Codex review, PR #943).
_TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <int N, decltype(N) K> void f();
    }
    """
)
_TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <int M, decltype(M) K> void f();
    }
    """
)

#: A rename of an unused parameter named ``type`` -- a legal identifier
#: that collides with the generated ``"type-param-N"`` marker prefix. A
#: naive sequential substitution pass rewrites a PRIOR parameter's own
#: marker, corrupting an unrelated discriminator (Codex review, PR #943).
_TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_A = textwrap.dedent(
    """
    namespace ns {
    template <class T, class type, T x> void f();
    }
    """
)
_TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_B = textwrap.dedent(
    """
    namespace ns {
    template <class T, class U, T x> void f();
    }
    """
)

#: The two halves of the collision this phase exists to close: a record
#: nested in a **record** and the same bare names nested in a **namespace**.
#: Both render to the identical ``"B::C"`` qualified name, which is exactly
#: why a ``qualified_name``-keyed identity cannot tell them apart.
_NESTED_IN_RECORD = "struct B { struct C { int x; }; };\n"
_NESTED_IN_NAMESPACE = "namespace B { struct C { int x; }; }\n"


def _clang_parser(header_text: str, tmp_path: Path, name: str) -> _ClangAstParser:
    header = tmp_path / f"{name}.hpp"
    header.write_text(header_text)
    out = subprocess.run(
        [
            "clang",
            "-x",
            "c++",
            "-std=c++17",
            # Pinned rather than left at the runner's own default target --
            # confirmed by direct compilation that clang's own `mangledName`
            # differs by host: `--target=x86_64-apple-darwin` (a macOS CI
            # runner's implicit default) mangles a namespaced variable as
            # `__ZN2ns4gVarE` (Mach-O's extra leading underscore baked
            # directly into the AST-dump JSON, not something this module's
            # own Mach-O normalization ever sees), while
            # `x86_64-unknown-linux-gnu` gives the plain `_ZN2ns4gVarE`
            # every assertion in this file is written against. This module
            # tests entity-identity logic, not host-linker-convention
            # accidents, so every live-clang probe here compiles for one
            # fixed target regardless of which OS runs the test.
            "--target=x86_64-unknown-linux-gnu",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return _ClangAstParser(json.loads(out.stdout), {"c_fn", "c_var"}, set())


def _castxml_parser(header_text: str, tmp_path: Path, name: str) -> _CastxmlParser:
    header = tmp_path / f"{name}.hpp"
    header.write_text(header_text)
    xml_out = tmp_path / f"{name}.xml"
    subprocess.run(
        [
            "castxml",
            "--castxml-output=1",
            "-std=c++17",
            "-x",
            "c++",
            str(header),
            "-o",
            str(xml_out),
        ],
        check=True,
        capture_output=True,
    )
    return _CastxmlParser(
        parse_xml(xml_out).getroot(),
        exported_dynamic={"c_fn", "c_var"},
        exported_static=set(),
    )


def _one(items: list[Any], **match: Any) -> Any:
    """The single element of *items* whose attributes match, or fail loudly."""
    found = [i for i in items if all(getattr(i, k) == v for k, v in match.items())]
    assert len(found) == 1, f"expected exactly one {match}, got {len(found)}"
    return found[0]


class TestCarrierFieldShape:
    """The field's own dataclass contract, independent of any producer."""

    def test_defaults_to_none_for_a_direct_caller(self) -> None:
        # RecordType/EnumType/Function/Variable are public API dataclasses.
        # An external caller constructing one directly must get an honest
        # "nobody supplied an identity", never a fabricated one.
        assert RecordType(name="A", kind="struct").entity_id is None
        assert EnumType(name="E").entity_id is None
        assert Function(name="f", mangled="_Z1fv", return_type="void").entity_id is None
        assert Variable(name="v", mangled="v", type="int").entity_id is None

    @pytest.mark.parametrize(
        ("build", "identity"),
        [
            (
                lambda eid: RecordType(name="A", kind="struct", entity_id=eid),
                entity_id_for_type((Namespace("ns"),), "A"),
            ),
            (
                lambda eid: EnumType(name="E", entity_id=eid),
                entity_id_for_enum((Namespace("ns"),), "E"),
            ),
            (
                lambda eid: Function(
                    name="f", mangled="_Z1fv", return_type="void", entity_id=eid
                ),
                entity_id_for_function((), "f", mangled_name="_Z1fv"),
            ),
            (
                lambda eid: Variable(name="v", mangled="v", type="int", entity_id=eid),
                entity_id_for_variable((), "v", is_extern_c=True),
            ),
        ],
    )
    def test_identity_is_not_part_of_equality(
        self, build: Any, identity: EntityId
    ) -> None:
        # `entity_id` is *derived* from the declaration, so folding it into
        # __eq__ would make two otherwise-identical model objects compare
        # unequal purely on whether a producer happened to wire it -- the
        # same identity-vs-payload split `identity.Record.access` applies
        # one level down.
        assert build(identity) == build(None)

    @pytest.mark.parametrize(
        "cls", [RecordType, EnumType, Function, Variable]
    )
    def test_keyword_only_so_no_positional_slot_moves(self, cls: Any) -> None:
        # A public, non-keyword-only dataclass cannot take a positional
        # insertion without silently rebinding an existing caller's
        # arguments (the same reasoning `Function.hidden_friend_owner`
        # already records). Asserting the field's own `kw_only` flag pins
        # that structurally, for every carrier at once, rather than probing
        # one hand-counted argument list that a later field addition would
        # quietly invalidate.
        carrier = next(f for f in dataclasses.fields(cls) if f.name == "entity_id")
        assert carrier.kw_only is True
        assert carrier.compare is False
        assert carrier.default is None


def _snapshot_with_every_kind() -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1.0",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="void",
                entity_id=entity_id_for_function((), "f", mangled_name="_Z1fv"),
            )
        ],
        variables=[
            Variable(
                name="v",
                mangled="_ZN2ns1vE",
                type="int",
                entity_id=entity_id_for_variable((), "v", mangled_name="_ZN2ns1vE"),
            )
        ],
        types=[
            RecordType(
                name="A",
                kind="struct",
                entity_id=entity_id_for_type((Namespace("ns"), Record("Outer")), "A"),
            )
        ],
        enums=[
            EnumType(
                name="E", entity_id=entity_id_for_enum((Namespace("ns"),), "E")
            )
        ],
    )


class TestCarrierIsNotPersisted:
    """The deliberate limitation of this slice, stated as an executable fact.

    Encoding an ``EntityId`` faithfully means preserving ``ScopePath``'s
    *typed segments*; flattening them to a string is a lossy, one-way
    projection (a record nested in a record and the same names nested in a
    namespace render identically), so the plan specifies a versioned wire
    DTO on ``storage/entity_ids.py`` instead — its own reviewable slice.
    Until then the field is runtime-only, and no consumer may read it.
    """

    def test_no_entity_id_key_survives_serialization(self) -> None:
        d = snapshot_to_dict(_snapshot_with_every_kind())
        for list_key in ("types", "enums", "functions", "variables"):
            assert d[list_key], f"fixture must exercise {list_key}"
            for decl in d[list_key]:
                assert "entity_id" not in decl

    def test_snapshot_still_json_serializable(self) -> None:
        # The real regression this guards: `EntityId.kind` is a plain Enum
        # (not a (str, Enum)), and `scope` is a tuple of dataclasses, so an
        # asdict()-ed carrier reaching json.dumps raises TypeError outright.
        text = json.dumps(snapshot_to_dict(_snapshot_with_every_kind()))
        assert "entity_id" not in text

    def test_reload_yields_none_not_a_reconstructed_identity(self) -> None:
        reloaded = snapshot_from_dict(snapshot_to_dict(_snapshot_with_every_kind()))
        assert reloaded.functions[0].entity_id is None
        assert reloaded.variables[0].entity_id is None
        assert reloaded.types[0].entity_id is None
        assert reloaded.enums[0].entity_id is None

    def test_schema_version_did_not_move(self) -> None:
        # Nothing about the wire format changed, so a snapshot written by
        # this build must stay readable by the previous one.
        from abicheck.serialization import SCHEMA_VERSION

        assert SCHEMA_VERSION == 27


#: Modules allowed to *call* an ``entity_id_for_*`` constructor: the two
#: header-AST producers and their ``extract`` entity modules. By option
#: (a)'s own design the identity is computed once, at parse time, and read
#: thereafter — never recomputed from an already-parsed model object, which
#: is structurally incapable of reproducing a typed ``ScopePath`` anyway.
_ALLOWED_RESOLVER_CALLERS = (
    "dumper_clang.py",
    "dumper_castxml.py",
    "extract/headers/",
)

_RESOLVER_NAMES = frozenset(
    {
        "entity_id_for_type",
        "entity_id_for_enum",
        "entity_id_for_typedef",
        "entity_id_for_constant",
        "entity_id_for_variable",
        "entity_id_for_function",
    }
)


def _resolver_call_sites() -> list[str]:
    """Every ``abicheck/`` file calling an ``entity_id_for_*`` constructor.

    A real AST scan (a ``Call`` whose callee resolves to one of the names),
    not a textual match — so a docstring or a comment naming a constructor
    is not mistaken for a call, and an attribute-style call
    (``identity.entity_id_for_type(...)``) is caught as readily as a bare
    one.
    """
    sites: list[str] = []
    for path in sorted(_ABICHECK_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if called in _RESOLVER_NAMES:
                sites.append(path.relative_to(_ABICHECK_ROOT.parent).as_posix())
    return sites


class TestResolverIsOnlyCalledByAProducer:
    def test_no_post_parse_module_recomputes_an_identity(self) -> None:
        offenders = sorted(
            {
                site
                for site in _resolver_call_sites()
                if not any(allowed in site for allowed in _ALLOWED_RESOLVER_CALLERS)
                # model/identity.py defines them; its own siblings may not.
                and not site.endswith("model/identity.py")
            }
        )
        assert offenders == [], (
            "entity_id_for_* must only be called by a header-AST producer, "
            "which is the only place a typed ScopePath exists; "
            f"unexpected call sites: {offenders}"
        )

    def test_the_scan_actually_finds_the_real_producers(self) -> None:
        # A guard on the guard: an assertion that only ever sees an empty
        # list would pass just as happily against a broken scanner.
        sites = set(_resolver_call_sites())
        assert "abicheck/dumper_clang.py" in sites
        assert "abicheck/dumper_castxml.py" in sites
        assert "abicheck/extract/headers/clang/records.py" in sites
        assert "abicheck/extract/headers/castxml/records.py" in sites


def _assert_probe_identities(parser: Any) -> None:
    """Every declaration kind carries a real, correctly-shaped ``EntityId``.

    Shared by both live-backend tests: the two producers must agree, not
    merely each be self-consistent, which is the whole point of resolving
    identity through one primitive.
    """
    outer = _one(parser.parse_types(), name="Outer", qualified_name="ns::Outer")
    inner = _one(parser.parse_types(), name="Inner", qualified_name="ns::Outer::Inner")
    assert outer.entity_id == EntityId(
        scope=(Namespace("ns"),), kind=EntityKind.TYPE, leaf_name="Outer"
    )
    # The nesting segment is a Record, not a Namespace -- the distinction a
    # flattened "ns::Outer::Inner" spelling cannot carry.
    assert inner.entity_id == EntityId(
        scope=(Namespace("ns"), Record("Outer")),
        kind=EntityKind.TYPE,
        leaf_name="Inner",
    )

    color = _one(parser.parse_enums(), name="Color")
    assert color.entity_id == EntityId(
        scope=(Namespace("ns"),), kind=EntityKind.ENUM, leaf_name="Color"
    )

    # An overload pair: the exact case a bare (scope, kind, leaf_name)
    # identity collapses into one id.
    overloads = [f for f in parser.parse_functions() if f.name == "f"]
    assert len(overloads) == 2
    assert overloads[0].entity_id != overloads[1].entity_id
    for func in overloads:
        assert func.entity_id is not None
        assert func.entity_id.extra[0] == "mangled"

    # extern "C": routed through the signature-free branch on both
    # backends, since neither producer's raw spelling is a real mangling.
    c_fn = _one(parser.parse_functions(), name="c_fn")
    assert c_fn.entity_id == EntityId(
        scope=(), kind=EntityKind.FUNCTION, leaf_name="c_fn", extra=("extern_c",)
    )
    c_var = _one(parser.parse_variables(), name="c_var")
    assert c_var.entity_id == EntityId(
        scope=(), kind=EntityKind.VARIABLE, leaf_name="c_var", extra=("extern_c",)
    )

    g_var = _one(parser.parse_variables(), name="gVar")
    assert g_var.entity_id is not None
    assert g_var.entity_id.extra == ("mangled", "_ZN2ns4gVarE")


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_populates_every_kind(tmp_path: Path) -> None:
    _assert_probe_identities(_clang_parser(_PROBE_HEADER, tmp_path, "probe"))


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_populates_every_kind(tmp_path: Path) -> None:
    _assert_probe_identities(_castxml_parser(_PROBE_HEADER, tmp_path, "probe"))


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_honors_static_export_evidence_for_c_linkage(
    tmp_path: Path,
) -> None:
    """A C API observed only via a STATIC archive's export set must get
    the same extern-"C" override a dynamically-linked one gets.

    castxml's language-mode detection for an ambiguous header defaults to
    C++ (the "case141" class of issue), so a plain C-compiled
    ``int foo(int);`` gets a bogus pseudo-Itanium ``mangled="_Z3fooi"``
    attribute -- confirmed by direct compilation. The recovery override
    checked only ``exported_dynamic``, so a symbol observed only through a
    static archive's export set (``exported_static``) left that bogus guess
    standing (Codex review, PR #943)."""
    header = tmp_path / "static_c.h"
    header.write_text("int foo(int x);\n")
    xml_out = tmp_path / "static_c.xml"
    subprocess.run(
        [
            "castxml",
            "--castxml-output=1",
            "-std=c++17",
            "-x",
            "c++",
            str(header),
            "-o",
            str(xml_out),
        ],
        check=True,
        capture_output=True,
    )
    parser = _CastxmlParser(
        parse_xml(xml_out).getroot(),
        exported_dynamic=set(),
        exported_static={"foo"},
    )
    foo = _one(parser.parse_functions(), name="foo")
    assert foo.mangled == "foo"
    assert foo.entity_id is not None and foo.entity_id.extra == ("extern_c",)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_closes_the_record_vs_namespace_collision(tmp_path: Path) -> None:
    """``B::C`` nested in a record and in a namespace are two identities.

    This is the collision `_find_opaque_types`'s bare-``RecordType.name``
    keying (and every other flat-name key) cannot see: both declarations
    render to the identical ``qualified_name``, so any string-keyed index
    merges them. The two headers are parsed separately on purpose — that is
    the real shape of the bug, one spelling on each side of a comparison.
    """
    in_record = _one(
        _clang_parser(_NESTED_IN_RECORD, tmp_path, "rec").parse_types(),
        name="C",
    )
    in_namespace = _one(
        _clang_parser(_NESTED_IN_NAMESPACE, tmp_path, "ns").parse_types(),
        name="C",
    )
    assert in_record.qualified_name == in_namespace.qualified_name == "B::C"
    assert in_record.entity_id != in_namespace.entity_id
    assert in_record.entity_id == EntityId(
        scope=(Record("B"),), kind=EntityKind.TYPE, leaf_name="C"
    )
    assert in_namespace.entity_id == EntityId(
        scope=(Namespace("B"),), kind=EntityKind.TYPE, leaf_name="C"
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_closes_the_record_vs_namespace_collision(tmp_path: Path) -> None:
    """The castxml half of the same collision — see the clang sibling."""
    in_record = _one(
        _castxml_parser(_NESTED_IN_RECORD, tmp_path, "rec").parse_types(), name="C"
    )
    in_namespace = _one(
        _castxml_parser(_NESTED_IN_NAMESPACE, tmp_path, "ns").parse_types(), name="C"
    )
    assert in_record.qualified_name == in_namespace.qualified_name == "B::C"
    assert in_record.entity_id != in_namespace.entity_id
    assert in_record.entity_id == EntityId(
        scope=(Record("B"),), kind=EntityKind.TYPE, leaf_name="C"
    )
    assert in_namespace.entity_id == EntityId(
        scope=(Namespace("B"),), kind=EntityKind.TYPE, leaf_name="C"
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_missing_mangling_is_not_read_as_c_linkage(tmp_path: Path) -> None:
    """A mangling-free C++ template must not take the ``extern "C"`` branch.

    Clang emits no ``mangledName`` for an uninstantiated template, so
    ``mangled`` falls back to the bare ``name`` -- reading True for the
    ``mangled == name`` C-linkage heuristic, forcing ``scope=()`` and
    collapsing ``A::f``/``B::f`` onto one ``EntityId`` (Codex + CodeRabbit
    review, PR #943). Exercises all three unmangled shapes as pairs."""
    parser = _clang_parser(_UNINSTANTIATED_TEMPLATES, tmp_path, "tmpl")

    for leaf in ("f", "m"):
        pair = [fn for fn in parser.parse_functions() if fn.name == leaf]
        assert len(pair) == 2, f"expected two {leaf!r} declarations"
        assert all(fn.entity_id is not None for fn in pair)
        # Neither may claim C linkage, and neither may lose its scope.
        for fn in pair:
            assert fn.entity_id is not None
            assert fn.entity_id.extra[0] == "sig", fn.entity_id
            assert fn.entity_id.scope != (), fn.entity_id
        assert pair[0].entity_id != pair[1].entity_id

    statics = [var for var in parser.parse_variables() if var.name == "v"]
    assert len(statics) == 2
    for var in statics:
        assert var.entity_id is not None
        assert var.entity_id.extra == (), var.entity_id
        assert var.entity_id.scope != (), var.entity_id
    assert statics[0].entity_id != statics[1].entity_id


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_real_c_linkage_still_takes_the_extern_c_branch(
    tmp_path: Path,
) -> None:
    """The negative control for the fix above, which narrowed a heuristic:
    a genuine C-linkage declaration really does carry an explicit
    ``mangledName`` equal to its name (verified via ``clang -x c``/``-x
    c++ extern "C"``), pinned so a future narrowing cannot silently break
    extern-"C" identity too."""
    parser = _clang_parser(_PROBE_HEADER, tmp_path, "probe")
    c_fn = _one(parser.parse_functions(), name="c_fn")
    c_var = _one(parser.parse_variables(), name="c_var")
    assert c_fn.entity_id is not None and c_fn.entity_id.extra == ("extern_c",)
    assert c_var.entity_id is not None and c_var.entity_id.extra == ("extern_c",)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_nested_cpp_linkage_inside_extern_c_is_not_extern_c(
    tmp_path: Path,
) -> None:
    """``extern "C++"`` nested inside ``extern "C"`` must reset linkage
    back to genuine C++ -- linkage specs don't stack, the innermost wins.
    Clang genuinely mangles ``cppfun`` (``_Z6cppfunv``), but the previous
    sticky (OR-only) propagation never reset for the inner block,
    collapsing it onto the bare ``("extern_c",)`` id (Codex review, PR
    #943)."""
    parser = _clang_parser(
        'extern "C" { extern "C++" { void cppfun(); } }', tmp_path, "nestedlinkage"
    )
    cppfun = _one(parser.parse_functions(), name="cppfun")
    assert cppfun.mangled == "_Z6cppfunv"
    assert cppfun.is_extern_c is False
    assert cppfun.entity_id is not None
    assert cppfun.entity_id.extra == ("mangled", "_Z6cppfunv")


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_hidden_friend_template_resolves_in_namespace_scope(
    tmp_path: Path,
) -> None:
    """A hidden friend template is a member of the enclosing namespace, not
    the befriending class ([namespace.memdef]): clang rejects two such
    friends with identical signatures in different classes as a
    *redefinition*, proof they're one entity, but the lexical walk kept
    each ``EntityId`` under its own class's ``Record`` scope (Codex
    review, PR #943); ``hidden_friend_owner`` still names the class."""
    parser = _clang_parser(
        "struct A { template<class T> friend void f(T); };"
        " struct B { template<class U> friend void f(U); };",
        tmp_path,
        "hiddenfriend",
    )
    funcs = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(funcs) == 2
    a_fn = next(fn for fn in funcs if fn.hidden_friend_owner == "A")
    b_fn = next(fn for fn in funcs if fn.hidden_friend_owner == "B")
    assert a_fn.is_hidden_friend is True
    assert b_fn.is_hidden_friend is True
    assert a_fn.entity_id is not None and b_fn.entity_id is not None
    assert a_fn.entity_id.scope == ()
    assert b_fn.entity_id.scope == ()
    assert a_fn.entity_id == b_fn.entity_id


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_kind_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """``template<class T> void f()`` vs ``template<int N> void f()``
    share scope/leaf name/params, and neither is mangled, so the ``sig``
    fallback had nothing to distinguish them by -- `distinct: 1` of 2
    before this fix (Codex review, PR #943)."""
    parser = _clang_parser(_TEMPLATE_PARAM_KIND_COLLISION, tmp_path, "tmplkind")
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2 and all(fn.entity_id is not None for fn in pair)
    for fn in pair:
        assert fn.entity_id is not None  # narrows for mypy
        assert fn.entity_id.extra[0] == "sig" and fn.entity_id.extra[-2] == "tmpl"
    assert pair[0].entity_id != pair[1].entity_id
    kinds = {fn.entity_id.extra[-1] for fn in pair if fn.entity_id is not None}
    assert kinds == {"type", "nontype:int"}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_packness_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """``template<class T> void f()`` vs ``template<class... T> void f()``
    are legal overloads sharing every other discriminator; the kind-only
    fix above still reduced both to `("type",)` -- `distinct: 1` of 2
    before this fix (Codex review, PR #943)."""
    parser = _clang_parser(_TEMPLATE_PARAM_PACKNESS_COLLISION, tmp_path, "tmplpack")
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2 and all(fn.entity_id is not None for fn in pair)
    for fn in pair:
        assert fn.entity_id is not None  # narrows for mypy
        assert fn.entity_id.extra[0] == "sig" and fn.entity_id.extra[-2] == "tmpl"
    assert pair[0].entity_id != pair[1].entity_id
    kinds = {fn.entity_id.extra[-1] for fn in pair if fn.entity_id is not None}
    assert kinds == {"type", "type..."}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A pure template-parameter RENAME must NOT change the ``EntityId``.
    ``template<class T, T N> void f();``/``template<class U, U N> void
    f();`` are identical, but clang's ``qualType`` for ``N`` spells the
    dependent type literally as the type parameter's own name
    (``"T"``/``"U"``) -- unequal ``EntityId``s before this fix, which would
    fingerprint a non-semantic rename as a remove+add (Codex review, PR
    #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_PARAM_DEPENDENT_RENAME_A, tmp_path, "depa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_PARAM_DEPENDENT_RENAME_B, tmp_path, "depb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_template_param_nested_arity_discriminates(
    tmp_path: Path,
) -> None:
    """``template<template<class> class TT>`` vs ``template<template<class,
    class> class TT>`` share every other discriminator; the earlier,
    non-recursive version reduced both to the bare ``"template"`` tag --
    `distinct: 1` of 2 before this fix (Codex review, PR #943)."""
    parser = _clang_parser(
        _TEMPLATE_TEMPLATE_PARAM_NESTED_ARITY_COLLISION, tmp_path, "tmpltt"
    )
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2
    assert all(fn.entity_id is not None for fn in pair)
    for fn in pair:
        assert fn.entity_id is not None
        assert fn.entity_id.extra[0] == "sig", fn.entity_id
        assert fn.entity_id.extra[-2] == "tmpl", fn.entity_id
    assert pair[0].entity_id != pair[1].entity_id
    kinds = {fn.entity_id.extra[-1] for fn in pair if fn.entity_id is not None}
    assert kinds == {"template(type)", "template(type,type)"}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_template_param_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A pure RENAME of a template-TEMPLATE parameter must NOT change the
    ``EntityId``. ``template<template<class> class TT, TT<int>* N> void
    f();`` renamed ``TT`` to ``UU`` is identical, but clang's ``qualType``
    for ``N`` spells the dependent type literally (``"TT<int> *"``/
    ``"UU<int> *"``) -- unequal ``EntityId``s before this fix (Codex
    review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_A, tmp_path, "ttdepa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_B, tmp_path, "ttdepb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0<int> *"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_rename_in_ordinary_param_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A pure template-parameter RENAME must NOT change identity when it
    affects an ORDINARY parameter, not a non-type parameter's own type.
    ``template<class T> void f(T);``/``template<class U> void f(U);`` are
    identical, but clang's ordinary-parameter spelling names the template
    parameter literally (``"T"``/``"U"``) -- unequal ``EntityId``s before
    this fix (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_A, tmp_path, "ordpa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_B, tmp_path, "ordpb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[1] == "type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_nontype_param_dependent_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A rename of a non-type parameter referenced by a LATER non-type
    parameter's own dependent type must NOT change identity --
    ``decltype(N)`` spells ``N``'s name literally, so a rename to ``M``
    changes it too unless canonicalized (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_A, tmp_path, "ntdepa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_B, tmp_path, "ntdepb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:decltype(type-param-0)"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_rename_of_param_named_type_does_not_corrupt_a_prior_marker(
    tmp_path: Path,
) -> None:
    """Renaming an unused parameter named ``type`` must NOT corrupt an
    unrelated parameter's marker -- a naive sequential substitution pass
    rewrote a PRIOR parameter's own generated token (Codex review, PR
    #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_A, tmp_path, "gena"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_B, tmp_path, "genb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_dependent_return_type_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """Two templates differing ONLY in a dependent return type are legal,
    coexisting overloads (clang accepts both with no redefinition error),
    but shared scope/leaf/params/kinds collided them (Codex review, PR
    #943); a rename reflected only in the return type must still match."""
    header = (
        "struct A { using x = int; using y = double; };"
        " template<class T> typename T::x f(T);"
        " template<class T> typename T::y f(T);"
    )
    pair = [
        fn
        for fn in _clang_parser(header, tmp_path, "rettmpl").parse_functions()
        if fn.name == "f"
    ]
    assert len(pair) == 2 and pair[0].entity_id != pair[1].entity_id

    a = _one(
        _clang_parser(
            "struct A { using x = int; }; template<class T> typename T::x f(T);",
            tmp_path,
            "retrena",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct A { using x = int; }; template<class U> typename U::x f(U);",
            tmp_path,
            "retrenb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id


# ── hybrid dumper: entity_id must stay in sync across post-parse rewrites ────
#
# `dumper_hybrid.py` rewrites a declaration's `mangled` field in two places
# AFTER it was already parsed with a real `entity_id`: reconciling a castxml
# ctor/dtor synthetic placeholder key to clang's real mangling, and
# normalizing a Mach-O linker symbol's leading underscore. Both tests live
# here, not in `test_dumper_hybrid.py`, since they're about this module's own
# identity-carrier contract (ADR-063 Phase 2), not the hybrid merge itself.


def test_reconciled_constructor_adopts_clangs_entity_id() -> None:
    """The identity carrier must be rewritten alongside ``mangled``, not
    left holding the stale synthetic placeholder key (Codex review, fresh
    evidence).

    Before this fix, ``dataclasses.replace(f, mangled=match.mangled)``
    rewrote only the ``mangled`` field: the reconciled function's own
    ``mangled`` correctly showed the real clang symbol, but its
    ``entity_id`` still carried the synthetic, no-such-symbol-exists
    placeholder inside its own "mangled" tag -- a caller keying on
    ``entity_id`` (rather than ``mangled``) would fragment this one real
    declaration into two identities across a comparison.
    """
    synthetic = f"{_hybrid.SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(int)"
    castxml_ctor = Function(
        name="Widget",
        mangled=synthetic,
        return_type="void",
        params=[Param(name="n", type="int")],
        access=AccessLevel.PUBLIC,
        entity_id=entity_id_for_function((), "Widget", mangled_name=synthetic),
    )
    real_mangled = "_ZN2ns6WidgetC1Ei"
    real_entity_id = entity_id_for_function((), "Widget", mangled_name=real_mangled)
    clang_ctor = Function(
        name="Widget",
        mangled=real_mangled,
        return_type="void",
        params=[Param(name="n", type="int")],
        access=AccessLevel.PUBLIC,
        entity_id=real_entity_id,
    )
    castxml = _hybrid_snap(functions=[castxml_ctor], ast_producer="castxml")
    clang = _hybrid_snap(functions=[clang_ctor], ast_producer="clang")
    merged = _hybrid.merge_snapshots(castxml, clang)

    reconciled = merged.func_by_mangled(real_mangled)
    assert reconciled is not None
    assert reconciled.entity_id == real_entity_id
    assert reconciled.entity_id.extra == ("mangled", real_mangled)


def test_macho_normalization_resynchronizes_entity_id_mangled_tag() -> None:
    """The Mach-O underscore-strip must re-spell the identity carrier's own
    "mangled" tag alongside the ``mangled`` field it strips it from (Codex
    review, fresh evidence) -- otherwise a clang-side declaration's
    ``entity_id`` still names the prefixed Darwin linker symbol after
    ``mangled`` itself has been normalized to castxml's prefix-free
    spelling, desynchronizing the two.

    A **clang-only** variable (no matching castxml declaration) is the real
    path this matters for: castxml's own already-correct identity always
    wins on a matched pair, so a normalization bug here is invisible unless
    the clang-only declaration itself -- with no castxml counterpart to
    shadow it -- passes straight through into the merged snapshot.
    """
    prefixed = "__ZN2ns1gE"
    stripped = "_ZN2ns1gE"
    clang_v = Variable(
        name="g",
        mangled=prefixed,
        type="int",
        entity_id=entity_id_for_variable((), "g", mangled_name=prefixed),
    )
    castxml = _hybrid_snap(variables=[], ast_producer="castxml", platform="macho")
    clang = _hybrid_snap(variables=[clang_v], ast_producer="clang", platform="macho")
    merged = _hybrid.merge_snapshots(castxml, clang)

    reconciled = merged.var_by_mangled(stripped)
    assert reconciled is not None
    assert reconciled.entity_id is not None
    assert reconciled.entity_id.extra == ("mangled", stripped)


def _mangled_rewrite_sites() -> list[tuple[str, int, bool]]:
    """Every post-parse rewrite of a declaration's ``mangled`` field.

    Returns ``(path, line, keeps_carrier_in_sync)`` for each
    ``dataclasses.replace(..., mangled=...)`` call anywhere under
    ``abicheck/`` — a real AST scan for the keyword, not a textual match,
    so a ``mangled`` mentioned in a docstring or a differently-named
    keyword is not counted.
    """
    sites: list[tuple[str, int, bool]] = []
    for path in sorted(_ABICHECK_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if called != "replace":
                continue
            keywords = {kw.arg for kw in node.keywords}
            if "mangled" not in keywords:
                continue
            sites.append(
                (
                    path.relative_to(_ABICHECK_ROOT.parent).as_posix(),
                    node.lineno,
                    "entity_id" in keywords,
                )
            )
    return sites


class TestMangledRewritesKeepTheCarrierInSync:
    """A rewrite of ``mangled`` must rewrite ``entity_id`` alongside it.

    A function's/variable's ``EntityId`` is *derived* from its mangled
    spelling (``extra=("mangled", ...)``), so any code that re-spells
    ``mangled`` after a producer already resolved the identity leaves the
    carrier pointing at a name that may no longer exist. That is not
    hypothetical: ``dumper_hybrid.py`` does exactly this in two places —
    reconciling a castxml synthetic ctor/dtor placeholder key to clang's
    real mangling, and normalizing a Mach-O linker symbol's leading
    underscore — and both silently desynced the carrier until review caught
    it (Codex, PR #943).

    This is the third distinct way an "inert, purely additive" field turned
    out not to be inert (the other two: a generic object-graph walk that
    crashed on the frozen carrier, and an over-broad ``extern "C"``
    predicate feeding the resolver). Three one-off fixes do not close a
    class, so the invariant is stated mechanically here: the audit that
    found all three sites is only true on the day it was run, and a fourth
    rewrite added later would desync in silence exactly like the first
    three did.
    """

    def test_every_mangled_rewrite_also_rewrites_the_carrier(self) -> None:
        desynced = [
            f"{path}:{line}"
            for path, line, in_sync in _mangled_rewrite_sites()
            if not in_sync
        ]
        assert desynced == [], (
            "these sites re-spell `mangled` without updating `entity_id`, "
            "leaving the identity carrier pointing at a stale spelling; pass "
            "`entity_id=model.identity.with_mangled_name(<old>, <new>)` (or "
            "the matching declaration's own already-resolved entity_id) "
            f"alongside it: {desynced}"
        )

    def test_the_scan_actually_finds_the_known_rewrite_sites(self) -> None:
        # A guard on the guard: an assertion that only ever sees an empty
        # list passes just as happily against a scanner that matches
        # nothing. These are the real rewrites the audit found.
        found = {path for path, _line, _sync in _mangled_rewrite_sites()}
        assert "abicheck/dumper_hybrid.py" in found, found
        assert len(_mangled_rewrite_sites()) >= 3, _mangled_rewrite_sites()
