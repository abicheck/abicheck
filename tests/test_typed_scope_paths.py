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

"""Typed parser scope tracking in both header-AST backends (ADR-063 Phase 2).

Three layers, mirroring ``test_castxml_var_access_value.py``'s own shape:

1. synthetic-AST/XML unit tests against ``_ClangAstParser``/``_CastxmlParser``
   directly (no toolchain needed);
2. ``integration``-marked live tests running the real ``clang -ast-dump=json``
   and ``castxml`` binaries, which is where every claim about what those two
   producers *actually* emit for an inline namespace, a reopened anonymous
   namespace, and sibling anonymous unions was verified;
3. the parity layer: ``flat_names(typed_path)`` must reproduce the flat
   scope/``qualified_name`` spelling byte-for-byte, since this slice is
   purely additive and must not move a single existing snapshot field.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.dumper_castxml import _CastxmlParser
from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.headers.castxml.location import qualified_name
from abicheck.extract.headers.clang.scope import (
    anonymous_scope_key,
    anonymous_scope_kind,
    scope_segment_for,
)
from abicheck.extract.headers.scope_segments import flat_names
from abicheck.model.identity import Anonymous, InlineNamespace, Namespace, Record
from abicheck.name_classification import strip_anonymous_type_location


def _tu(*inner: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def _ns(name: str | None, *inner: dict[str, Any], inline: bool = False) -> dict:
    node: dict[str, Any] = {"kind": "NamespaceDecl", "inner": list(inner)}
    if name is not None:
        node["name"] = name
    if inline:
        node["isInline"] = True
    return node


def _rec(
    name: str | None,
    *inner: dict[str, Any],
    tag: str = "struct",
    access: str | None = None,
) -> dict:
    node: dict[str, Any] = {
        "kind": "CXXRecordDecl",
        "tagUsed": tag,
        "inner": list(inner),
    }
    if name is not None:
        node["name"] = name
    if access is not None:
        node["access"] = access
    return node


def _records_by_name(parser: _ClangAstParser) -> dict[str, Any]:
    return {d.node.get("name", ""): d for d in parser._records if d.node.get("name")}


# ── clang: node-level segment construction ───────────────────────────────────


class TestClangScopeSegmentForNode:
    def test_namespace_node_becomes_a_namespace_segment(self) -> None:
        assert scope_segment_for(_ns("ns"), access="public") == Namespace("ns")

    def test_inline_namespace_node_becomes_an_inline_segment(self) -> None:
        segment = scope_segment_for(_ns("v1", inline=True), access="public")
        assert segment == InlineNamespace("v1")
        assert segment != Namespace("v1")

    def test_record_node_becomes_a_record_segment_carrying_access(self) -> None:
        segment = scope_segment_for(_rec("A"), access="private")
        assert segment == Record("A")
        assert isinstance(segment, Record)
        assert segment.access == "private"

    def test_record_and_namespace_of_one_name_are_different_segments(self) -> None:
        assert scope_segment_for(_rec("A"), access="public") != scope_segment_for(
            _ns("A"), access="public"
        )

    def test_named_linkage_spec_contributes_no_segment(self) -> None:
        """Unreachable in real clang output (a linkage specification is
        spelled with a string literal), and deliberately not mapped onto some
        other segment kind — a guess there would be a new collision, not a
        recovered fact."""
        node = {"kind": "LinkageSpecDecl", "name": "C", "language": "C"}
        assert scope_segment_for(node, access="public") is None

    def test_specialization_node_contributes_no_segment_here(self) -> None:
        """``_walk`` owns the trimmed ``A<double>`` spelling; this function
        must not form a second opinion about it."""
        node = {"kind": "ClassTemplateSpecializationDecl", "name": "A"}
        assert scope_segment_for(node, access="public") is None

    def test_anonymous_node_without_an_ordinal_contributes_nothing(self) -> None:
        assert scope_segment_for(_ns(None), access="public") is None

    def test_anonymous_node_with_an_ordinal_becomes_an_anonymous_segment(self) -> None:
        assert scope_segment_for(
            _ns(None), access="public", anonymous_ordinal=2
        ) == Anonymous("namespace", 2)

    @pytest.mark.parametrize("tag", ("struct", "class", "union"))
    def test_anonymous_record_kind_follows_the_tag(self, tag: str) -> None:
        assert anonymous_scope_kind(_rec(None, tag=tag)) == tag

    def test_anonymous_namespace_and_anonymous_struct_are_different_kinds(self) -> None:
        assert anonymous_scope_kind(_ns(None)) != anonymous_scope_kind(_rec(None))

    def test_implicit_node_is_never_an_anonymous_scope(self) -> None:
        """clang's implicit injected-class-name records are walked but must
        not consume a sibling ordinal."""
        node = _rec(None)
        node["isImplicit"] = True
        assert anonymous_scope_kind(node) is None

    def test_unnamed_linkage_spec_is_not_an_anonymous_scope(self) -> None:
        assert (
            anonymous_scope_kind({"kind": "LinkageSpecDecl", "language": "C"}) is None
        )

    def test_named_node_is_not_an_anonymous_scope(self) -> None:
        assert anonymous_scope_kind(_ns("ns")) is None

    def test_unknown_record_tag_is_not_an_anonymous_scope(self) -> None:
        assert anonymous_scope_kind(_rec(None, tag="enum")) is None


class TestClangAnonymousScopeKey:
    def test_reopened_namespace_reports_the_original_id(self) -> None:
        node = _ns(None)
        node["id"] = "0x2"
        node["previousDecl"] = "0x1"
        node["originalNamespace"] = {"id": "0x1", "kind": "NamespaceDecl", "name": ""}
        assert anonymous_scope_key(node) == "0x1"

    def test_previous_decl_is_used_when_no_original_namespace(self) -> None:
        node = _rec(None)
        node["id"] = "0x2"
        node["previousDecl"] = "0x1"
        assert anonymous_scope_key(node) == "0x1"

    def test_previous_decl_is_used_when_original_namespace_has_no_id(self) -> None:
        """``originalNamespace`` present as a dict, but without a usable
        ``id`` of its own, falls through to ``previousDecl`` rather than
        reporting no key at all -- never observed in real clang output (an
        ``originalNamespace`` always carries the referenced node's real
        id), but this function must not assume it."""
        node = _ns(None)
        node["id"] = "0x2"
        node["previousDecl"] = "0x1"
        node["originalNamespace"] = {"kind": "NamespaceDecl", "name": ""}
        assert anonymous_scope_key(node) == "0x1"

    def test_first_declaration_reports_its_own_id(self) -> None:
        node = _ns(None)
        node["id"] = "0x1"
        assert anonymous_scope_key(node) == "0x1"

    def test_missing_id_reports_none(self) -> None:
        """A hand-built AST: "cannot be merged", never "same as the last one
        that also had no id"."""
        assert anonymous_scope_key(_ns(None)) is None


