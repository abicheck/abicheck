# Copyright 2026 Nikolay Petrov
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

"""Regression test for castxml lambda-closure/anonymous-type name location
leakage (defect: identical headers in two different checkout directories
compared as BREAKING, since a lambda closure type's ``name`` embeds an
absolute source path -- ``"raii_guard<(lambda at /tmp/old/lib.hpp:4:37)>"``
vs. ``"raii_guard<(lambda at /tmp/new/lib.hpp:4:37)>"`` -- and old/new type
matching (``diff_helpers.type_map_key``) keys on that raw, unstripped
spelling. The clang JSON-AST frontend already strips this
(``dumper_clang_expr._normalize_qual_type``); the castxml frontend had no
equivalent.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.diff_helpers import type_map_key
from abicheck.dumper import _CastxmlParser
from abicheck.name_classification import strip_anonymous_type_location


def _file(root: Element, file_id: str, name: str) -> None:
    f = SubElement(root, "File")
    f.set("id", file_id)
    f.set("name", name)


def _lambda_struct_root(source_path: str) -> Element:
    """Mirror castxml output for a class template instantiated with a
    lambda closure type, e.g. ``raii_guard<decltype([]{})>``."""
    root = Element("CastXML", attrib={"format": "1.4.0"})
    _file(root, "f1", source_path)
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

    struct_el = SubElement(root, "Struct")
    struct_el.set("id", "_2")
    struct_el.set(
        "name",
        f"raii_guard<(lambda at {source_path}:4:37)>",
    )
    struct_el.set("context", "_1")
    struct_el.set("file", "f1")
    struct_el.set("line", "4")
    struct_el.set("members", "")

    return root


class TestLambdaClosureTypeNameLocationStripped:
    def test_type_name_strips_embedded_source_location(self) -> None:
        root = _lambda_struct_root("/tmp/old/lib.hpp")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        # The path is stripped; the :line:col discriminator is kept (Codex
        # review) so two distinct lambdas in the same header don't collapse
        # to one identity — see TestDistinctLambdasInOneSnapshotStayDistinct.
        assert parser._type_name("_2") == "raii_guard<(lambda:lib.hpp:4:37)>"

    def test_record_type_own_name_has_no_embedded_path(self) -> None:
        root = _lambda_struct_root("/tmp/old/lib.hpp")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        (rec,) = [t for t in types if t.name.startswith("raii_guard")]
        assert rec.name == "raii_guard<(lambda:lib.hpp:4:37)>"
        assert "/tmp/old" not in rec.name

    def test_two_checkout_directories_produce_identical_type_identity(self) -> None:
        # The actual bug: the same declaration, compiled from two different
        # checkout directories, must resolve to the SAME type_map_key so
        # old/new matching doesn't manufacture a spurious
        # type_removed/type_added pair for an unchanged type.
        old_root = _lambda_struct_root("/tmp/old/lib.hpp")
        new_root = _lambda_struct_root("/tmp/new/lib.hpp")
        old_parser = _CastxmlParser(
            old_root, exported_dynamic=set(), exported_static=set()
        )
        new_parser = _CastxmlParser(
            new_root, exported_dynamic=set(), exported_static=set()
        )
        (old_rec,) = [
            t for t in old_parser.parse_types() if t.name.startswith("raii_guard")
        ]
        (new_rec,) = [
            t for t in new_parser.parse_types() if t.name.startswith("raii_guard")
        ]
        assert type_map_key(old_rec) == type_map_key(new_rec)


class TestDistinctLambdasInOneSnapshotStayDistinct:
    """Codex review, real finding: stripping the *entire* location (path
    AND line:col) collapsed two distinct lambda closure types declared in
    the same header into one identical key, so diff_helpers.TypeMap
    silently overwrote one entry with the other -- changes to the
    discarded instantiation could be missed or compared against the wrong
    record. Keeping :line:col as a discriminator fixes this while still
    matching across checkout roots (the test above)."""

    def test_two_lambdas_in_one_header_produce_distinct_type_map_keys(self) -> None:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", "/tmp/old/lib.hpp")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        first = SubElement(root, "Struct")
        first.set("id", "_2")
        first.set("name", "guard<(lambda at /tmp/old/lib.hpp:4:3)>")
        first.set("context", "_1")
        first.set("file", "f1")
        first.set("line", "4")
        first.set("members", "")

        second = SubElement(root, "Struct")
        second.set("id", "_3")
        second.set("name", "guard<(lambda at /tmp/old/lib.hpp:40:3)>")
        second.set("context", "_1")
        second.set("file", "f1")
        second.set("line", "40")
        second.set("members", "")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = [t for t in parser.parse_types() if t.name.startswith("guard")]
        assert len(types) == 2
        names = {t.name for t in types}
        assert names == {"guard<(lambda:lib.hpp:4:3)>", "guard<(lambda:lib.hpp:40:3)>"}
        keys = {type_map_key(t) for t in types}
        assert len(keys) == 2, "distinct lambdas must not collide on one key"


class TestDistinctLambdasInDifferentHeadersStayDistinct:
    """Round-8 review finding (Codex, fresh evidence): keeping only
    :line:col as a discriminator fixed the same-header collision above, but
    two distinct lambda closure types declared at the IDENTICAL line:col in
    two DIFFERENT headers still collided, since neither header's own path
    survived stripping. The declaring header's own basename is now kept
    alongside :line:col, distinguishing this case too."""

    def test_two_lambdas_at_same_position_in_different_headers_stay_distinct(
        self,
    ) -> None:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", "/src/one.hpp")
        _file(root, "f2", "/src/two.hpp")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        first = SubElement(root, "Struct")
        first.set("id", "_2")
        first.set("name", "guard<(lambda at /src/one.hpp:4:3)>")
        first.set("context", "_1")
        first.set("file", "f1")
        first.set("line", "4")
        first.set("members", "")

        second = SubElement(root, "Struct")
        second.set("id", "_3")
        second.set("name", "guard<(lambda at /src/two.hpp:4:3)>")
        second.set("context", "_1")
        second.set("file", "f2")
        second.set("line", "4")
        second.set("members", "")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = [t for t in parser.parse_types() if t.name.startswith("guard")]
        assert len(types) == 2
        names = {t.name for t in types}
        assert names == {"guard<(lambda:one.hpp:4:3)>", "guard<(lambda:two.hpp:4:3)>"}
        keys = {type_map_key(t) for t in types}
        assert len(keys) == 2, (
            "two lambdas at the same line:col in different headers must "
            "not collide on one key"
        )


class TestPathContainingALiteralCloseParen:
    """CodeRabbit review, round 3: the path group was ``[^)]*?``, which by
    construction cannot match a path containing a literal ``)`` (a real,
    if unusual, Windows path like ``C:\\release (old)\\foo.hpp``) -- the
    identical failure mode ``\\S+?`` had for a path containing a space,
    just from a different character class. Fixed by widening to ``.*?``."""

    def test_windows_path_with_parenthesized_component_is_stripped(self) -> None:
        name = r"raii_guard<(lambda at C:\release (old)\foo.hpp:4:37)>"
        assert strip_anonymous_type_location(name) == "raii_guard<(lambda:foo.hpp:4:37)>"

    def test_two_checkouts_differing_only_in_a_parenthesized_component_match(
        self,
    ) -> None:
        old = r"guard<(lambda at C:\release (old)\a.hpp:4:37)>"
        new = r"guard<(lambda at C:\release (new)\a.hpp:4:37)>"
        assert strip_anonymous_type_location(old) == strip_anonymous_type_location(new)


class TestOrdinaryNameWhitespaceIsPreserved:
    """Codex review, round 4: strip_anonymous_type_location() is now applied
    to every castxml record/enum name at extraction time (not just
    anonymous ones), so its whitespace-collapse step used to rewrite
    meaningful internal whitespace on an ORDINARY name too -- notably a
    C++20 fixed-string NTTP template argument, where two distinct
    specializations differing only in embedded whitespace must not
    collapse onto the same identity."""

    def test_distinct_fixed_string_template_args_stay_distinct(self) -> None:
        a = strip_anonymous_type_location('Tag<"a  b">')
        b = strip_anonymous_type_location('Tag<"a b">')
        assert a == 'Tag<"a  b">'
        assert b == 'Tag<"a b">'
        assert a != b

    def test_composite_name_with_a_real_lambda_marker_preserves_unrelated_whitespace(
        self,
    ) -> None:
        """Round-4 review, second finding: even the "only collapse when the
        substitution fired" gate wasn't narrow enough -- a composite name
        where the substitution legitimately fires in ONE template argument
        (a real lambda marker) must not touch whitespace in an unrelated
        SIBLING argument (a fixed-string NTTP literal)."""
        a = strip_anonymous_type_location('Tag<"a  b", (lambda at /tmp/a.hpp:4:3)>')
        b = strip_anonymous_type_location('Tag<"a b", (lambda at /tmp/a.hpp:4:3)>')
        assert a == 'Tag<"a  b", (lambda:a.hpp:4:3)>'
        assert b == 'Tag<"a b", (lambda:a.hpp:4:3)>'
        assert a != b


class TestAnonymousMarkerRequiredBeforeStripping:
    """Round-4 review, Codex, fresh evidence: the regex previously matched a
    bare ``\\bat\\s+...:N:M`` anywhere in the name, so an ordinary
    specialization whose fixed-string argument merely CONTAINS
    location-shaped text (no real lambda/unnamed-type marker at all) was
    incorrectly rewritten, risking a collision with a genuinely distinct
    type. The regex now requires an actual ``(lambda``/``(unnamed <kind>``
    marker immediately before ``at``."""

    def test_fixed_string_literal_containing_location_shaped_text_is_untouched(
        self,
    ) -> None:
        name = 'Tag<"at /checkout:1:2)">'
        assert strip_anonymous_type_location(name) == name


class TestAnonymousEnumLocationStripped:
    def test_enum_name_has_no_embedded_path(self) -> None:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", "/tmp/old/lib.hpp")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        enum_el = SubElement(root, "Enumeration")
        enum_el.set("id", "_2")
        enum_el.set("name", "(unnamed enum at /tmp/old/lib.hpp:56:5)")
        enum_el.set("context", "_1")
        enum_el.set("file", "f1")
        enum_el.set("line", "56")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (enum_type,) = parser.parse_enums()
        assert enum_type.name == "(unnamed enum:lib.hpp:56:5)"
        assert "/tmp/old" not in enum_type.name

    def test_reference_to_anonymous_enum_matches_its_own_declaration(self) -> None:
        """Round-3 review finding (Codex + CodeRabbit, independently): a
        *reference* to an anonymous enum (e.g. a field's type) went through
        ``_type_name_uncached``'s bare ``el.get("name", "?")`` fallback,
        never the ``strip_anonymous_type_location`` normalization
        ``parse_enums()`` applies to the declaration itself -- so the
        reference and the declaration disagreed on spelling and never
        matched across two checkouts."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", "/tmp/old/lib.hpp")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        enum_el = SubElement(root, "Enumeration")
        enum_el.set("id", "_2")
        enum_el.set("name", "(unnamed enum at /tmp/old/lib.hpp:56:5)")
        enum_el.set("context", "_1")
        enum_el.set("file", "f1")
        enum_el.set("line", "56")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (enum_type,) = parser.parse_enums()
        # The reference (a field/param/return naming the same id) goes
        # through _type_name, not parse_enums -- both must agree.
        assert parser._type_name("_2") == enum_type.name == "(unnamed enum:lib.hpp:56:5)"


class TestQualifiedNameNormalizesEnclosingAnonymousScope:
    """Round-3 review finding (Codex + CodeRabbit, independently):
    ``_qualified_type_name`` copied an enclosing Struct/Class/Union's raw
    (unnormalized) ``name`` when building a nested type's ``qualified_name``
    -- so a type nested inside a lambda-closure-instantiated template (e.g.
    ``Wrapper<(lambda at /checkout/a.hpp:4:3)>::Inner``) still leaked the
    checkout path via the enclosing segment, even though the leaf itself was
    already normalized -- defeating type_map_key's qualified_name-preferred
    cross-checkout matching for nested declarations."""

    def _nested_struct_root(self, source_path: str) -> Element:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", source_path)
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        outer = SubElement(root, "Struct")
        outer.set("id", "_2")
        outer.set("name", f"Wrapper<(lambda at {source_path}:4:3)>")
        outer.set("context", "_1")
        outer.set("file", "f1")
        outer.set("line", "4")
        outer.set("members", "_3")

        inner = SubElement(root, "Struct")
        inner.set("id", "_3")
        inner.set("name", "Inner")
        inner.set("context", "_2")
        inner.set("file", "f1")
        inner.set("line", "5")
        inner.set("members", "")

        return root

    def test_qualified_name_has_no_embedded_path(self) -> None:
        root = self._nested_struct_root("/tmp/old/lib.hpp")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        (inner,) = [t for t in types if t.name == "Inner"]
        assert inner.qualified_name == "Wrapper<(lambda:lib.hpp:4:3)>::Inner"
        assert "/tmp/old" not in (inner.qualified_name or "")

    def test_two_checkout_directories_produce_identical_nested_type_identity(
        self,
    ) -> None:
        old_root = self._nested_struct_root("/tmp/old/lib.hpp")
        new_root = self._nested_struct_root("/tmp/new/lib.hpp")
        old_parser = _CastxmlParser(
            old_root, exported_dynamic=set(), exported_static=set()
        )
        new_parser = _CastxmlParser(
            new_root, exported_dynamic=set(), exported_static=set()
        )
        (old_inner,) = [t for t in old_parser.parse_types() if t.name == "Inner"]
        (new_inner,) = [t for t in new_parser.parse_types() if t.name == "Inner"]
        assert type_map_key(old_inner) == type_map_key(new_inner)
