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

"""Tests for the CastXML schema-completeness detectors: default member
initializers, `abstract` records, `enum class` scoping, the explicit
`override` specifier, and `[[deprecated]]` on each surface kind.

Each ChangeKind gets a model-level detector test (constructing snapshots
directly) plus, where the parsing itself is the interesting part, a real
castxml-XML parser test in TestCastxmlParserPopulatesNewAttributes.
"""

from __future__ import annotations

from xml.etree.ElementTree import fromstring

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper import _CastxmlParser
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _snap(
    version="1.0",
    functions=None,
    variables=None,
    types=None,
    enums=None,
    from_headers=True,
    ast_producer="castxml",
):
    # ast_producer defaults to "castxml" (not None): every detector under
    # test here gates on _both_castxml_backed, since these facts are
    # castxml-only today (the clang header backend doesn't populate them
    # yet) — a bare from_headers=True snapshot with ast_producer=None would
    # otherwise fail that gate and every test below would see zero changes
    # (Codex review, PR #582).
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        from_headers=from_headers,
        ast_producer=ast_producer if from_headers else None,
    )


def _pub_func(name, mangled, **kwargs):
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        **kwargs,
    )


def _pub_var(name, mangled, type_="int", **kwargs):
    return Variable(
        name=name, mangled=mangled, type=type_, visibility=Visibility.PUBLIC, **kwargs
    )


def _kinds(result):
    return {c.kind for c in result.changes}


def _make_parser(xml_str: str) -> _CastxmlParser:
    root = fromstring(xml_str)  # noqa: S314  # nosec B314 (trusted test data)
    return _CastxmlParser(root, set(), set())


# ── type_became_abstract / type_lost_abstract ───────────────────────────────


class TestTypeAbstractChanged:
    def test_became_abstract(self):
        t_old = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=False)
        t_new = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=True)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_BECAME_ABSTRACT in _kinds(r)
        assert r.verdict == Verdict.API_BREAK

    def test_lost_abstract(self):
        t_old = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=True)
        t_new = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=False)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_LOST_ABSTRACT in _kinds(r)

    def test_none_on_either_side_skips(self):
        """DWARF/symbols-only mode (is_abstract=None) must not manufacture a
        finding from schema evolution / tier downgrade."""
        t_old = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=None)
        t_new = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=True)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_BECAME_ABSTRACT not in _kinds(r)


# ── enum_became_scoped / enum_lost_scoped ───────────────────────────────────


class TestEnumScopedChanged:
    def test_became_scoped(self):
        e_old = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=False)
        e_new = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=True)
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_BECAME_SCOPED in _kinds(r)
        assert r.verdict == Verdict.API_BREAK

    def test_lost_scoped(self):
        e_old = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=True)
        e_new = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=False)
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_LOST_SCOPED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_none_on_either_side_skips(self):
        e_old = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=None)
        e_new = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=True)
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_BECAME_SCOPED not in _kinds(r)


# ── func_override_specifier_added / _removed ────────────────────────────────


class TestFuncOverrideSpecifierChanged:
    def test_gained_override(self):
        f_old = _pub_func(
            "Derived::draw", "_ZN7Derived4drawEv", is_virtual=True, is_override=False
        )
        f_new = _pub_func(
            "Derived::draw", "_ZN7Derived4drawEv", is_virtual=True, is_override=True
        )
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_OVERRIDE_SPECIFIER_ADDED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE

    def test_lost_override(self):
        f_old = _pub_func(
            "Derived::draw", "_ZN7Derived4drawEv", is_virtual=True, is_override=True
        )
        f_new = _pub_func(
            "Derived::draw", "_ZN7Derived4drawEv", is_virtual=True, is_override=False
        )
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_OVERRIDE_SPECIFIER_REMOVED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_none_on_either_side_skips(self):
        """A free function (or a producer that can't tell) leaves is_override
        None — never applicable/known, distinct from "not an override"."""
        f_old = _pub_func("plain_fn", "_Z8plain_fnv", is_override=None)
        f_new = _pub_func("plain_fn", "_Z8plain_fnv", is_override=True)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_OVERRIDE_SPECIFIER_ADDED not in _kinds(r)


# ── field_default_initializer_removed / _changed ────────────────────────────