# ── clang: whole-walk behaviour ──────────────────────────────────────────────


class TestClangWalkScopePaths:
    def test_record_in_record_never_collides_with_record_in_namespace(self) -> None:
        """The exact collision a flat ``"A::B"`` spelling cannot express."""
        parser = _ClangAstParser(
            _tu(_rec("A", _rec("B")), _ns("outer", _ns("A", _rec("Bn")))), set(), set()
        )
        decls = _records_by_name(parser)
        assert decls["B"].scope_path == (Record("A"),)
        assert decls["Bn"].scope_path == (Namespace("outer"), Namespace("A"))
        assert decls["B"].scope_path != decls["Bn"].scope_path[-1:]

    def test_inline_namespace_is_distinguished_from_an_ordinary_one(self) -> None:
        parser = _ClangAstParser(
            _tu(
                _ns("a", _ns("v1", _rec("Inline"), inline=True)),
                _ns("b", _ns("v1", _rec("Plain"))),
            ),
            set(),
            set(),
        )
        decls = _records_by_name(parser)
        assert decls["Inline"].scope_path[-1] == InlineNamespace("v1")
        assert decls["Plain"].scope_path[-1] == Namespace("v1")
        assert decls["Inline"].scope_path[-1] != decls["Plain"].scope_path[-1]

    def test_record_scope_segment_carries_its_own_access(self) -> None:
        parser = _ClangAstParser(
            _tu(_rec("Outer", _rec("Priv", _rec("Leaf"), access="private"))),
            set(),
            set(),
        )
        segment = _records_by_name(parser)["Leaf"].scope_path[-1]
        assert isinstance(segment, Record)
        assert segment.access == "private"

    def test_sibling_anonymous_scopes_get_distinct_ordinals(self) -> None:
        parser = _ClangAstParser(
            _tu(
                _rec(
                    "Holder",
                    _rec(None, _rec("In1"), tag="union"),
                    _rec(None, _rec("In2"), tag="union"),
                )
            ),
            set(),
            set(),
        )
        decls = _records_by_name(parser)
        assert decls["In1"].scope_path == (Record("Holder"), Anonymous("union", 0))
        assert decls["In2"].scope_path == (Record("Holder"), Anonymous("union", 1))

    def test_anonymous_ordinals_are_per_parent_not_global(self) -> None:
        """A global counter would make one anonymous scope's identity depend
        on how many unrelated ones happened to be walked first."""
        parser = _ClangAstParser(
            _tu(
                _rec("H1", _rec(None, _rec("A1"), tag="union")),
                _rec("H2", _rec(None, _rec("A2"), tag="union")),
            ),
            set(),
            set(),
        )
        decls = _records_by_name(parser)
        assert decls["A1"].scope_path == (Record("H1"), Anonymous("union", 0))
        assert decls["A2"].scope_path == (Record("H2"), Anonymous("union", 0))

    def test_anonymous_kind_disambiguates_at_a_shared_ordinal(self) -> None:
        parser = _ClangAstParser(
            _tu(
                _ns(None, _rec("InNs")), _rec("H", _rec(None, _rec("InU"), tag="union"))
            ),
            set(),
            set(),
        )
        decls = _records_by_name(parser)
        assert decls["InNs"].scope_path == (Anonymous("namespace", 0),)
        assert decls["InU"].scope_path[-1] == Anonymous("union", 0)
        assert decls["InNs"].scope_path[0] != decls["InU"].scope_path[-1]

    def test_reopened_anonymous_namespace_is_one_scope(self) -> None:
        """C++ merges two ``namespace { }`` blocks in one TU; clang emits two
        nodes linked by ``originalNamespace``. Numbering blocks positionally
        would split one real scope into two identities."""
        first = _ns(None, _rec("P"))
        first["id"] = "0x1"
        second = _ns(None, _rec("Q"))
        second["id"] = "0x2"
        second["previousDecl"] = "0x1"
        second["originalNamespace"] = {"id": "0x1", "kind": "NamespaceDecl", "name": ""}
        parser = _ClangAstParser(_tu(first, second), set(), set())
        decls = _records_by_name(parser)
        assert (
            decls["P"].scope_path
            == decls["Q"].scope_path
            == (Anonymous("namespace", 0),)
        )

    def test_typed_path_reproduces_the_flat_scope_exactly(self) -> None:
        """The additive-ness invariant: nothing this slice adds may move the
        flat spelling every existing consumer reads."""
        parser = _ClangAstParser(
            _tu(
                _ns("outer", _ns("v1", _rec("A", _rec("B")), inline=True)),
                _ns(None, _rec("Hidden")),
                _rec("H", _rec(None, _rec("In"), tag="union")),
                {"kind": "LinkageSpecDecl", "language": "C", "inner": [_rec("CRec")]},
            ),
            set(),
            set(),
        )
        seen = 0
        for decl in parser._records:
            assert flat_names(decl.scope_path) == decl.scope
            seen += 1
        assert seen >= 5

    def test_declarations_keep_their_flat_scope_untouched(self) -> None:
        parser = _ClangAstParser(
            _tu(_ns("outer", _ns("v1", _rec("A", _rec("B")), inline=True))),
            set(),
            set(),
        )
        decls = _records_by_name(parser)
        assert decls["B"].scope == ("outer", "v1", "A")

    def test_scope_path_defaults_to_empty_for_a_hand_built_decl(self) -> None:
        """``_Decl``'s new field is optional, so every existing direct
        construction (tests, sibling entity modules) keeps working."""
        from abicheck.dumper_clang import _Decl

        assert _Decl(node={}, scope=(), file="", access="public").scope_path == ()


