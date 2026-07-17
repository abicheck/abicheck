"""Unit tests for struct-field parsing via the castxml ``members`` attribute.

Covers the fallback path added in PR #63 where castxml serialises Field
elements as space-separated IDs in the ``members`` attribute of a Struct
instead of as inline child elements.
"""

from __future__ import annotations

from xml.etree.ElementTree import fromstring

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper import _CastxmlParser
from abicheck.model import AbiSnapshot

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

_QUALIFIED_FIELDS_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Hw" context="_1" file="f1" line="1"
          members="_4 _5 _6 _7" size="128" align="32">
    <Field id="_4" name="status"   type="_10" offset="0"/>
    <Field id="_5" name="port"     type="_11" offset="32"/>
    <Field id="_6" name="cache"    type="_7"  offset="64" mutable="1"/>
    <Field id="_7" name="plain"    type="_7"  offset="96"/>
  </Struct>
  <CvQualifiedType id="_10" type="_7" volatile="1"/>
  <CvQualifiedType id="_11" type="_7" const="1" volatile="1"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_ANON_UNION_QUALIFIED_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Outer" context="_1" file="f1" line="1" size="64" align="32">
    <Field id="_3" name="" type="_4" offset="0"/>
  </Struct>
  <Union id="_4" name="" context="_2" file="f1" line="1" size="32" align="32">
    <Field id="_5" name="raw" type="_8" offset="0"/>
    <Field id="_6" name="cached" type="_7" offset="0" mutable="1"/>
  </Union>
  <CvQualifiedType id="_8" type="_7" volatile="1"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_TYPEDEF_QUALIFIED_FIELD_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Cfg" context="_1" file="f1" line="1"
          members="_4" size="32" align="32"/>
  <Field id="_4" name="x" type="_20" offset="0" context="_2"/>
  <Typedef id="_20" name="T" type="_21" context="_1"/>
  <CvQualifiedType id="_21" type="_7" const="1" volatile="1"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_RESTRICT_PARAM_XML = """<?xml version="1.0"?>
<CastXML>
  <Function id="_2" name="fill" returns="_i" context="_1" mangled="_Z4fillPi" file="f1" line="1">
    <Argument name="buf" type="_r"/>
  </Function>
  <CvQualifiedType id="_r" type="_p" restrict="1"/>
  <PointerType id="_p" type="_i"/>
  <FundamentalType id="_i" name="int" size="32"/>
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


class TestQualifiedFieldFacts:
    """castxml's CvQualifiedType (volatile/const+volatile) and Field
    ``mutable="1"`` must populate TypeField.is_volatile/is_const/is_mutable —
    not just leak into the type-name spelling — since diff_types.py's
    field_became_volatile/field_became_mutable detectors read the booleans.
    """

    def test_volatile_and_const_volatile_fields(self) -> None:
        p = _make_parser(_QUALIFIED_FIELDS_XML)
        fields = {f.name: f for f in p.parse_types()[0].fields}
        assert fields["status"].is_volatile is True
        assert fields["status"].is_const is False
        assert fields["port"].is_volatile is True
        assert fields["port"].is_const is True
        assert fields["cache"].is_volatile is False
        assert fields["plain"].is_volatile is False
        assert fields["plain"].is_const is False

    def test_mutable_field(self) -> None:
        p = _make_parser(_QUALIFIED_FIELDS_XML)
        fields = {f.name: f for f in p.parse_types()[0].fields}
        assert fields["cache"].is_mutable is True
        assert fields["plain"].is_mutable is False
        assert fields["status"].is_mutable is False

    def test_anonymous_union_flatten_preserves_qualifiers(self) -> None:
        """Flattened anonymous-union members must carry the same real
        volatile/mutable facts as an ordinary named field (not just the
        type-name string)."""
        p = _make_parser(_ANON_UNION_QUALIFIED_XML)
        fields = {f.name: f for f in p.parse_types()[0].fields}
        assert fields["raw"].is_volatile is True
        assert fields["raw"].is_mutable is False
        assert fields["cached"].is_mutable is True
        assert fields["cached"].is_volatile is False

    def test_const_volatile_through_typedef_indirection(self) -> None:
        """A field declared through a typedef to a cv-qualified type
        (``typedef const volatile int T; struct Cfg { T x; };``) renders as
        the bare alias spelling ("T"), so is_const/is_volatile must be
        resolved by walking the real XML type chain rather than pattern-
        matching that spelling — a regex over "T" would never see the
        qualifiers behind it (Codex review, PR #582)."""
        p = _make_parser(_TYPEDEF_QUALIFIED_FIELD_XML)
        field = p.parse_types()[0].fields[0]
        assert field.type == "T"
        assert field.is_const is True
        assert field.is_volatile is True


class TestRestrictParameter:
    """`restrict` has no ABI/mangling effect (unlike const/volatile), so it
    must never leak into the rendered type spelling — only into the
    dedicated ``Param.is_restrict`` fact — or a restrict-only parameter
    change would misfire the generic, BREAKING type-mismatch path instead of
    the dedicated compatible ``PARAM_RESTRICT_CHANGED`` detector (Codex
    review, PR #582).
    """

    def test_restrict_pointer_param_type_spelling_excludes_restrict(self) -> None:
        p = _make_parser(_RESTRICT_PARAM_XML)
        fn = p.parse_functions()[0]
        assert fn.params[0].type == "int*"
        assert "restrict" not in fn.params[0].type

    def test_restrict_pointer_param_sets_is_restrict(self) -> None:
        p = _make_parser(_RESTRICT_PARAM_XML)
        fn = p.parse_functions()[0]
        assert fn.params[0].is_restrict is True


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
        assert "const" in fields[0].type.lower()  # sample_rate → const int
        assert fields[1].type == "int"  # raw_value
        assert fields[2].type == "int"  # cache_hits
        # is_const must be a real structured fact, not just embedded in the
        # type-name string (diff_types.py's field const/volatile/mutable
        # detectors read these booleans, not the type spelling).
        assert fields[0].is_const is True
        assert fields[1].is_const is False
        assert fields[2].is_const is False

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
            "kLimit": "4",
            "A::kLimit": "1",
            "B::kLimit": "2",
            "C::kLimit": "3",
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

_FIELD_PLAIN_INT_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Reg" context="_1" file="f1" line="1"
          members="_4" size="32" align="32"/>
  <Field id="_4" name="status" type="_7" offset="0" context="_2"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_FIELD_VOLATILE_INT_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Reg" context="_1" file="f1" line="1"
          members="_4" size="32" align="32"/>
  <Field id="_4" name="status" type="_8" offset="0" context="_2"/>
  <CvQualifiedType id="_8" type="_7" volatile="1"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""


class TestByValueFieldQualifierEndToEndDiff:
    """Full parser -> compare() pipeline: a by-value field gaining `volatile`
    (castxml's real spelling embeds the qualifier in the type string, e.g.
    "int" -> "volatile int") is a deliberate source-break escalation for a
    BY-VALUE field (unlike a pointer/reference-position cv change, which is
    genuinely ABI-neutral) — see case30_field_qualifiers' BREAKING ground
    truth and test_top_level_field_const_is_not_neutralised in
    test_const_pointer_abi_neutral.py. So both the compatible
    FIELD_BECAME_VOLATILE and the breaking TYPE_FIELD_TYPE_CHANGED are
    expected from the real parser output, and the verdict is BREAKING. (An
    earlier attempt to suppress TYPE_FIELD_TYPE_CHANGED here, per a Codex
    review comment on PR #582, was reverted — it silently regressed that
    ground truth.)"""

    def test_by_value_volatile_field_change_escalates_to_breaking(self) -> None:
        old_types = _make_parser(_FIELD_PLAIN_INT_XML).parse_types()
        new_types = _make_parser(_FIELD_VOLATILE_INT_XML).parse_types()
        old_snap = AbiSnapshot(library="libtest.so.1", version="1.0", types=old_types)
        new_snap = AbiSnapshot(library="libtest.so.1", version="2.0", types=new_types)

        r = compare(old_snap, new_snap)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.FIELD_BECAME_VOLATILE in kinds
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds
        assert r.verdict == Verdict.BREAKING