class TestFieldDefaultInitializerChanged:
    def test_initializer_removed(self):
        t_old = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default="30")],
        )
        t_new = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default=None)],
        )
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_initializer_changed(self):
        t_old = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default="30")],
        )
        t_new = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default="60")],
        )
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_DEFAULT_INITIALIZER_CHANGED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE

    def test_initializer_gained_is_not_flagged(self):
        """Matches PARAM_DEFAULT_VALUE_*'s convention: gaining a default is
        purely additive and never itself reported."""
        t_old = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default=None)],
        )
        t_new = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default="30")],
        )
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED not in _kinds(r)
        assert ChangeKind.FIELD_DEFAULT_INITIALIZER_CHANGED not in _kinds(r)

    def test_skipped_when_not_header_aware(self):
        """Not header-tier-confirmed on both sides: None must not be read as
        a real removal (mirrors param_defaults' own gate)."""
        t_old = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default="30")],
        )
        t_new = RecordType(
            name="Cfg",
            kind="struct",
            size_bits=32,
            fields=[TypeField("timeout", "int", 0, default=None)],
        )
        r = compare(
            _snap(types=[t_old], from_headers=False),
            _snap(types=[t_new], from_headers=False),
        )
        assert ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED not in _kinds(r)


# ── {func,var,type,enum}_deprecated_{added,removed} ─────────────────────────


class TestFuncDeprecatedChanged:
    def test_deprecated_added(self):
        f_old = _pub_func("legacy_api", "_Z10legacy_apiv", deprecated=None)
        f_new = _pub_func(
            "legacy_api", "_Z10legacy_apiv", deprecated="use new_api instead"
        )
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_DEPRECATED_ADDED in _kinds(r)
        assert r.verdict == Verdict.COMPATIBLE

    def test_deprecated_removed(self):
        f_old = _pub_func(
            "legacy_api", "_Z10legacy_apiv", deprecated="use new_api instead"
        )
        f_new = _pub_func("legacy_api", "_Z10legacy_apiv", deprecated=None)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_DEPRECATED_REMOVED in _kinds(r)

    def test_skipped_when_not_header_aware(self):
        f_old = _pub_func("legacy_api", "_Z10legacy_apiv", deprecated="msg")
        f_new = _pub_func("legacy_api", "_Z10legacy_apiv", deprecated=None)
        r = compare(
            _snap(functions=[f_old], from_headers=False),
            _snap(functions=[f_new], from_headers=False),
        )
        assert ChangeKind.FUNC_DEPRECATED_REMOVED not in _kinds(r)


class TestVarDeprecatedChanged:
    def test_deprecated_added(self):
        v_old = _pub_var("g_legacyFlag", "g_legacyFlag", deprecated=None)
        v_new = _pub_var("g_legacyFlag", "g_legacyFlag", deprecated="")
        r = compare(_snap(variables=[v_old]), _snap(variables=[v_new]))
        assert ChangeKind.VAR_DEPRECATED_ADDED in _kinds(r)

    def test_deprecated_removed(self):
        v_old = _pub_var("g_legacyFlag", "g_legacyFlag", deprecated="")
        v_new = _pub_var("g_legacyFlag", "g_legacyFlag", deprecated=None)
        r = compare(_snap(variables=[v_old]), _snap(variables=[v_new]))
        assert ChangeKind.VAR_DEPRECATED_REMOVED in _kinds(r)


class TestTypeDeprecatedChanged:
    def test_deprecated_added(self):
        t_old = RecordType(
            name="OldWidget", kind="class", size_bits=32, deprecated=None
        )
        t_new = RecordType(
            name="OldWidget", kind="class", size_bits=32, deprecated="use Widget2"
        )
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_DEPRECATED_ADDED in _kinds(r)

    def test_deprecated_removed(self):
        t_old = RecordType(
            name="OldWidget", kind="class", size_bits=32, deprecated="use Widget2"
        )
        t_new = RecordType(
            name="OldWidget", kind="class", size_bits=32, deprecated=None
        )
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert ChangeKind.TYPE_DEPRECATED_REMOVED in _kinds(r)


class TestEnumDeprecatedChanged:
    def test_deprecated_added(self):
        e_old = EnumType(name="OldMode", members=[EnumMember("A", 0)], deprecated=None)
        e_new = EnumType(
            name="OldMode", members=[EnumMember("A", 0)], deprecated="use Mode2"
        )
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_DEPRECATED_ADDED in _kinds(r)

    def test_deprecated_removed(self):
        e_old = EnumType(
            name="OldMode", members=[EnumMember("A", 0)], deprecated="use Mode2"
        )
        e_new = EnumType(name="OldMode", members=[EnumMember("A", 0)], deprecated=None)
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert ChangeKind.ENUM_DEPRECATED_REMOVED in _kinds(r)


# ── producer-mismatch regression (Codex review, PR #582) ───────────────────
#
# Every fact above is castxml-only today: the clang header backend
# (--ast-frontend clang) also sets from_headers=True but never populates
# TypeField.default/deprecated, RecordType.is_abstract/deprecated,
# EnumType.is_scoped/deprecated, or Function.is_override/deprecated. Without
# gating on ast_producer specifically, comparing a castxml-parsed old
# snapshot to a clang-parsed new snapshot would read as every one of these
# facts having been removed, purely because the new side's parser doesn't
# capture them — never because anything really changed.