# ── castxml: synthetic XML ───────────────────────────────────────────────────


def _el(parent: Element, tag: str, **attrib: str) -> Element:
    return SubElement(parent, tag, attrib=attrib)


def _castxml_root() -> Element:
    """A minimal, real-shaped castxml document.

    Mirrors real castxml 0.7 output verified in the integration test below:
    the global scope is ``<Namespace id="_1" name="::">``, a class-scope
    record carries an ``access`` attribute while a namespace-scope one does
    not, and an unnamed scope carries no ``name`` attribute at all.
    """
    root = Element("CastXML", attrib={"format": "1.4.0"})
    _el(root, "Namespace", id="_1", name="::", members="_2 _3 _4 _5")
    _el(root, "Namespace", id="_2", name="outer", context="_1", members="_6 _7")
    _el(root, "Namespace", id="_3", context="_1", members="_8")  # anonymous
    _el(root, "Struct", id="_4", name="Outer2", context="_1", members="_9 _10 _11")
    _el(root, "Namespace", id="_5", name="A", context="_1", members="_12")
    _el(root, "Struct", id="_6", name="A", context="_2", members="_13")
    _el(root, "Struct", id="_7", name="B", context="_6", access="public")
    _el(root, "Struct", id="_8", name="Hidden", context="_3")
    _el(root, "Union", id="_9", context="_4", access="public", members="_14")
    _el(root, "Union", id="_10", context="_4", access="private", members="_15")
    _el(root, "Struct", id="_11", name="Priv", context="_4", access="private")
    _el(root, "Struct", id="_12", name="B", context="_5")
    _el(root, "Struct", id="_13", name="B", context="_6", access="protected")
    _el(root, "Field", id="_14", name="u1", type="_16", context="_9", access="public")
    _el(root, "Field", id="_15", name="u2", type="_16", context="_10", access="public")
    return root


