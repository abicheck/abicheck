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

"""Coverage-extension language-level detectors and dumper extraction.

Covers C-ellipsis variadic transitions, semantic contract attributes,
calling-convention attribute flips, dynamic exception specifications,
variable alignment, per-function vtable-index moves, the BTF/CTF
function/typedef bridge, and the castxml/clang extraction helpers.
"""
from __future__ import annotations

# Parse with defusedxml like production does (dumper_castxml feeds the parser
# defusedxml-parsed trees; stdlib fromstring would trip bandit B314 here).
from defusedxml.ElementTree import fromstring

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper_castxml import _CastxmlParser, _extract_contract_attributes
from abicheck.dumper_clang import (
    _clang_contract_attributes,
    _clang_exception_spec,
    _clang_var_alignment_bits,
)
from abicheck.model import AbiSnapshot, Function, Variable
from abicheck.service import _typeinfo_functions
from abicheck.type_metadata import FuncProto


def _snap(functions=None, variables=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions or [],
        variables=variables or [],
        types=[],
        enums=[],
        typedefs={},
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _fn(**kwargs) -> Function:
    kwargs.setdefault("name", "log_message")
    kwargs.setdefault("mangled", "log_message")
    kwargs.setdefault("return_type", "void")
    return Function(**kwargs)


def _var(**kwargs) -> Variable:
    kwargs.setdefault("name", "g_state")
    kwargs.setdefault("mangled", "g_state")
    kwargs.setdefault("type", "int")
    return Variable(**kwargs)


# ── Variadic transitions ─────────────────────────────────────────────────────

class TestVariadic:
    def test_added_is_breaking(self):
        r = compare(
            _snap([_fn(is_variadic=False)]),
            _snap([_fn(is_variadic=True)]),
        )
        assert ChangeKind.FUNC_VARIADIC_ADDED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_removed_is_breaking(self):
        r = compare(
            _snap([_fn(is_variadic=True)]),
            _snap([_fn(is_variadic=False)]),
        )
        assert ChangeKind.FUNC_VARIADIC_REMOVED in _kinds(r)

    def test_unknown_side_skipped(self):
        r = compare(
            _snap([_fn(is_variadic=None)]),
            _snap([_fn(is_variadic=True)]),
        )
        assert ChangeKind.FUNC_VARIADIC_ADDED not in _kinds(r)


# ── Contract attributes ──────────────────────────────────────────────────────

class TestContractAttributes:
    def test_attribute_added(self):
        r = compare(
            _snap([_fn(contract_attributes=[])]),
            _snap([_fn(contract_attributes=["nonnull(1)"])]),
        )
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED in _kinds(r)
        change = next(
            c for c in r.changes if c.kind == ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED
        )
        assert "nonnull(1)" in change.description

    def test_attribute_removed(self):
        r = compare(
            _snap([_fn(contract_attributes=["returns_nonnull"])]),
            _snap([_fn(contract_attributes=[])]),
        )
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_REMOVED in _kinds(r)

    def test_uncaptured_side_skipped(self):
        r = compare(
            _snap([_fn(contract_attributes=None)]),
            _snap([_fn(contract_attributes=["noreturn"])]),
        )
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED not in _kinds(r)

    def test_cc_attribute_flip_is_calling_convention_changed(self):
        r = compare(
            _snap([_fn(contract_attributes=["stdcall"])]),
            _snap([_fn(contract_attributes=["cdecl"])]),
        )
        kinds = _kinds(r)
        assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED not in kinds
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_REMOVED not in kinds

    def test_regparm_value_change_is_calling_convention_changed(self):
        r = compare(
            _snap([_fn(contract_attributes=["regparm(2)"])]),
            _snap([_fn(contract_attributes=["regparm(3)"])]),
        )
        assert ChangeKind.CALLING_CONVENTION_CHANGED in _kinds(r)

    def test_identical_attributes_no_finding(self):
        r = compare(
            _snap([_fn(contract_attributes=["noreturn", "nonnull(1)"])]),
            _snap([_fn(contract_attributes=["nonnull(1)", "noreturn"])]),
        )
        kinds = _kinds(r)
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED not in kinds
        assert ChangeKind.FUNC_CONTRACT_ATTRIBUTE_REMOVED not in kinds


# ── Dynamic exception specifications ─────────────────────────────────────────

class TestExceptionSpec:
    def test_spec_changed(self):
        r = compare(
            _snap([_fn(exception_spec="throw()")]),
            _snap([_fn(exception_spec="")]),
        )
        assert ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED in _kinds(r)

    def test_stable_spec_no_finding(self):
        r = compare(
            _snap([_fn(exception_spec="throw(int)")]),
            _snap([_fn(exception_spec="throw(int)")]),
        )
        assert ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED not in _kinds(r)

    def test_uncaptured_side_skipped(self):
        r = compare(
            _snap([_fn(exception_spec=None)]),
            _snap([_fn(exception_spec="throw()")]),
        )
        assert ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED not in _kinds(r)


# ── Variable alignment ───────────────────────────────────────────────────────

class TestVariableAlignment:
    def test_changed_is_breaking(self):
        r = compare(
            _snap(variables=[_var(alignment_bits=512)]),
            _snap(variables=[_var(alignment_bits=64)]),
        )
        assert ChangeKind.VAR_ALIGNMENT_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_uncaptured_side_skipped(self):
        r = compare(
            _snap(variables=[_var(alignment_bits=None)]),
            _snap(variables=[_var(alignment_bits=64)]),
        )
        assert ChangeKind.VAR_ALIGNMENT_CHANGED not in _kinds(r)

    def test_stable_alignment_no_finding(self):
        r = compare(
            _snap(variables=[_var(alignment_bits=64)]),
            _snap(variables=[_var(alignment_bits=64)]),
        )
        assert ChangeKind.VAR_ALIGNMENT_CHANGED not in _kinds(r)

    def test_unknown_type_side_still_checks_alignment_only(self):
        # A stripped side reports type "?" — unknown is not a type change,
        # but a captured alignment drift on the same pair still reports.
        r = compare(
            _snap(variables=[_var(type="?", alignment_bits=512)]),
            _snap(variables=[_var(alignment_bits=64)]),
        )
        kinds = _kinds(r)
        assert ChangeKind.VAR_ALIGNMENT_CHANGED in kinds
        assert ChangeKind.VAR_TYPE_CHANGED not in kinds


# ── vtable index moves ───────────────────────────────────────────────────────

class TestVtableIndexMove:
    def test_slot_move_reports_vtable_changed(self):
        old = _fn(
            name="Widget::draw", mangled="_ZN6Widget4drawEv",
            is_virtual=True, vtable_index=2,
        )
        new = _fn(
            name="Widget::draw", mangled="_ZN6Widget4drawEv",
            is_virtual=True, vtable_index=3,
        )
        r = compare(_snap([old]), _snap([new]))
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(r)

    def test_unknown_index_skipped(self):
        old = _fn(mangled="_ZN6Widget4drawEv", is_virtual=True, vtable_index=None)
        new = _fn(mangled="_ZN6Widget4drawEv", is_virtual=True, vtable_index=3)
        r = compare(_snap([old]), _snap([new]))
        assert ChangeKind.TYPE_VTABLE_CHANGED not in _kinds(r)

    def test_stable_index_no_finding(self):
        old = _fn(mangled="_ZN6Widget4drawEv", is_virtual=True, vtable_index=2)
        new = _fn(mangled="_ZN6Widget4drawEv", is_virtual=True, vtable_index=2)
        r = compare(_snap([old]), _snap([new]))
        assert ChangeKind.TYPE_VTABLE_CHANGED not in _kinds(r)


# ── BTF/CTF function bridge ──────────────────────────────────────────────────

class TestTypeinfoFunctionBridge:
    def test_protos_become_functions(self):
        protos = {
            "frob": FuncProto(name="frob", return_type="int", params=[("a", "int")]),
        }
        funcs = _typeinfo_functions(protos)
        assert len(funcs) == 1
        assert funcs[0].mangled == "frob"
        assert funcs[0].return_type == "int"
        assert funcs[0].params[0].type == "int"
        assert funcs[0].is_extern_c

    def test_param_change_detected_through_bridge(self):
        old = _snap(_typeinfo_functions(
            {"frob": FuncProto(name="frob", return_type="int", params=[("a", "int")])}
        ))
        new = _snap(_typeinfo_functions(
            {"frob": FuncProto(name="frob", return_type="int", params=[("a", "long")])}
        ))
        r = compare(old, new)
        assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(r)

    def test_static_funcs_filtered_in_linkage_aware_blob(self):
        # BTF_KIND_FUNC vlen: 0 = static, 1 = global. A blob that carries
        # non-zero linkages distinguishes them, so file-local helpers must
        # not surface as public ABI functions.
        protos = {
            "helper": FuncProto(name="helper", return_type="void", params=[], linkage=0),
            "api_fn": FuncProto(name="api_fn", return_type="int", params=[], linkage=1),
        }
        funcs = _typeinfo_functions(protos)
        assert [f.name for f in funcs] == ["api_fn"]

    def test_legacy_all_zero_linkage_keeps_everything(self):
        # Legacy BTF encoders wrote linkage 0 for every function; treating
        # that as "all static" would silently drop the whole surface.
        protos = {
            "a_fn": FuncProto(name="a_fn", return_type="void", params=[], linkage=0),
            "b_fn": FuncProto(name="b_fn", return_type="int", params=[], linkage=0),
        }
        assert len(_typeinfo_functions(protos)) == 2

    def test_ctf_protos_without_linkage_kept(self):
        # CTF doesn't encode linkage (None) — always kept, even alongside
        # linkage-aware entries.
        protos = {
            "c_fn": FuncProto(name="c_fn", return_type="void", params=[]),
            "api_fn": FuncProto(name="api_fn", return_type="int", params=[], linkage=1),
        }
        assert [f.name for f in _typeinfo_functions(protos)] == ["api_fn", "c_fn"]


# ── castxml extraction ───────────────────────────────────────────────────────

_CASTXML_DOC = """
<CastXML version="1.0">
  <Function id="_f1" name="logf" returns="_t1" mangled="logf"
            attributes="gnu:nonnull(1) gnu:noreturn other"
            throw="_t1 _t2">
    <Argument name="fmt" type="_t2"/>
    <Ellipsis/>
  </Function>
  <Variable id="_v1" name="g_buf" type="_t1" mangled="g_buf" align="64"/>
  <FundamentalType id="_t1" name="int"/>
  <FundamentalType id="_t2" name="char"/>
</CastXML>
"""


class TestCastxmlExtraction:
    def _parser(self) -> _CastxmlParser:
        return _CastxmlParser(
            fromstring(_CASTXML_DOC), exported_dynamic=set(), exported_static=set()
        )

    def test_variadic_and_attributes_and_throw(self):
        funcs = self._parser().parse_functions()
        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.is_variadic is True
        assert fn.contract_attributes == ["nonnull(1)", "noreturn"]
        assert fn.exception_spec == "throw(int, char)"

    def test_variable_alignment(self):
        variables = self._parser().parse_variables()
        assert len(variables) == 1
        assert variables[0].alignment_bits == 64

    def test_extract_contract_attributes_filters_unknown(self):
        assert _extract_contract_attributes("noexcept final gnu:malloc") == ["malloc"]
        assert _extract_contract_attributes("") == []
        assert _extract_contract_attributes("stdcall") == ["stdcall"]

    def test_empty_throw_attribute_is_empty_spec(self):
        # castxml spells `throw()` as throw="" — distinct from the attribute
        # being absent (no dynamic spec at all).
        doc = """
        <CastXML version="1.0">
          <Function id="_f1" name="nothrow_fn" returns="_t1"
                    mangled="nothrow_fn" throw="">
            <Comment/>
          </Function>
          <FundamentalType id="_t1" name="int"/>
        </CastXML>
        """
        parser = _CastxmlParser(
            fromstring(doc), exported_dynamic=set(), exported_static=set()
        )
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].exception_spec == "throw()"


