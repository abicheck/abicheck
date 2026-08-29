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

from abicheck.dumper_castxml import _CastxmlParser
from abicheck.dumper_clang import _ClangAstParser
from abicheck.model import AbiSnapshot, EnumType, Function, RecordType, Variable
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