def _by_id(root: Element) -> dict[str, Element]:
    return {el.get("id", ""): el for el in root}


class TestCastxmlScopePaths:
    def test_record_in_record_never_collides_with_record_in_namespace(self) -> None:
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        els = _by_id(root)
        in_record = parser._scope_path(els["_13"])  # outer::A::B, A is a Struct
        in_namespace = parser._scope_path(els["_12"])  # A::B, A is a Namespace
        assert in_record == (Namespace("outer"), Record("A"))
        assert in_namespace == (Namespace("A"),)
        assert in_record[-1] != in_namespace[-1]

    def test_class_scope_access_is_carried(self) -> None:
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        segment = parser._scope_path(_by_id(root)["_15"])[-1]
        assert isinstance(segment, Anonymous)
        # The record ABOVE the anonymous union carries the access we can see.
        outer = parser._scope_path(_by_id(root)["_15"])[0]
        assert isinstance(outer, Record)
        assert outer.access == "public"

    def test_namespace_scope_record_defaults_to_the_no_access_spelling(self) -> None:
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        segment = parser._scope_path(_by_id(root)["_13"])[-1]
        assert isinstance(segment, Record)
        assert segment.access == "public"

    def test_sibling_anonymous_unions_get_distinct_ordinals(self) -> None:
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        els = _by_id(root)
        assert parser._scope_path(els["_14"]) == (
            Record("Outer2"),
            Anonymous("union", 0),
        )
        assert parser._scope_path(els["_15"]) == (
            Record("Outer2"),
            Anonymous("union", 1),
        )

    def test_anonymous_namespace_becomes_an_anonymous_segment(self) -> None:
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_8"]) == (Anonymous("namespace", 0),)

    def test_global_scope_contributes_no_segment(self) -> None:
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_4"]) == ()

    def test_dangling_context_id_stops_the_walk(self) -> None:
        """A ``context`` id with no matching element is never real castxml
        output (every id it emits resolves), but the walk must not raise on
        one -- it stops exactly where the chain goes dangling, same as
        reaching the global scope."""
        root = Element("CastXML")
        _el(root, "Struct", id="_1", name="Dangling", context="_missing")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_1"]) == ()

    def test_named_non_scope_context_contributes_no_segment(self) -> None:
        """Only ``Namespace``/``Struct``/``Class``/``Union`` are ever
        referenced as a ``context`` in real castxml output (this module's own
        docstring) -- a named element of any other tag must still contribute
        nothing rather than a guessed segment kind."""
        root = Element("CastXML")
        _el(root, "Namespace", id="_1", name="::")
        _el(root, "Function", id="_2", name="foo", context="_1")
        _el(root, "Struct", id="_3", name="Inner", context="_2")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_3"]) == ()

    def test_anonymous_non_scope_context_contributes_no_segment(self) -> None:
        """The unnamed counterpart of the above: an unnamed element of an
        unrecognized tag contributes no ``Anonymous`` segment either."""
        root = Element("CastXML")
        _el(root, "Namespace", id="_1", name="::")
        _el(root, "Function", id="_2", context="_1")
        _el(root, "Struct", id="_3", name="Inner", context="_2")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_3"]) == ()

    def test_ordinal_falls_back_to_a_full_scan_when_members_is_stale(self) -> None:
        """``members`` is castxml's own record of declaration order, but
        this module must not trust it blindly: a member missing from a
        stale/incomplete ``members`` string still gets a real ordinal from
        the full id-map scan, not a silently wrong one (and the member that
        *is* listed still resolves the fast way, without needing the scan)."""
        root = Element("CastXML")
        _el(root, "Namespace", id="_1", name="::")
        _el(root, "Struct", id="_2", name="Container", context="_1", members="_3")
        _el(root, "Struct", id="_3", context="_2")  # listed in `members`
        _el(root, "Struct", id="_4", context="_2")  # NOT listed -- stale
        # A field inside each anonymous struct, so its own scope_path names
        # the struct's Anonymous segment (scope_path never includes *el*
        # itself, only what contains it).
        _el(root, "Field", id="_5", name="f3", context="_3")
        _el(root, "Field", id="_6", name="f4", context="_4")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        els = _by_id(root)
        assert parser._scope_path(els["_5"]) == (
            Record("Container"),
            Anonymous("struct", 0),
        )
        assert parser._scope_path(els["_6"]) == (
            Record("Container"),
            Anonymous("struct", 1),
        )

    def test_ordinal_falls_back_when_members_attribute_is_empty(self) -> None:
        """``members=""`` (present but empty) must take the same full-scan
        fallback as a missing ``members`` attribute entirely, not be
        mistaken for "one member, the empty string"."""
        root = Element("CastXML")
        _el(root, "Namespace", id="_1", name="::")
        _el(root, "Struct", id="_2", name="Container", context="_1", members="")
        _el(root, "Struct", id="_3", context="_2")
        _el(root, "Field", id="_4", name="f3", context="_3")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_4"]) == (
            Record("Container"),
            Anonymous("struct", 0),
        )

    def test_ordinal_falls_back_when_the_parent_itself_is_unresolvable(self) -> None:
        """An anonymous scope whose own ``context`` id resolves to nothing
        (never real castxml output -- every id it emits resolves -- but not
        this module's job to assume) still gets a deterministic ordinal
        from the full-document fallback scan instead of raising."""
        root = Element("CastXML")
        _el(root, "Struct", id="_1", context="_missing")
        _el(root, "Field", id="_2", name="f", context="_1")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_2"]) == (Anonymous("struct", 0),)

    def test_ordinal_skips_a_dangling_id_inside_members(self) -> None:
        """A ``members`` string naming an id absent from the document (never
        real castxml output, but not this module's job to assume) is
        skipped rather than raising or miscounting the real siblings."""
        root = Element("CastXML")
        _el(root, "Namespace", id="_1", name="::")
        _el(
            root,
            "Struct",
            id="_2",
            name="Container",
            context="_1",
            members="_missing _3",
        )
        _el(root, "Struct", id="_3", context="_2")
        _el(root, "Field", id="_4", name="f3", context="_3")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._scope_path(_by_id(root)["_4"]) == (
            Record("Container"),
            Anonymous("struct", 0),
        )

    def test_typed_path_reproduces_qualified_name_exactly(self) -> None:
        """Oracle: ``qualified_name`` is exactly the typed path's own names
        plus the element's own (already-stripped) name -- so the typed walk
        provably kept the same parents, in the same order, as the flat one.
        """
        root = _castxml_root()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        checked = 0
        for el in root:
            if el.tag not in ("Struct", "Class", "Union", "Field", "Namespace"):
                continue
            own = strip_anonymous_type_location(el.get("name", "") or "")
            rebuilt = "::".join([*flat_names(parser._scope_path(el)), own])
            assert rebuilt == qualified_name(parser._ctx, el)
            checked += 1
        assert checked >= 10

    def test_a_cycle_in_the_context_chain_terminates(self) -> None:
        """Same cycle guard ``qualified_name`` already carries."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _el(root, "Namespace", id="_1", name="a", context="_2")
        _el(root, "Namespace", id="_2", name="b", context="_1")
        _el(root, "Struct", id="_3", name="S", context="_1")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert len(parser._scope_path(_by_id(root)["_3"])) == 2


# ── live producers ───────────────────────────────────────────────────────────


_LIVE_HEADER = textwrap.dedent(
    """
    namespace outer { inline namespace v1 { struct A { struct B { int x; }; }; } }
    namespace outer { namespace v1x { struct A { struct B { int z; }; }; } }
    namespace { struct P { int a; }; }
    namespace { struct Q { int b; }; }
    struct Holder { union { int u1; float u2; }; union { int u3; }; };
    namespace Reopened { struct { struct RFirst {}; } r1; }
    namespace Reopened { struct { struct RSecond {}; } r2; }
    namespace Transparent {
        extern "C" { struct { struct TFirst {}; } t1; }
        struct { struct TSecond {}; } t2;
    }
    """
)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_typed_scope_paths(tmp_path: Path) -> None:
    """The evidence layer for every clang claim this module's helpers make."""
    header = tmp_path / "live.hpp"
    header.write_text(_LIVE_HEADER)
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
    parser = _ClangAstParser(json.loads(out.stdout), set(), set())
    decls = {}
    for decl in parser._records:
        name = decl.node.get("name", "")
        if name:
            decls.setdefault((name, decl.scope), decl)

    # Inline namespace: detected, and distinct from an ordinary sibling.
    inline_b = next(d for (n, s), d in decls.items() if n == "B" and "v1" in s)
    plain_b = next(d for (n, s), d in decls.items() if n == "B" and "v1x" in s)
    assert inline_b.scope_path[:2] == (Namespace("outer"), InlineNamespace("v1"))
    assert plain_b.scope_path[:2] == (Namespace("outer"), Namespace("v1x"))
    # ... and the record-nesting segment is a Record, not a Namespace.
    assert isinstance(inline_b.scope_path[-1], Record)

    # Two reopened anonymous-namespace blocks are ONE scope.
    p = next(d for (n, _), d in decls.items() if n == "P")
    q = next(d for (n, _), d in decls.items() if n == "Q")
    assert p.scope_path == q.scope_path == (Anonymous("namespace", 0),)

    # A NAMED namespace reopened in two separate blocks must share ONE
    # running anonymous-ordinal counter across both blocks -- each block is
    # walked as a SEPARATE `_walk` call, so a call-local counter resets
    # between them and hands the second block's anonymous struct the same
    # ordinal already given to the first block's (Codex review, fresh
    # evidence: confirmed via `originalNamespace`/`previousDecl` linkage on
    # a real two-block reopening, identical to the anonymous-namespace case
    # above). `RFirst`/`RSecond` are two genuinely distinct anonymous
    # structs -- unlike a reopened namespace, an anonymous struct is never
    # merged across blocks -- so they must get DISTINCT ordinals under the
    # SAME `Namespace("Reopened")` segment, not collide at ordinal 0.
    r_first = next(d for d in parser._records if d.node.get("name") == "RFirst")
    r_second = next(d for d in parser._records if d.node.get("name") == "RSecond")
    assert r_first.scope_path[0] == r_second.scope_path[0] == Namespace("Reopened")
    assert r_first.scope_path[1] != r_second.scope_path[1]
    assert {r_first.scope_path[1], r_second.scope_path[1]} == {
        Anonymous("struct", 0),
        Anonymous("struct", 1),
    }

    # A TRANSPARENT AST wrapper (`extern "C" { ... }`, a `LinkageSpecDecl`)
    # contributes no `ScopePath` segment of its own, so an anonymous scope
    # declared inside it and one declared directly in the SAME enclosing
    # namespace are, from `ScopePath`'s own perspective, both direct
    # children of the identical logical scope -- they must share one
    # ordinal counter, not each get their own because they were walked as
    # separate AST nodes (Codex review, fresh evidence: confirmed the
    # `LinkageSpecDecl` node sits directly between the two in real clang
    # output).
    t_first = next(d for d in parser._records if d.node.get("name") == "TFirst")
    t_second = next(d for d in parser._records if d.node.get("name") == "TSecond")
    assert t_first.scope_path[0] == t_second.scope_path[0] == Namespace("Transparent")
    assert t_first.scope_path[1] != t_second.scope_path[1]
    assert {t_first.scope_path[1], t_second.scope_path[1]} == {
        Anonymous("struct", 0),
        Anonymous("struct", 1),
    }

    # Parity with the flat spelling, over every categorized declaration.
    for decl in parser._records + parser._functions + parser._variables:
        assert flat_names(decl.scope_path) == decl.scope


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_live_castxml_typed_scope_paths(tmp_path: Path) -> None:
    """The evidence layer for every castxml claim, including the two
    structural gaps this backend has (no inline-namespace element at all, no
    function-local declarations) that the module docstring records."""
    from xml.etree.ElementTree import parse as _parse

    header = tmp_path / "live.hpp"
    header.write_text(_LIVE_HEADER)
    xml_out = tmp_path / "live.xml"
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
    root = _parse(xml_out).getroot()
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())

    named = {}
    for el in root:
        if el.tag in ("Struct", "Class", "Union") and el.get("name"):
            named.setdefault(el.get("name", ""), el)

    # Two reopened anonymous-namespace blocks are one merged <Namespace>.
    assert parser._scope_path(named["P"]) == parser._scope_path(named["Q"])
    assert parser._scope_path(named["P"]) == (Anonymous("namespace", 0),)

    # Sibling anonymous unions in one record are distinguished.
    union_ids = [
        el.get("id", "")
        for el in root
        if el.tag == "Union"
        and not el.get("name")
        and el.get("context") == named["Holder"].get("id")
    ]
    assert len(union_ids) == 2
    fields = [el for el in root if el.tag == "Field" and el.get("context") in union_ids]
    ordinals = {
        parser._scope_path(f)[-1]
        for f in fields  # type: ignore[index]
    }
    assert ordinals == {Anonymous("union", 0), Anonymous("union", 1)}

    # castxml elides the inline namespace entirely -- a real backend gap,
    # already present in the flat spelling, pinned here so a future slice
    # cannot mistake it for a bug in this module.
    assert parser._scope_path(named["A"]) == (Namespace("outer"),)

    # Parity with qualified_name over every scope-bearing element.
    for el in root:
        if el.tag not in ("Struct", "Class", "Union", "Field", "Namespace", "Function"):
            continue
        own = strip_anonymous_type_location(el.get("name", "") or "")
        rebuilt = "::".join([*flat_names(parser._scope_path(el)), own])
        assert rebuilt == qualified_name(parser._ctx, el)
