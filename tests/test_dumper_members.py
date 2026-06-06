"""Unit tests for struct-field parsing via the castxml ``members`` attribute.

Covers the fallback path added in PR #63 where castxml serialises Field
elements as space-separated IDs in the ``members`` attribute of a Struct
instead of as inline child elements.
"""
from __future__ import annotations

from xml.etree.ElementTree import fromstring

from abicheck.dumper import _CastxmlParser

# ── helpers ──────────────────────────────────────────────────────────────


def _make_parser(
    xml_str: str,
    exported: set[str] | None = None,
    public_header_paths: list[str] | None = None,
) -> _CastxmlParser:
    root = fromstring(xml_str)  # noqa: S314  # nosec B314 (trusted test data)
    exp = exported or set()
    return _CastxmlParser(root, exp, exp, public_header_paths=public_header_paths)


# ── fixtures ─────────────────────────────────────────────────────────────

_INLINE_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Rect" context="_1" file="f1" line="1" size="128" align="32">
    <Field id="_4" name="width"  type="_6" offset="0"/>
    <Field id="_5" name="height" type="_6" offset="32"/>
    <Field id="_6" name="depth"  type="_7" offset="64"/>
  </Struct>
  <FundamentalType id="_6" name="int" size="32"/>
  <FundamentalType id="_7" name="float" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_MEMBERS_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Point" context="_1" file="f1" line="1"
          members="_4 _5" size="64" align="32"/>
  <Field id="_4" name="x" type="_6" offset="0"  context="_2"/>
  <Field id="_5" name="y" type="_6" offset="32" context="_2"/>
  <FundamentalType id="_6" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_MEMBERS_CONST_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="SensorConfig" context="_1" file="f1" line="1"
          members="_4 _5 _6" size="96" align="32"/>
  <Field id="_4" name="sample_rate" type="_10" offset="0"  context="_2"/>
  <Field id="_5" name="raw_value"   type="_11" offset="32" context="_2"/>
  <Field id="_6" name="cache_hits"  type="_7"  offset="64" context="_2"/>
  <CvQualifiedType id="_10" type="_7" const="1"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <FundamentalType id="_11" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_MIXED_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Mixed" context="_1" file="f1" line="1"
          members="_3 _4 _5" size="32" align="32"/>
  <Method id="_3" name="doIt" returns="_7" context="_2"/>
  <Field id="_4" name="value" type="_7" offset="0" context="_2"/>
  <Destructor id="_5" name="~Mixed" context="_2"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_EMPTY_MEMBERS_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Empty" context="_1" file="f1" line="1"
          members="" size="0" align="8"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""


# ── tests ─────────────────────────────────────────────────────────────────


class TestInlineChildrenLayout:
    """Classic castxml layout: Field elements as inline children of Struct."""

    def test_parse_fields_inline_children(self) -> None:
        """Inline-child layout must continue to work after the members fallback."""
        p = _make_parser(_INLINE_XML)
        types = p.parse_types()
        assert len(types) == 1
        assert types[0].name == "Rect"
        fields = types[0].fields
        assert [f.name for f in fields] == ["width", "height", "depth"]


class TestMembersAttributeLayout:
    """castxml --castxml-output=1 layout: fields via ``members=`` attribute."""

    def test_resolves_fields_via_id_map(self) -> None:
        """Fields referenced through members= must be resolved via id_map."""
        p = _make_parser(_MEMBERS_XML)
        types = p.parse_types()
        assert len(types) == 1
        t = types[0]
        assert t.name == "Point"
        assert [f.name for f in t.fields] == ["x", "y"]
        assert t.fields[0].offset_bits == 0
        assert t.fields[1].offset_bits == 32

    def test_cv_qualified_fields_typed_correctly(self) -> None:
        """const-qualified fields must be correctly typed when resolved via members=."""
        p = _make_parser(_MEMBERS_CONST_XML)
        types = p.parse_types()
        assert len(types) == 1
        fields = types[0].fields
        assert "const" in fields[0].type.lower()   # sample_rate → const int
        assert fields[1].type == "int"              # raw_value
        assert fields[2].type == "int"              # cache_hits

    def test_non_field_ids_in_members_are_ignored(self) -> None:
        """Non-Field IDs referenced in members= must be silently skipped."""
        p = _make_parser(_MIXED_XML)
        types = p.parse_types()
        assert len(types) == 1
        assert len(types[0].fields) == 1
        assert types[0].fields[0].name == "value"

    def test_empty_members_attribute_yields_no_fields(self) -> None:
        """Struct with empty members= must deserialise to an empty field list."""
        p = _make_parser(_EMPTY_MEMBERS_XML)
        types = p.parse_types()
        assert len(types) == 1
        assert types[0].fields == []


# ── constant extraction (qualified names) ────────────────────────────────────

