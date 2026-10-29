"""Calling conventions of function types, without a toolchain (default lane).

The integration lanes compile real libraries; these pin the pure logic the
header and DWARF readers use, so the default lane covers it: the clang
qualType suffix that names a convention, the castxml declaration keys that
reach a FunctionType node, the DWARF subroutine spelling, and the
generation-drift rule that demotes a respelling across an older baseline.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from abicheck.dwarf_utils import (
    DW_CC_TYPE_CONVENTIONS,
    subroutine_calling_convention,
    subroutine_type_spelling,
)
from abicheck.extract.headers.castxml.function_type_cc import (
    FUNCTION_CC_ATTR,
    castxml_function_type_keys,
    clang_function_type_cc,
    load_function_type_cc,
    trailing_calling_convention,
    with_function_type_cc,
)
from abicheck.policy.generation_drift_demotion import is_spelling_only_drift

# ── clang qualType suffix ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("qual_type", "expected"),
    [
        ("void (*)(int) __attribute__((ms_abi))", "ms_abi"),
        ("void (*)(int) __attribute__((sysv_abi))", "sysv_abi"),
        ("void (*)(int) __attribute__((regparm(2)))", "regparm(2)"),
        ("void (*)(int) __attribute__((vectorcall))", "vectorcall"),
        # Not a calling convention: a vector size, or no suffix at all.
        ("int __attribute__((vector_size(16)))", None),
        ("void (*)(int)", None),
        ("int", None),
    ],
)
def test_trailing_calling_convention(qual_type: str, expected: str | None) -> None:
    assert trailing_calling_convention(qual_type) == expected


# ── castxml FunctionType keys and the clang join ──────────────────────────

_XML = """<CastXML>
  <Function id="_1" name="f" mangled="_Z1fPFviE" returns="_2" context="_0">
    <Argument type="_3"/>
  </Function>
  <FunctionType id="_4" returns="_2"><Argument type="_5"/></FunctionType>
  <PointerType id="_3" type="_4"/>
  <FundamentalType id="_2" name="void"/>
  <FundamentalType id="_5" name="int"/>
  <Struct id="_9" name="S" context="_0"/>
  <Field id="_10" name="m" type="_6" context="_9"/>
  <PointerType id="_6" type="_7"/>
  <FunctionType id="_7" returns="_2"><Argument type="_5"/></FunctionType>
  <Typedef id="_11" name="cb_t" type="_12"/>
  <PointerType id="_12" type="_13"/>
  <FunctionType id="_13" returns="_2"><Argument type="_5"/></FunctionType>
  <Namespace id="_0" name="::"/>
</CastXML>"""


def test_castxml_keys_reach_the_function_type_through_declarators() -> None:
    root = ET.fromstring(_XML)
    keys = castxml_function_type_keys(root)
    assert keys == {
        "_4": "param:_Z1fPFviE#0",
        "_7": "field:S::m",
        "_13": "typedef:cb_t",
    }


def test_clang_join_keys_each_declaration_with_its_convention() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "f",
                "mangledName": "_Z1fPFviE",
                "type": {"qualType": "void (void (*)(int) __attribute__((ms_abi)))"},
                "inner": [
                    {
                        "kind": "ParmVarDecl",
                        "name": "p",
                        "type": {"qualType": "void (*)(int) __attribute__((ms_abi))"},
                    }
                ],
            },
            {
                "kind": "RecordDecl",
                "name": "S",
                "inner": [
                    {
                        "kind": "FieldDecl",
                        "name": "m",
                        "type": {"qualType": "void (*)(int) __attribute__((sysv_abi))"},
                    }
                ],
            },
            {
                "kind": "TypedefDecl",
                "name": "cb_t",
                "type": {"qualType": "void (*)(int)"},
            },
        ],
    }
    assert clang_function_type_cc(ast) == {
        "param:_Z1fPFviE#0": "ms_abi",
        "field:S::m": "sysv_abi",
    }


def test_carried_map_round_trips_and_leaves_the_tree_unwritten() -> None:
    root = ET.fromstring(_XML)
    carried = with_function_type_cc(root, {"_4": "ms_abi"})
    assert json.loads(carried.get(FUNCTION_CC_ATTR, "{}")) == {"_4": "ms_abi"}
    assert load_function_type_cc(carried) == {"_4": "ms_abi"}
    assert root.get(FUNCTION_CC_ATTR) is None
    assert list(carried) == list(root)


# ── DWARF subroutine spelling ─────────────────────────────────────────────


def _die(attributes: dict[str, int] | None = None) -> SimpleNamespace:
    attrs = {k: SimpleNamespace(value=v) for k, v in (attributes or {}).items()}
    return SimpleNamespace(attributes=attrs)


def test_clang_convention_codes_name_the_source_convention() -> None:
    assert DW_CC_TYPE_CONVENTIONS[0xC1] == "ms_abi"
    assert (
        subroutine_calling_convention(_die({"DW_AT_calling_convention": 0xC1}))
        == "ms_abi"
    )
    assert subroutine_calling_convention(_die()) is None
    assert (
        subroutine_calling_convention(_die({"DW_AT_calling_convention": 0x01})) is None
    )


@pytest.mark.parametrize(
    ("return_name", "params", "varargs", "pointer", "attrs", "expected"),
    [
        ("void", ["int"], False, True, {}, "void (*)(int)"),
        ("int", [], False, True, {}, "int (*)(void)"),
        ("int", ["int", "void *"], True, False, {}, "int (int, void *, ...)"),
        (
            "void",
            ["int"],
            False,
            True,
            {"DW_AT_calling_convention": 0xC1},
            "void (*)(int) __attribute__((ms_abi))",
        ),
        (
            "long",
            ["int"],
            False,
            False,
            {"DW_AT_calling_convention": 0xC0},
            "long (int) __attribute__((vectorcall))",
        ),
    ],
)
def test_subroutine_spelling_matches_the_header_shape(
    return_name: str,
    params: list[str],
    varargs: bool,
    pointer: bool,
    attrs: dict[str, int],
    expected: str,
) -> None:
    die = _die(attrs)
    assert (
        subroutine_type_spelling(
            die, return_name, params, varargs=varargs, pointer=pointer
        )
        == expected
    )


# ── generation drift: an older baseline's respelling is drift ─────────────


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # A generation-28 baseline lacks the convention castxml now joins.
        ("void (*)(int)", "void (*)(int) __attribute__((ms_abi))"),
        # ... and the reverse (the newer side lost it).
        ("void (*)(int) __attribute__((ms_abi))", "void (*)(int)"),
        # A DWARF stored tag against a fresh declarator with the same wrappers.
        ("fn(...)*", "void (*)(int)"),
        ("fn(...)*", "void (*)(int) __attribute__((ms_abi))"),
    ],
)
def test_older_baseline_respelling_is_demoted(old: str, new: str) -> None:
    assert is_spelling_only_drift(old, new, frozenset())


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # A real signature change is not drift.
        ("void (*)(int)", "void (*)(long)"),
        # Two different conventions are a real change.
        (
            "void (*)(int) __attribute__((sysv_abi))",
            "void (*)(int) __attribute__((ms_abi))",
        ),
        # A pointer became a reference: the wrappers differ.
        ("fn(...)*", "void (&)(int)"),
    ],
)
def test_real_callback_change_is_not_demoted(old: str, new: str) -> None:
    assert not is_spelling_only_drift(old, new, frozenset())