# ── clang extraction helpers ─────────────────────────────────────────────────

class TestClangExtraction:
    def test_contract_attributes(self):
        node = {
            "inner": [
                {"kind": "NonNullAttr"},
                {"kind": "WarnUnusedResultAttr"},
                {"kind": "ParmVarDecl"},
            ]
        }
        assert _clang_contract_attributes(node) == ["nonnull", "warn_unused_result"]

    def test_exception_spec(self):
        assert _clang_exception_spec(" throw(int, char)") == "throw(int, char)"
        assert _clang_exception_spec(" throw()") == "throw()"
        assert _clang_exception_spec(" const noexcept") == ""

    def test_var_alignment(self):
        node = {
            "inner": [
                {
                    "kind": "AlignedAttr",
                    "inner": [{"kind": "ConstantExpr", "value": "64"}],
                }
            ]
        }
        assert _clang_var_alignment_bits(node) == 512
        assert _clang_var_alignment_bits({"inner": []}) is None

    def test_contract_attributes_skip_non_dict_children(self):
        node = {"inner": ["not-a-dict", {"kind": "NoReturnAttr"}]}
        assert _clang_contract_attributes(node) == ["noreturn"]

    def test_contract_attribute_args_preserved(self):
        # Argument-bearing attributes keep their operands (matching castxml), so
        # nonnull(1) vs nonnull(2) is a detectable change, not a bare `nonnull`.
        def _nonnull(idx: int) -> dict:
            return {
                "kind": "NonNullAttr",
                "inner": [{"kind": "ConstantExpr", "value": idx}],
            }

        assert _clang_contract_attributes({"inner": [_nonnull(1)]}) == ["nonnull(1)"]
        assert _clang_contract_attributes({"inner": [_nonnull(2)]}) == ["nonnull(2)"]

    def test_contract_attribute_multi_arg_and_string(self):
        # format(printf,1,2): archetype string + two indices, in source order.
        node = {
            "inner": [
                {
                    "kind": "FormatAttr",
                    "inner": [
                        {"kind": "StringLiteral", "value": '"printf"'},
                        {"kind": "ConstantExpr", "value": 1},
                        {"kind": "ConstantExpr", "value": 2},
                    ],
                }
            ]
        }
        assert _clang_contract_attributes(node) == ["format(printf,1,2)"]

    def test_contract_attribute_constantexpr_wrapping_not_double_counted(self):
        # clang wraps a literal inside its ConstantExpr with the same value;
        # taking the outer value and not descending avoids regparm(2,2).
        node = {
            "inner": [
                {
                    "kind": "RegparmAttr",
                    "inner": [
                        {
                            "kind": "ConstantExpr",
                            "value": 2,
                            "inner": [{"kind": "IntegerLiteral", "value": 2}],
                        }
                    ],
                }
            ]
        }
        assert _clang_contract_attributes(node) == ["regparm(2)"]

    def test_contract_attribute_no_args_stays_bare(self):
        # Argless attributes (noreturn) render as bare tokens, matching castxml.
        node = {"inner": [{"kind": "NoReturnAttr", "inner": []}]}
        assert _clang_contract_attributes(node) == ["noreturn"]

    def test_contract_attribute_arg_walk_skips_noise(self):
        # Exercises the arg walk's three fall-through paths: a non-dict child is
        # skipped, a bool value is not an ABI arg, and a value-less wrapper is
        # descended into to reach the real ConstantExpr operand.
        node = {
            "inner": [
                {
                    "kind": "AllocSizeAttr",
                    "inner": [
                        "not-a-dict",
                        {"kind": "CXXBoolLiteralExpr", "value": True, "inner": []},
                        {
                            "kind": "ImplicitCastExpr",
                            "inner": [{"kind": "ConstantExpr", "value": 3}],
                        },
                    ],
                }
            ]
        }
        assert _clang_contract_attributes(node) == ["alloc_size(3)"]

    def test_var_alignment_integer_value(self):
        # clang may emit the evaluated constant as an int, not a string.
        node = {
            "inner": [
                {"kind": "AlignedAttr", "inner": [{"kind": "ConstantExpr", "value": 32}]}
            ]
        }
        assert _clang_var_alignment_bits(node) == 256

    def test_var_alignment_ignores_other_attributes(self):
        node = {"inner": [{"kind": "NoReturnAttr"}, "junk",
                          {"kind": "AlignedAttr", "inner": []}]}
        assert _clang_var_alignment_bits(node) is None

    def test_var_alignment_nested_and_junk_entries(self):
        # The value can sit a level deeper (e.g. inside an IntegerLiteral);
        # non-dict siblings and non-numeric values must be skipped.
        node = {
            "inner": [
                {
                    "kind": "AlignedAttr",
                    "inner": [
                        {"kind": "ConstantExpr", "value": "abc",
                         "inner": [{"kind": "IntegerLiteral", "value": "16"}]},
                        "junk",
                    ],
                }
            ]
        }
        assert _clang_var_alignment_bits(node) == 128
