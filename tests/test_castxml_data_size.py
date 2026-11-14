"""Unit tests for the CastXML Itanium data-size derivation (data_size.py).

Hand-built CastXML documents; the expected sizes follow the Itanium C++ ABI
rules the compilers apply (pinned against g++/clang++ by
``test_castxml_data_size_integration.py``), computed here by hand rather
than by the helper under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.data_size import data_size_bits, pod_for_layout

_TYPES = """
  <FundamentalType id="_int" name="int" size="32" align="32"/>
  <FundamentalType id="_char" name="char" size="8" align="8"/>
  <PointerType id="_ptr" type="_char" size="64" align="64"/>
  <ReferenceType id="_ref" type="_int" size="64" align="64"/>
"""
#: An Itanium mangling: the target castxml parsed for (see itanium_abi_target).
_ITANIUM_FN = (
    '<Function id="_fn" name="f" returns="_int" context="_1" mangled="_Z1fv"/>'
)


def _ctx(
    body: str,
    tmp_path: Path,
    source: str = "",
    target: str = _ITANIUM_FN,
    triple: str | None = "x86_64-pc-linux-gnu",
) -> CastxmlParserContext:
    hdr = tmp_path / "h.hpp"
    hdr.write_text(source or "\n" * 40)
    xml = (
        f'<CastXML format="1.4.0"><File id="f1" name="{hdr}"/>{_TYPES}{target}'
        f"{body}</CastXML>"
    )
    ctx = CastxmlParserContext(fromstring(xml), set(), set())
    ctx.build_id_map()
    ctx.target_triple = triple
    return ctx


def _rec(ctx: CastxmlParserContext, name: str) -> object:
    return next(el for el in ctx.record_els if el.get("name") == name)


def _implicit(rid: str) -> str:
    """The implicit members CastXML emits for a record without user ctors."""
    return (
        f'<Constructor id="{rid}_dc" name="S" context="{rid}" access="public" '
        'file="f1" line="1" inline="1" artificial="1"/>'
    )


def _struct(rid: str, name: str, size: int, members: str, extra: str = "") -> str:
    ids = " ".join(
        part.split('"')[1] for part in members.split("<")[1:] if 'id="' in part
    )
    return (
        f'<Struct id="{rid}" name="{name}" context="_1" file="f1" line="1" '
        f'members="{ids}" size="{size}" align="32"{extra}>'
        + "".join(
            m if m.startswith("<Base") else ""
            for m in ["<" + p for p in members.split("<")[1:]]
        )
        + "</Struct>"
        + "".join("<" + p for p in members.split("<")[1:] if not p.startswith("Base"))
    )


def _field(
    fid: str, ctx: str, typ: str, off: int, extra: str = "", line: int = 2
) -> str:
    return (
        f'<Field id="{fid}" name="{fid}" type="{typ}" context="{ctx}" '
        f'access="public" file="f1" line="{line}" offset="{off}"{extra}/>'
    )


def _int_char(rid: str) -> str:
    return _field(f"{rid}a", rid, "_int", 0) + _field(f"{rid}c", rid, "_char", 32)


def test_pod_keeps_sizeof(tmp_path: Path) -> None:
    ctx = _ctx(_struct("_s", "S", 64, _implicit("_s") + _int_char("_s")), tmp_path)
    assert pod_for_layout(ctx, _rec(ctx, "S")) is True
    assert data_size_bits(ctx, _rec(ctx, "S")) == 64


@pytest.mark.parametrize(
    "member",
    [
        # user-provided (out-of-line) constructor
        '<Constructor id="_k" name="S" context="_s" access="public" file="f1" line="3"/>',
        # user-provided destructor
        '<Destructor id="_k" name="S" context="_s" access="public" file="f1" line="3"/>'
        + "IMPLICIT",
        # virtual method -> dsize includes the vptr; tested separately
    ],
)
def test_user_provided_special_member_exposes_tail_padding(
    tmp_path: Path, member: str
) -> None:
    member = member.replace("IMPLICIT", _implicit("_s"))
    ctx = _ctx(_struct("_s", "S", 64, member + _int_char("_s")), tmp_path)
    assert pod_for_layout(ctx, _rec(ctx, "S")) is False
    assert data_size_bits(ctx, _rec(ctx, "S")) == 40  # int a; char c -> 5 bytes


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (' access="private"', False),  # access mix
        (' init="1"', False),  # default member initializer
    ],
)
def test_field_facts_decide_pod(tmp_path: Path, extra: str, expected: bool) -> None:
    fields = _field("_sa", "_s", "_int", 0).replace(
        ' access="public"', extra if "access" in extra else ' access="public"' + extra
    )
    fields += _field("_sc", "_s", "_char", 32)
    ctx = _ctx(_struct("_s", "S", 64, _implicit("_s") + fields), tmp_path)
    assert pod_for_layout(ctx, _rec(ctx, "S")) is expected
    assert data_size_bits(ctx, _rec(ctx, "S")) == 40


def test_reference_member_is_not_pod(tmp_path: Path) -> None:
    fields = _field("_sr", "_s", "_ref", 0) + _field("_sc", "_s", "_char", 64)
    ctx = _ctx(_struct("_s", "S", 128, _implicit("_s") + fields), tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "S")) == 72


def test_bitfield_rounds_up_to_a_byte(tmp_path: Path) -> None:
    ctor = '<Constructor id="_k" name="S" context="_s" access="public" file="f1" line="3"/>'
    fields = _field("_sc", "_s", "_char", 0) + _field(
        "_sb", "_s", "_int", 8, ' bits="11"'
    )
    ctx = _ctx(_struct("_s", "S", 32, ctor + fields), tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "S")) == 24  # 8 + 11 bits -> 3 bytes


def test_base_contributes_its_own_data_size(tmp_path: Path) -> None:
    ctor = '<Constructor id="_k" name="B" context="_b" access="public" file="f1" line="3"/>'
    base = _struct("_b", "B", 64, ctor + _int_char("_b"))
    derived = _struct(
        "_d",
        "D",
        64,
        '<Base type="_b" access="public" virtual="0" offset="0"/>'
        + _implicit("_d")
        + _field("_dx", "_d", "_char", 40),
    )
    ctx = _ctx(base + derived, tmp_path)
    assert data_size_bits(ctx, _rec(ctx, "D")) == 48  # x reused B's padding


@pytest.mark.parametrize(
    "triple", ["x86_64-pc-linux-gnu", "arm64-apple-darwin23", "x86_64-w64-mingw32"]
)
def test_empty_base_contributes_nothing(tmp_path: Path, triple: str) -> None:
    # An empty class holds no data even when POD (C++03 POD here; every empty
    # class is C++11 POD on Apple arm64): ``struct X : E { char z; }`` puts z
    # at offset 0 on every Itanium target.
    empty = _struct("_e", "E", 8, _implicit("_e"))
    derived = _struct(
        "_d",
        "D",
        8,
        '<Base type="_e" access="public" virtual="0" offset="0"/>' + _implicit("_d"),
    )
    ctx = _ctx(empty + derived, tmp_path, triple=triple)
    assert data_size_bits(ctx, _rec(ctx, "E")) == 0
    assert data_size_bits(ctx, _rec(ctx, "D")) == 0


def test_own_vptr_counts(tmp_path: Path) -> None:
    virt = (
        '<Method id="_m" name="f" context="_s" access="public" file="f1" line="3" '
        'virtual="1"/>'
    )
    ctx = _ctx(
        _struct(
            "_s", "S", 128, _implicit("_s") + virt + _field("_sc", "_s", "_char", 64)
        ),
        tmp_path,
    )
    assert data_size_bits(ctx, _rec(ctx, "S")) == 72


@pytest.mark.parametrize(
    "case",
    ["virtual_base", "defaulted_ctor", "no_default_ctor", "incomplete", "nua"],
)
def test_unknown_fact_leaves_data_size_unknown(tmp_path: Path, case: str) -> None:
    ctor = '<Constructor id="_k" name="S" context="_s" access="public" file="f1" line="3"/>'
    source = "\n" * 40
    members = ctor + _int_char("_s")
    extra = ""
    body_prefix = ""
    if case == "virtual_base":
        body_prefix = _struct(
            "_b", "B", 32, _implicit("_b") + _field("_bx", "_b", "_int", 0)
        )
        members = '<Base type="_b" access="public" virtual="1"/>' + members
    elif case == "defaulted_ctor":
        members = members.replace('line="3"/>', 'line="3" inline="1"/>')
        source = "\n\nS() = default;\n" + "\n" * 30
    elif case == "no_default_ctor":
        members = _int_char("_s")  # a deleted ctor is not emitted
    elif case == "incomplete":
        extra = ' incomplete="1"'
    elif case == "nua":
        body_prefix = _struct("_e", "E", 8, _implicit("_e"))
        members += _field("_se", "_s", "_e", 40, line=4)
        source = "\n\n\n[[no_unique_address]] E e;\n" + "\n" * 30
    ctx = _ctx(body_prefix + _struct("_s", "S", 64, members, extra), tmp_path, source)
    assert data_size_bits(ctx, _rec(ctx, "S")) is None


def test_nua_irrelevant_when_dominated(tmp_path: Path) -> None:
    """A possibly-[[no_unique_address]] empty member followed by real data
    ends no later than that data either way."""
    ctor = '<Constructor id="_k" name="S" context="_s" access="public" file="f1" line="3"/>'
    empty = _struct("_e", "E", 8, _implicit("_e"))
    members = (
        ctor
        + _field("_se", "_s", "_e", 0, line=4)
        + _field("_sa", "_s", "_int", 32, line=5)
    )
    source = "\n\n\n[[no_unique_address]] E e;\nint a;\n" + "\n" * 30
    ctx = _ctx(empty + _struct("_s", "S", 64, members), tmp_path, source)
    assert data_size_bits(ctx, _rec(ctx, "S")) == 64


def test_c_document_is_always_pod(tmp_path: Path) -> None:
    xml_body = (
        '<Struct id="_s" name="S" context="_1" file="f1" line="1" members="_sa _sc" '
        'size="64" align="32"/>' + _int_char("_s")
    )
    ctx = _ctx(xml_body.replace(_TYPES, ""), tmp_path)
    # _TYPES holds a ReferenceType (C++ only): drop it to model a C document.
    ctx.is_cxx_document = False
    assert data_size_bits(ctx, _rec(ctx, "S")) == 64
