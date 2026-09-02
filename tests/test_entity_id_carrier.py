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

"""The ``entity_id`` carrier field (ADR-063 Phase 2, third and fifth slices).

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
* and — as of the fifth slice (schema v28) — the field DOES round-trip
  through ``serialization.py``, via ``storage/entity_id_codec.py``'s bridge
  onto ``storage/entity_ids.py``'s wire-schema-v2 ``domain_entity_id_to_dto``/
  ``domain_entity_id_from_dto``. The third slice's own interim state (the
  field dropped outright, pinned by a now-superseded
  ``TestCarrierIsNotPersisted``) existed only because that wire DTO did not
  exist yet; ``TestCarrierIsPersisted`` below is this slice's replacement,
  pinning the round trip as a real property instead. **No consumer may read
  this field off a snapshot yet** — persistence landing is not the same as
  the `finding_identity.py` algorithm migration or the post-parse consumer
  migrations, both still open, separate slices.
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
    entity_id_for_constant,
    entity_id_for_enum,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_typedef,
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
    constexpr int kLimit = 7;
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

#: The two halves of the collision this phase exists to close: a record
#: nested in a **record** and the same bare names nested in a **namespace**.
#: Both render to the identical ``"B::C"`` qualified name, which is exactly
#: why a ``qualified_name``-keyed identity cannot tell them apart.
_NESTED_IN_RECORD = "struct B { struct C { int x; }; };\n"
_NESTED_IN_NAMESPACE = "namespace B { struct C { int x; }; }\n"


def _clang_parser(
    header_text: str, tmp_path: Path, name: str, *, public: bool = False
) -> _ClangAstParser:
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
    return _ClangAstParser(
        json.loads(out.stdout),
        {"c_fn", "c_var"},
        set(),
        public_header_paths=[str(header)] if public else None,
    )


def _castxml_parser(
    header_text: str, tmp_path: Path, name: str, *, public: bool = False
) -> _CastxmlParser:
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
            # Pinned for the identical reason `_clang_parser` above pins
            # its own `--target` -- castxml forwards an unrecognized flag
            # straight to its internal clang compiler, so this is the same
            # flag, just reached a different way. Confirmed by a real
            # Windows CI failure: an unpinned castxml targets the runner's
            # own host by default, mangling `gVar` as MSVC's
            # `"?gVar@ns@@3HA"` instead of the Itanium `"_ZN2ns4gVarE"`
            # every assertion in this file is written against -- this
            # module tests entity-identity logic, not host-mangling-scheme
            # accidents, so every live-castxml probe here targets the same
            # fixed platform `_clang_parser` already does, regardless of
            # which OS runs the test.
            "--target=x86_64-unknown-linux-gnu",
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
        public_header_paths=[str(header)] if public else None,
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

    @pytest.mark.parametrize("cls", [RecordType, EnumType, Function, Variable])
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


class TestSidecarFieldShape:
    """``typedef_entity_ids``/``constant_entity_ids``: the ADR-063 Phase 2
    closing slice's sidecars, for the two ``dict[str, str]`` collections that
    have no declaration object to carry an ``entity_id`` on."""

    @pytest.mark.parametrize(
        "field_name", ["typedef_entity_ids", "constant_entity_ids"]
    )
    def test_defaults_to_empty_and_is_keyword_only(self, field_name: str) -> None:
        snap = AbiSnapshot(library="libx.so", version="1.0")
        assert getattr(snap, field_name) == {}
        sidecar = next(
            f for f in dataclasses.fields(AbiSnapshot) if f.name == field_name
        )
        assert sidecar.kw_only is True
        assert sidecar.default_factory is dict

    @pytest.mark.parametrize(
        ("field_name", "partner"),
        [
            ("typedef_entity_ids", "typedefs_qualified"),
            ("constant_entity_ids", "constants"),
        ],
    )
    def test_sidecar_shares_its_partner_key_space(
        self, field_name: str, partner: str
    ) -> None:
        # The whole point of the sidecar shape: a consumer joins it against
        # the dict it annotates by key, with no second key convention.
        snap = _snapshot_with_sidecars()
        assert set(getattr(snap, field_name)) == set(getattr(snap, partner))


def _snapshot_with_sidecars() -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1.0",
        from_headers=True,
        typedefs={"Alias": "int"},
        typedefs_qualified={"ns::Alias": "int"},
        constants={"ns::kLimit": "7"},
        typedef_entity_ids={
            "ns::Alias": entity_id_for_typedef((Namespace("ns"),), "Alias")
        },
        constant_entity_ids={
            "ns::kLimit": entity_id_for_constant((Namespace("ns"),), "kLimit")
        },
    )


class TestSidecarIsPersisted:
    """The sidecars round-trip losslessly (schema v31)."""

    def test_reload_reconstructs_the_identical_identities(self) -> None:
        original = _snapshot_with_sidecars()
        reloaded = snapshot_from_dict(
            json.loads(json.dumps(snapshot_to_dict(original)))
        )
        assert reloaded.typedef_entity_ids == original.typedef_entity_ids
        assert reloaded.constant_entity_ids == original.constant_entity_ids

    def test_scope_kind_survives_rather_than_a_rendered_string(self) -> None:
        # The same counterexample the declaration carrier's own round-trip
        # test pins: a "ns::Alias" spelling cannot say whether "ns" was a
        # namespace or a record, so the wire form must keep the segment type.
        snap = AbiSnapshot(
            library="libx.so",
            version="1.0",
            typedef_entity_ids={
                "ns::Alias": entity_id_for_typedef((Record("ns"),), "Alias")
            },
        )
        reloaded = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))
        assert (
            reloaded.typedef_entity_ids["ns::Alias"]
            == snap.typedef_entity_ids["ns::Alias"]
        )
        assert reloaded.typedef_entity_ids["ns::Alias"] != entity_id_for_typedef(
            (Namespace("ns"),), "Alias"
        )

    def test_pre_v31_snapshot_loads_with_empty_sidecars(self) -> None:
        d = snapshot_to_dict(_snapshot_with_sidecars())
        d["schema_version"] = 30
        d.pop("typedef_entity_ids", None)
        d.pop("constant_entity_ids", None)
        reloaded = snapshot_from_dict(json.loads(json.dumps(d)))
        assert reloaded.typedef_entity_ids == {}
        assert reloaded.constant_entity_ids == {}
        # An absent sidecar must not disturb the dicts it annotates.
        assert reloaded.typedefs_qualified == {"ns::Alias": "int"}
        assert reloaded.constants == {"ns::kLimit": "7"}

    def test_schema_version_moved_to_31(self) -> None:
        from abicheck.serialization import SCHEMA_VERSION

        assert SCHEMA_VERSION >= 31


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
            EnumType(name="E", entity_id=entity_id_for_enum((Namespace("ns"),), "E"))
        ],
    )


class TestCarrierIsPersisted:
    """The fifth slice's own contract: the carrier round-trips losslessly.

    Supersedes the third slice's ``TestCarrierIsNotPersisted`` (see this
    module's docstring) now that ``storage/entity_ids.py``'s wire-schema-v2
    ``EntityId`` bridge exists to encode ``ScopePath``'s typed segments
    without collapsing distinct scopes onto one rendered string.
    """

    def test_entity_id_key_survives_serialization(self) -> None:
        d = snapshot_to_dict(_snapshot_with_every_kind())
        for list_key in ("types", "enums", "functions", "variables"):
            assert d[list_key], f"fixture must exercise {list_key}"
            for decl in d[list_key]:
                assert "entity_id" in decl

    def test_snapshot_still_json_serializable(self) -> None:
        # The real regression this guards: `EntityId.kind` is a plain Enum
        # (not a (str, Enum)), and `scope` is a tuple of dataclasses, so a
        # naive asdict()-ed carrier reaching json.dumps raises TypeError
        # outright -- the wire-schema-v2 encoding must produce a plain,
        # JSON-safe document instead.
        text = json.dumps(snapshot_to_dict(_snapshot_with_every_kind()))
        assert '"entity_id"' in text

    def test_reload_reconstructs_the_identical_identity(self) -> None:
        original = _snapshot_with_every_kind()
        # Through a real json.dumps/json.loads round trip, not just the dict
        # form -- the property this schema bump exists to establish is that
        # a snapshot WRITTEN TO DISK AND RELOADED keeps its identities.
        reloaded = snapshot_from_dict(
            json.loads(json.dumps(snapshot_to_dict(original)))
        )
        assert reloaded.functions[0].entity_id == original.functions[0].entity_id
        assert reloaded.variables[0].entity_id == original.variables[0].entity_id
        assert reloaded.types[0].entity_id == original.types[0].entity_id
        assert reloaded.enums[0].entity_id == original.enums[0].entity_id

    def test_record_nested_in_record_survives_the_round_trip(self) -> None:
        # The exact counterexample the wire-schema-v2 Design section's own
        # finding raised: a rendered qualified_name string cannot
        # distinguish a record nested in a record from the same bare names
        # nested in a namespace (both render "ns::A"). Pinned here at the
        # whole-snapshot level, not only in the storage-layer bridge's own
        # unit tests, since this is the property a real dump/compare run
        # actually depends on.
        snap = AbiSnapshot(
            library="libx.so",
            version="1.0",
            types=[
                RecordType(
                    name="A",
                    kind="struct",
                    entity_id=entity_id_for_type((Record("ns"),), "A"),
                )
            ],
        )
        reloaded = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))
        assert reloaded.types[0].entity_id == snap.types[0].entity_id
        assert reloaded.types[0].entity_id != entity_id_for_type(
            (Namespace("ns"),), "A"
        )

    def test_declaration_with_no_resolved_identity_reloads_as_none(self) -> None:
        # A direct, non-producer construction never fabricates an identity
        # (TestCarrierFieldShape), and that honesty must survive a round
        # trip too -- no key at all, and no reconstructed guess on reload.
        snap = AbiSnapshot(
            library="libx.so",
            version="1.0",
            functions=[Function(name="f", mangled="_Z1fv", return_type="void")],
        )
        d = snapshot_to_dict(snap)
        assert "entity_id" not in d["functions"][0]
        reloaded = snapshot_from_dict(json.loads(json.dumps(d)))
        assert reloaded.functions[0].entity_id is None

    def test_schema_version_moved_to_28(self) -> None:
        from abicheck.serialization import SCHEMA_VERSION

        # >=, not ==: this pins the floor the entity_id carrier landed at,
        # not the exact current schema version -- a later, unrelated bump
        # (e.g. ADR-063 Phase 3's surface_graph field, schema v29) must not
        # fail this test.
        assert SCHEMA_VERSION >= 28

    def test_pre_v28_snapshot_loads_with_entity_id_none(self) -> None:
        # A legacy snapshot never wrote this key at all -- absence must
        # degrade to "no identity available", not raise or fabricate one.
        d = snapshot_to_dict(_snapshot_with_every_kind())
        d["schema_version"] = 27
        for list_key in ("types", "enums", "functions", "variables"):
            for decl in d[list_key]:
                decl.pop("entity_id", None)
        reloaded = snapshot_from_dict(d)
        assert reloaded.functions[0].entity_id is None
        assert reloaded.variables[0].entity_id is None
        assert reloaded.types[0].entity_id is None
        assert reloaded.enums[0].entity_id is None


class TestMalformedEntityIdDocumentIsRefused:
    """A falsy-but-present wire value (``{}``, ``[]``, ``""``, ``False``,
    ``0``) is not the same thing as an absent/``None`` carrier -- only
    ``None`` may load as "this declaration never resolved an identity";
    anything else reaches the real wire-schema-v2 reader so its own
    validation rejects the malformed document, rather than the truthiness
    check silently reading it as an honest absence (Codex review, PR #949).
    """

    @pytest.mark.parametrize("bogus_entity_id", [{}, [], "", False, 0])
    def test_falsy_entity_id_document_is_refused_not_treated_as_absent(
        self, bogus_entity_id: object
    ) -> None:
        d = snapshot_to_dict(_snapshot_with_every_kind())
        d["functions"][0]["entity_id"] = bogus_entity_id
        with pytest.raises((TypeError, ValueError)):
            snapshot_from_dict(d)


class TestMalformedSidecarEntityIdDocumentIsRefused:
    """The same bug class as :class:`TestMalformedEntityIdDocumentIsRefused`
    above, recurring on ``typedef_entity_ids``/``constant_entity_ids``
    (ADR-063 Phase 2's typedef/constant slice): a present-but-malformed
    sidecar container or key must be refused, not silently read as an
    honest absence or as a valid identity (Codex review).
    """

    @pytest.mark.parametrize(
        "sidecar_key", ["typedef_entity_ids", "constant_entity_ids"]
    )
    @pytest.mark.parametrize(
        "bogus_sidecar", [[], "", False, 0, ["not", "a", "mapping"]]
    )
    def test_non_mapping_sidecar_is_refused_not_treated_as_empty(
        self, sidecar_key: str, bogus_sidecar: object
    ) -> None:
        d = snapshot_to_dict(_snapshot_with_every_kind())
        d[sidecar_key] = bogus_sidecar
        with pytest.raises((TypeError, ValueError)):
            snapshot_from_dict(d)

    @pytest.mark.parametrize(
        "sidecar_key", ["typedef_entity_ids", "constant_entity_ids"]
    )
    def test_non_string_sidecar_key_is_refused(self, sidecar_key: str) -> None:
        # A well-formed entity-id document, so the only defect under test is
        # the non-string key -- a document-shape defect must not mask it.
        valid_document = {
            "schema_version": 2,
            "scope": [],
            "kind": "typedef",
            "leaf_name": "x",
            "extra": [],
        }
        d = snapshot_to_dict(_snapshot_with_every_kind())
        d[sidecar_key] = {1: valid_document}
        with pytest.raises((TypeError, ValueError)):
            snapshot_from_dict(d)

    @pytest.mark.parametrize(
        "sidecar_key", ["typedef_entity_ids", "constant_entity_ids"]
    )
    def test_non_string_sidecar_key_is_refused_on_encode(
        self, sidecar_key: str
    ) -> None:
        # A caller that constructs AbiSnapshot directly (not through either
        # header-AST producer) could hand the sidecar a non-string key --
        # the encode side must refuse it too, or two colliding keys (1 and
        # "1") would silently collapse onto one JSON key, losing an
        # identity rather than being rejected up front (Codex review).
        bad_entity_id = entity_id_for_typedef((), "x")
        snap = dataclasses.replace(
            _snapshot_with_every_kind(), **{sidecar_key: {1: bad_entity_id}}
        )
        with pytest.raises((TypeError, ValueError)):
            snapshot_to_dict(snap)

    @pytest.mark.parametrize(
        "sidecar_key", ["typedef_entity_ids", "constant_entity_ids"]
    )
    @pytest.mark.parametrize("bogus_sidecar", [[], "", False, 0])
    def test_non_mapping_sidecar_is_refused_on_encode(
        self, sidecar_key: str, bogus_sidecar: object
    ) -> None:
        # The mirror of test_non_mapping_sidecar_is_refused_not_treated_as_
        # empty above, but on the encode path: a caller constructing
        # AbiSnapshot directly (outside the dataclass's own type
        # annotation) with a non-mapping sidecar must not leak an
        # AttributeError from a bare .items() call (Codex review).
        snap = dataclasses.replace(
            _snapshot_with_every_kind(), **{sidecar_key: bogus_sidecar}
        )
        with pytest.raises((TypeError, ValueError)):
            snapshot_to_dict(snap)


#: Modules allowed to *call* an ``entity_id_for_*`` constructor: the two
#: header-AST producers and their ``extract`` entity modules. By option
#: (a)'s own design the identity is computed once, at parse time, and read
#: thereafter — never recomputed from an already-parsed model object, which
#: is structurally incapable of reproducing a typed ``ScopePath`` anyway.
_ALLOWED_RESOLVER_CALLERS = (
    "dumper_clang.py",
    "dumper_castxml.py",
    "extract/headers/",
    # ADR-063 Phase 2: dwarf_snapshot.py/extract/dwarf_scope.py build a typed
    # ScopePath like the two header-AST backends. extract/
    # export_symbol_identity.py is the shared export-table-only builder for
    # ELF/Mach-O/PE fallback construction -- scope-free branches only.
    "dwarf_snapshot.py",
    "extract/dwarf_scope.py",
    "extract/export_symbol_identity.py",
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
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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

    # ADR-063 Phase 2's closing slice: typedefs have no declaration object to
    # carry an identity on, so theirs travels in a sidecar keyed exactly like
    # `parse_typedefs_qualified()`.
    assert parser.parse_typedef_entity_ids()["ns::Alias"] == EntityId(
        scope=(Namespace("ns"),), kind=EntityKind.TYPEDEF, leaf_name="Alias"
    )
    assert set(parser.parse_typedef_entity_ids()) == set(
        parser.parse_typedefs_qualified()
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_populates_every_kind(tmp_path: Path) -> None:
    _assert_probe_identities(_clang_parser(_PROBE_HEADER, tmp_path, "probe"))


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_populates_every_kind(tmp_path: Path) -> None:
    _assert_probe_identities(_castxml_parser(_PROBE_HEADER, tmp_path, "probe"))


def _assert_probe_constant_identity(parser: Any) -> None:
    """The constant sidecar, for a parser given a real public-header set.

    Split from :func:`_assert_probe_identities` because ``parse_constants``
    is provenance-gated on both backends (it returns ``{}`` outright with no
    public set), so its sidecar can only be observed by a parser configured
    the way a real dump configures one.
    """
    assert parser.parse_constant_entity_ids()["ns::kLimit"] == EntityId(
        scope=(Namespace("ns"),), kind=EntityKind.CONSTANT, leaf_name="kLimit"
    )
    # The sidecar and the value map are built from one filtering pass, so
    # they agree on which constants qualify -- the property that lets a
    # detector join one against the other by key.
    assert set(parser.parse_constant_entity_ids()) == set(parser.parse_constants())


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_populates_the_constant_sidecar(tmp_path: Path) -> None:
    _assert_probe_constant_identity(
        _clang_parser(_PROBE_HEADER, tmp_path, "probe_const", public=True)
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_populates_the_constant_sidecar(tmp_path: Path) -> None:
    _assert_probe_constant_identity(
        _castxml_parser(_PROBE_HEADER, tmp_path, "probe_const", public=True)
    )


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
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_export_override_recognizes_non_itanium_mangling_prefixes(
    tmp_path: Path,
) -> None:
    """The export-evidence override must fire regardless of which
    mangling-scheme prefix castxml's guessed attribute happens to use --
    gating it on Itanium's own ``"_Z"`` prefix left a real Windows CI
    failure standing: a Windows-targeting castxml decorates a guessed
    C-linkage function/variable with its own ``"?...@@..."`` prefix,
    never Itanium's, so the override never matched even though the real
    export table already confirmed the bare name (Codex review, PR
    #943). Simulates that prefix by rewriting a real castxml dump's own
    ``mangled`` attributes post-hoc -- this sandbox has no MSVC-targeting
    castxml to reproduce the real failure directly."""
    header = tmp_path / "msvc_like.h"
    header.write_text('int foo(int x);\nextern "C" int c_var;\n')
    xml_out = tmp_path / "msvc_like.xml"
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
    root = parse_xml(xml_out).getroot()
    for el in root.iter():
        if el.get("name") == "foo" and el.get("mangled"):
            el.set("mangled", "?foo@@YAHH@Z")
        elif el.get("name") == "c_var" and el.get("mangled"):
            el.set("mangled", "?c_var@@3HA")
    parser = _CastxmlParser(
        root, exported_dynamic={"foo", "c_var"}, exported_static=set()
    )
    foo = _one(parser.parse_functions(), name="foo")
    assert foo.mangled == "foo"
    assert foo.entity_id is not None and foo.entity_id.extra == ("extern_c",)
    c_var = _one(parser.parse_variables(), name="c_var")
    assert c_var.mangled == "c_var"
    assert c_var.entity_id is not None and c_var.entity_id.extra == ("extern_c",)


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
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