def _clang_snap(**kwargs):
    """Same shape as _snap, but tagged as if produced by the clang backend
    (which never sets any of the six castxml-only facts under test)."""
    return _snap(ast_producer="clang", **kwargs)


class TestProducerMismatchDoesNotFalsePositive:
    def test_type_abstract_producer_mismatch(self):
        t_old = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=True)
        t_new = RecordType(name="Shape", kind="class", size_bits=64, is_abstract=None)
        r = compare(_snap(types=[t_old]), _clang_snap(types=[t_new]))
        assert ChangeKind.TYPE_LOST_ABSTRACT not in _kinds(r)

    def test_enum_scoped_producer_mismatch(self):
        e_old = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=True)
        e_new = EnumType(name="Color", members=[EnumMember("Red", 0)], is_scoped=None)
        r = compare(_snap(enums=[e_old]), _clang_snap(enums=[e_new]))
        assert ChangeKind.ENUM_LOST_SCOPED not in _kinds(r)

    def test_func_override_producer_mismatch(self):
        f_old = _pub_func(
            "Derived::draw", "_ZN7Derived4drawEv", is_virtual=True, is_override=True
        )
        f_new = _pub_func(
            "Derived::draw", "_ZN7Derived4drawEv", is_virtual=True, is_override=None
        )
        r = compare(_snap(functions=[f_old]), _clang_snap(functions=[f_new]))
        assert ChangeKind.FUNC_OVERRIDE_SPECIFIER_REMOVED not in _kinds(r)

    def test_field_default_initializer_producer_mismatch(self):
        t_old = RecordType(
            name="Cfg", kind="struct", size_bits=32,
            fields=[TypeField("timeout", "int", 0, default="30")],
        )
        t_new = RecordType(
            name="Cfg", kind="struct", size_bits=32,
            fields=[TypeField("timeout", "int", 0, default=None)],
        )
        r = compare(_snap(types=[t_old]), _clang_snap(types=[t_new]))
        assert ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED not in _kinds(r)

    def test_func_deprecated_producer_mismatch(self):
        f_old = _pub_func("old_api", "_Z7old_apiv", deprecated="use new_api")
        f_new = _pub_func("old_api", "_Z7old_apiv", deprecated=None)
        r = compare(_snap(functions=[f_old]), _clang_snap(functions=[f_new]))
        assert ChangeKind.FUNC_DEPRECATED_REMOVED not in _kinds(r)

    def test_var_deprecated_producer_mismatch(self):
        v_old = _pub_var("kOld", "kOld", deprecated="use kNew")
        v_new = _pub_var("kOld", "kOld", deprecated=None)
        r = compare(_snap(variables=[v_old]), _clang_snap(variables=[v_new]))
        assert ChangeKind.VAR_DEPRECATED_REMOVED not in _kinds(r)

    def test_type_deprecated_producer_mismatch(self):
        t_old = RecordType(name="OldWidget", kind="class", size_bits=32, deprecated="x")
        t_new = RecordType(name="OldWidget", kind="class", size_bits=32, deprecated=None)
        r = compare(_snap(types=[t_old]), _clang_snap(types=[t_new]))
        assert ChangeKind.TYPE_DEPRECATED_REMOVED not in _kinds(r)

    def test_enum_deprecated_producer_mismatch(self):
        e_old = EnumType(name="OldMode", members=[EnumMember("A", 0)], deprecated="x")
        e_new = EnumType(name="OldMode", members=[EnumMember("A", 0)], deprecated=None)
        r = compare(_snap(enums=[e_old]), _clang_snap(enums=[e_new]))
        assert ChangeKind.ENUM_DEPRECATED_REMOVED not in _kinds(r)

    def test_castxml_both_sides_still_fires(self):
        """Sanity check: the gate must not be so strict it also blocks the
        genuine castxml-vs-castxml case (already covered per-kind above, but
        asserted once more here right next to the mismatch tests)."""
        f_old = _pub_func("old_api", "_Z7old_apiv", deprecated="use new_api")
        f_new = _pub_func("old_api", "_Z7old_apiv", deprecated=None)
        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        assert ChangeKind.FUNC_DEPRECATED_REMOVED in _kinds(r)

    def test_survives_dump_to_json_then_compare_files_workflow(self):
        """The realistic workflow — dump each side to JSON, reload, THEN
        compare — must still fire. This is the actual bug Codex caught:
        snapshot_to_dict() wrote ast_producer, but snapshot_from_dict()
        never read it back, so every persisted-then-reloaded castxml
        snapshot silently lost the tag and _both_castxml_backed was always
        False for this — by far the most common — real workflow, not just
        the in-memory compare() used by every other test in this file."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        f_old = _pub_func("old_api", "_Z7old_apiv", deprecated="use new_api")
        f_new = _pub_func("old_api", "_Z7old_apiv", deprecated=None)
        old_reloaded = snapshot_from_dict(snapshot_to_dict(_snap(functions=[f_old])))
        new_reloaded = snapshot_from_dict(snapshot_to_dict(_snap(functions=[f_new])))
        assert old_reloaded.ast_producer == "castxml"
        assert new_reloaded.ast_producer == "castxml"
        r = compare(old_reloaded, new_reloaded)
        assert ChangeKind.FUNC_DEPRECATED_REMOVED in _kinds(r)


# ── Real castxml XML → model field population ───────────────────────────────

_ABSTRACT_RECORD_XML = """<?xml version="1.0"?>
<CastXML>
  <Class id="_2" name="Shape" context="_1" file="f1" line="1" size="64" align="64" abstract="1"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_CONCRETE_RECORD_XML = """<?xml version="1.0"?>
<CastXML>
  <Class id="_2" name="Shape" context="_1" file="f1" line="1" size="64" align="64"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_SCOPED_ENUM_XML = """<?xml version="1.0"?>