_CONSTANTS_XML = """<?xml version="1.0"?>
<CastXML>
  <Variable id="_10" name="kLimit" type="_6" init="4" context="_1" file="f1" line="1"/>
  <Variable id="_16" name="kLimit" type="_6" init="1" context="_7" file="f1" line="2"/>
  <Variable id="_17" name="kLimit" type="_6" init="2" context="_8" file="f1" line="3"/>
  <Variable id="_18" name="kLimit" type="_6" init="3" context="_9" file="f1" line="4"/>
  <FundamentalType id="_6" name="const int" size="32"/>
  <Namespace id="_1" name="::"/>
  <Namespace id="_7" name="A" context="_1"/>
  <Namespace id="_8" name="B" context="_1"/>
  <Struct id="_9" name="C" context="_1" file="f1" line="4"/>
  <File id="f1" name="test.h"/>
</CastXML>"""


_PRIVATE_CONST_XML = """<?xml version="1.0"?>
<CastXML>
  <Variable id="_13" name="kPublic" type="_6" init="1" context="_9" access="public" file="f1" line="3" static="1"/>
  <Variable id="_14" name="kPrivate" type="_6" init="2" context="_9" access="private" file="f1" line="5" static="1"/>
  <FundamentalType id="_6" name="const int" size="32"/>
  <Namespace id="_1" name="::"/>
  <Struct id="_9" name="Widget" context="_1" file="f1" line="2"/>
  <File id="f1" name="test.h"/>
</CastXML>"""


class TestConstantExtraction:
    def test_private_static_constants_are_not_extracted(self) -> None:
        # A private static constexpr member is an implementation detail a
        # consumer cannot name — its value must not be reported as an API
        # constant. Only the public member is extracted.
        p = _make_parser(_PRIVATE_CONST_XML, public_header_paths=["test.h"])
        assert p.parse_constants() == {"Widget::kPublic": "1"}

    def test_same_named_constants_in_different_scopes_do_not_alias(self) -> None:
        # Regression: bare-name keys would collapse A::kLimit/B::kLimit/C::kLimit
        # into one entry (last-wins), masking a real change. Keys must qualify by
        # namespace/class context (and a global constant stays bare).
        p = _make_parser(_CONSTANTS_XML, public_header_paths=["test.h"])
        consts = p.parse_constants()
        assert consts == {
            "kLimit": "4", "A::kLimit": "1", "B::kLimit": "2", "C::kLimit": "3",
        }

    def test_no_public_set_skips_extraction(self) -> None:
        # Without any public-header set (e.g. DWARF/symbols-only mode), constant
        # extraction is a no-op (provenance is opt-in).
        p = _make_parser(_CONSTANTS_XML)
        assert p.parse_constants() == {}

    def test_edge_branches_skip_non_constant_and_non_public(self) -> None:
        # Exercises the per-variable filters: a non-const (mutable) global is
        # skipped, a const without an initializer is skipped, and a const in a
        # system header is excluded by provenance.
        p = _make_parser(_CONST_EDGE_XML, public_header_paths=["api.h"])
        assert p.parse_constants() == {"kKept": "7"}

    def test_defensive_branches(self) -> None:
        # Builtin var, unnamed var, and a const with no source location are all
        # skipped; a const with an unresolved context still yields its bare name
        # (the context walk stops cleanly).
        p = _make_parser(_CONST_DEFENSIVE_XML, public_header_paths=["api.h"])
        assert p.parse_constants() == {"kReal": "1"}


def test_default_argument_value_is_parsed() -> None:
    # parse_functions must capture the castxml Argument `default` attribute.
    p = _make_parser(_DEFAULT_ARG_XML)
    fns = {f.name: f for f in p.parse_functions()}
    params = fns["connect"].params
    assert [p.default for p in params] == [None, "5000"]


_CONST_EDGE_XML = """<?xml version="1.0"?>
<CastXML>
  <Variable id="_2" name="kKept" type="_c" init="7" context="_1" file="f1" line="1"/>
  <Variable id="_3" name="counter" type="_i" init="0" context="_1" file="f1" line="2"/>
  <Variable id="_4" name="kNoInit" type="_c" context="_1" file="f1" line="3"/>
  <Variable id="_5" name="kSys" type="_c" init="9" context="_1" file="f2" line="1"/>
  <FundamentalType id="_c" name="const int" size="32"/>
  <FundamentalType id="_i" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="api.h"/>
  <File id="f2" name="/usr/include/stdint.h"/>
</CastXML>"""

_CONST_DEFENSIVE_XML = """<?xml version="1.0"?>
<CastXML>
  <Variable id="_2" name="kReal" type="_c" init="1" context="_999" file="f1" line="1"/>
  <Variable id="_3" type="_c" init="2" context="_1" file="f1" line="2"/>
  <Variable id="_4" name="kBuiltin" type="_c" init="3" context="_1" file="f0" line="0"/>
  <Variable id="_5" name="kNoLoc" type="_c" init="4" context="_1"/>
  <FundamentalType id="_c" name="const int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="api.h"/>
  <File id="f0" name="&lt;builtin&gt;"/>
</CastXML>"""

_DEFAULT_ARG_XML = """<?xml version="1.0"?>
<CastXML>
  <Function id="_2" name="connect" returns="_i" context="_1" mangled="_Z7connectPKci" file="f1" line="1">
    <Argument name="host" type="_p"/>
    <Argument name="timeout" type="_i" default="5000"/>
  </Function>
  <FundamentalType id="_i" name="int" size="32"/>
  <PointerType id="_p" type="_i"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""