<CastXML>
  <Enumeration id="_2" name="Color" context="_1" file="f1" line="1" scoped="1">
    <EnumValue name="Red" init="0"/>
  </Enumeration>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_DEPRECATED_XML = """<?xml version="1.0"?>
<CastXML>
  <Function id="_2" name="old_api" returns="_v" context="_1" mangled="_Z7old_apiv"
            file="f1" line="1" deprecation="use new_api instead"/>
  <Variable id="_3" name="g_old" type="_i" context="_1" mangled="g_old" file="f1" line="2"
            deprecation=""/>
  <Class id="_4" name="OldWidget" context="_1" file="f1" line="3" size="32" align="32"
         deprecation="use Widget2"/>
  <Typedef id="_5" name="MyInt" type="_i" context="_1" file="f1" line="4"
           deprecation="use int directly"/>
  <FundamentalType id="_v" name="void" size="0"/>
  <FundamentalType id="_i" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_FIELD_INIT_XML = """<?xml version="1.0"?>
<CastXML>
  <Struct id="_2" name="Cfg" context="_1" file="f1" line="1"
          members="_4" size="32" align="32"/>
  <Field id="_4" name="timeout" type="_7" offset="0" init="30"/>
  <FundamentalType id="_7" name="int" size="32"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""

_OVERRIDE_METHOD_XML = """<?xml version="1.0"?>
<CastXML>
  <Class id="_2" name="Derived" context="_1" file="f1" line="1" size="8" align="8"/>
  <Method id="_3" name="draw" returns="_v" context="_2" virtual="1"
          attributes="override" file="f1" line="2"/>
  <FundamentalType id="_v" name="void" size="0"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""


class TestCastxmlParserPopulatesNewAttributes:
    def test_abstract_attribute(self) -> None:
        p = _make_parser(_ABSTRACT_RECORD_XML)
        assert p.parse_types()[0].is_abstract is True

    def test_non_abstract_record(self) -> None:
        p = _make_parser(_CONCRETE_RECORD_XML)
        assert p.parse_types()[0].is_abstract is False

    def test_scoped_enum_attribute(self) -> None:
        p = _make_parser(_SCOPED_ENUM_XML)
        assert p.parse_enums()[0].is_scoped is True

    def test_deprecation_attribute_function_variable_type(self) -> None:
        p = _make_parser(_DEPRECATED_XML)
        fn = p.parse_functions()[0]
        assert fn.deprecated == "use new_api instead"
        var = p.parse_variables()[0]
        assert var.deprecated == ""
        rec = p.parse_types()[0]
        assert rec.deprecated == "use Widget2"

    def test_field_default_initializer(self) -> None:
        p = _make_parser(_FIELD_INIT_XML)
        assert p.parse_types()[0].fields[0].default == "30"

    def test_override_specifier(self) -> None:
        p = _make_parser(_OVERRIDE_METHOD_XML)
        fn = p.parse_functions()[0]
        assert fn.is_override is True

    def test_non_override_virtual_method(self) -> None:
        xml = _OVERRIDE_METHOD_XML.replace('attributes="override"', 'attributes=""')
        p = _make_parser(xml)
        fn = p.parse_functions()[0]
        assert fn.is_override is False

    def test_free_function_override_is_none(self) -> None:
        xml = """<?xml version="1.0"?>
<CastXML>
  <Function id="_2" name="plain" returns="_v" context="_1" mangled="_Z5plainv" file="f1" line="1"/>
  <FundamentalType id="_v" name="void" size="0"/>
  <Namespace id="_1" name="::"/>
  <File id="f1" name="test.h"/>
</CastXML>"""
        p = _make_parser(xml)
        fn = p.parse_functions()[0]
        assert fn.is_override is None
